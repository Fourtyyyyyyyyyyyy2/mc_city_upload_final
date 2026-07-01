"""叙事图层：公会装饰图层（新卡 8 D 方案）。

公开 API:
    GUILD_DECORATIONS       —— 公会 → 装饰方块 id 表
    place_guild_decorations(metas, height_map, ctx, center_x, center_z)
        主入口，提交 HTTP，返回成功放置块数
    build_decor_payloads(metas, height_map, ctx, center_x, center_z)
        → (payloads, skipped)；不调 HTTP，dry-run 用

设计：
    - 每栋建筑（meta.guild 非空时）在 origin 朝外方向偏 1 格放 1 个公会标志
      方块。朝外 = 远离城市中心，玩家从城外走过来先看见装饰，再看见建筑墙，
      再看见挂墙告示牌（在 origin 朝心 1 格）。
    - 4 公会各一个具识别度的方块：
        scholars  → chiseled_bookshelf  (学者藏书)
        engineers → anvil               (工匠锻铁)
        merchants → barrel              (商人桶装)
        adventurers → grindstone        (勇者磨刀)
    - 方块朝向（如果支持 facing/face）：朝心，与告示牌同向。
    - 单块失败不影响其它，与 spec 1.5 CONSTRAINTS 一致。
    - 不动建筑主体 npy；与 paste_volume / terraform 路径正交。
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from ..mc.placement import set_blocks_batch
from ..scan.coord_frame import ScanContext
from .types import BuildingMeta

DECOR_BATCH_SIZE = 256

# 公会 → 装饰方块。方块 id 末尾可以含 [属性=值]，朝向运行时拼装。
# 每个 guild 用最有识别度的单块装饰（避免视觉杂乱 / 抢主体建筑戏）。
GUILD_DECORATIONS: dict[str, str] = {
    "soul_scholars":  "minecraft:chiseled_bookshelf",
    "soul_engineers": "minecraft:anvil",
    "merchants":      "minecraft:barrel",
    "adventurers":    "minecraft:grindstone",
}

# 哪些方块支持 facing 属性（4 朝向）。其它（如 barrel）默认朝上无 facing。
# chiseled_bookshelf 的 facing 是它正面朝向；anvil/grindstone 是 facing 决定旋转轴。
_DECOR_FACING_KIND: dict[str, str] = {
    "minecraft:chiseled_bookshelf": "facing",   # facing=north/south/east/west
    "minecraft:anvil":              "facing",   # anvil 朝向 = 砧面所对
    "minecraft:grindstone":         "facing",   # 配合 face=floor 默认
}


# ── 公共入口 ──────────────────────────────────────────────────────
def place_guild_decorations(metas: list[BuildingMeta],
                            height_map: np.ndarray,
                            ctx: ScanContext,
                            center_x: int,
                            center_z: int,
                            codec=None) -> int:
    """每栋建筑朝外偏 1 格放公会标志方块。返回成功放置数。

    codec 留兼容签名（装饰不入 scan_volume）。
    """
    _ = codec
    payloads, skipped = build_decor_payloads(
        metas, height_map, ctx, center_x, center_z)
    if skipped:
        print(f"  ⚠️ 跳过 {skipped} 块公会装饰（无 guild / 越界 / sentinel）")
    if not payloads:
        print("  没有可放置的公会装饰")
        return 0

    success = 0
    for i in range(0, len(payloads), DECOR_BATCH_SIZE):
        batch = payloads[i:i + DECOR_BATCH_SIZE]
        if set_blocks_batch(batch):
            success += len(batch)
        else:
            print(f"  ⚠️ 公会装饰批次写入失败（{len(batch)} 块），继续下一批")
    return success


# ── dry-run 友好的核心 ────────────────────────────────────────────
def build_decor_payloads(metas: list[BuildingMeta],
                         height_map: np.ndarray,
                         ctx: ScanContext,
                         center_x: int,
                         center_z: int
                         ) -> tuple[list[dict], int]:
    """计算每栋建筑的装饰 payload。不调 HTTP。

    Returns (payloads, skipped)。payloads 顺序与 metas 同序（跳过的不占位）。
    """
    NZ, NX = height_map.shape
    payloads: list[dict] = []
    skipped = 0

    for meta in metas:
        guild = meta.guild
        if not guild or guild not in GUILD_DECORATIONS:
            skipped += 1
            continue

        bx, _by, bz = meta.origin
        dx, dz = _outward_offset_unit(bx, bz, center_x, center_z)
        if dx == 0 and dz == 0:
            # 建筑恰好在中心，没有"外"方向；放朝南
            dx, dz = 0, 1
        decor_x = bx + dx
        decor_z = bz + dz

        xs, zs = ctx.w2s(decor_x, decor_z)
        if not (0 <= xs < NX and 0 <= zs < NZ):
            skipped += 1
            continue
        ground_y = int(height_map[zs, xs])
        if ground_y <= ctx.min_y:
            skipped += 1
            continue

        block_base = GUILD_DECORATIONS[guild]
        # facing 朝心（与门口告示牌同向，玩家正面读到）
        facing = _facing_toward_center(decor_x, decor_z, center_x, center_z)
        block_id = _apply_facing(block_base, facing)

        payloads.append({
            "x": int(decor_x), "y": int(ground_y + 1), "z": int(decor_z),
            "id": block_id,
        })

    return payloads, skipped


# ── helpers ───────────────────────────────────────────────────────
def _outward_offset_unit(bx: int, bz: int,
                         cx: int, cz: int) -> tuple[int, int]:
    """从建筑朝远离中心方向的 chebyshev 单位向量（4 cardinal）。"""
    dx = bx - cx
    dz = bz - cz
    if abs(dx) >= abs(dz):
        return (1 if dx >= 0 else -1, 0)
    return (0, 1 if dz >= 0 else -1)


def _facing_toward_center(wx: int, wz: int,
                          cx: int, cz: int) -> str:
    """4 正方向中朝中心最近的一个。同 signs._door_facing_and_offset 内部逻辑。"""
    dx = cx - wx
    dz = cz - wz
    if abs(dx) >= abs(dz):
        return "east" if dx >= 0 else "west"
    return "south" if dz >= 0 else "north"


def _apply_facing(block_id: str, facing: str) -> str:
    """如果方块支持 facing 属性，把 [facing=...] 拼到 id 末尾。

    简单地把所有支持的方块统一加 facing；不需要 facing 的方块（barrel/composter
    等）直接返回原 id。
    """
    kind = _DECOR_FACING_KIND.get(block_id)
    if kind == "facing":
        return f"{block_id}[facing={facing}]"
    return block_id


__all__ = [
    "GUILD_DECORATIONS",
    "place_guild_decorations", "build_decor_payloads",
]
