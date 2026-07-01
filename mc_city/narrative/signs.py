"""叙事图层：在建筑门口挂墙告示牌（1.20+ front_text NBT 语法）。

入口：
    place_door_signs(metas, height_map, ctx, center_x, center_z) -> int

规则：
- 朝向：告示牌正面朝城市中心（即玩家从灵魂树/市心走来时看到正面）。
- 位置：建筑 origin 朝中心方向偏移 1 格，y = height_map 取值 + 1。
- 文本：4 行，建筑名 / 一句话角色名 / 一句话描述 / 灵历+公会徽号；
  每行硬截至 15 字符（中文也按 1 计）。
- 容错：height_map 给出 sentinel（<= ctx.min_y）的列跳过 + ⚠️；
  HTTP 失败也只打印，不抛。
"""
import json
from typing import Iterable, Optional

import numpy as np

from ..mc.placement import set_blocks_batch
from ..scan.coord_frame import ScanContext
from .types import BuildingMeta

# 告示牌每行最多字符（MC 牌面约束；英文较窄，放宽到 18）。
SIGN_LINE_LIMIT = 18

# 单批方块上限，约束要 < 1024。
SIGN_BATCH_SIZE = 256

# role → (短角色名, 一句话描述)。两行都会经过 15 字符截断。
# 固定名建筑（4 主殿/exchange/city_hall）刻意用"别号"，避免 L1=L2 重复。
_ROLE_TAGLINE: dict[str, tuple[str, str]] = {
    "soul_academy_main":   ("Grand Hall",     "Seat of Learning"),
    "soul_engineers_main": ("The Foundry",    "Stone into Tools"),
    "merchants_main":      ("Market Heart",   "Where Roads Meet"),
    "adventurers_main":    ("Expedition Hall", "Heroes Enlist"),
    "soul_core_exchange":  ("The Exchange",   "Trade for Soul"),
    "city_hall":           ("Council Hall",   "Of the People"),
    "guild_branch":        ("Guild Branch",   "A Hall of Study"),
    "guild_workshop":      ("Workshop",       "Fires Never Die"),
    "shop":                ("Market Shop",    "Fair Trade"),
    "inn":                 ("Traveler's Inn", "Rest and Ale"),
    "house":               ("Home",           "A Hearth Here"),
    "warehouse":           ("Storehouse",     "Sealed Goods"),
    "watchtower":          ("Watchtower",     "Eyes on the Wild"),
    "placeholder":         ("Unmarked",       "Unknown"),
}

# 公会徽号（ASCII，避免 MC 字体不支持 emoji）
_GUILD_SIGIL: dict[str, str] = {
    "soul_scholars":  "[SCH]",
    "soul_engineers": "[ENG]",
    "merchants":      "[MER]",
    "adventurers":    "[ADV]",
}


# ── 公共入口 ──────────────────────────────────────────────────────────
def place_door_signs(metas: list[BuildingMeta],
                     height_map: np.ndarray,
                     ctx: ScanContext,
                     center_x: int,
                     center_z: int,
                     codec=None) -> int:
    """在每栋建筑朝心一面挂一块告示牌。返回成功放置的数量。

    codec 参数保留兼容签名，本函数不需要（告示牌不入 scan_volume）。
    """
    _ = codec
    payloads, skipped = build_sign_payloads(metas, height_map, ctx,
                                            center_x, center_z)
    if skipped:
        print(f"  ⚠️ 跳过 {skipped} 块告示牌（地形挡住或超出扫描范围）")

    if not payloads:
        print("  没有可放置的告示牌")
        return 0

    success = 0
    for batch in _chunked(payloads, SIGN_BATCH_SIZE):
        if set_blocks_batch(batch):
            success += len(batch)
        else:
            print(f"  ⚠️ 告示牌批次写入失败（{len(batch)} 块），继续下一批")
    return success


# ── dry-run 可见的核心：构造每块告示牌的 PUT payload ────────────────
def build_sign_payloads(metas: list[BuildingMeta],
                        height_map: np.ndarray,
                        ctx: ScanContext,
                        center_x: int,
                        center_z: int) -> tuple[list[dict], int]:
    """把 metas 转换成 set_blocks_batch 接受的 dict 列表。

    返回 (payloads, skipped_count)。不调 HTTP，方便单测/dry-run。
    """
    NZ, NX = height_map.shape
    payloads: list[dict] = []
    skipped = 0

    for meta in metas:
        bx, _by, bz = meta.origin
        facing, sx, sz = _door_facing_and_offset(bx, bz, center_x, center_z)

        xs, zs = ctx.w2s(sx, sz)
        if not (0 <= xs < NX and 0 <= zs < NZ):
            skipped += 1
            continue

        ground_y = int(height_map[zs, xs])
        if ground_y <= ctx.min_y:  # sentinel 列：无效地表
            skipped += 1
            continue

        sign_y = ground_y + 1
        lines = build_sign_lines(meta)
        block_id = _wall_sign_block_id(facing, lines)
        payloads.append({"x": int(sx), "y": int(sign_y), "z": int(sz),
                         "id": block_id})

    return payloads, skipped


# ── 文本/朝向/SNBT 工具 ────────────────────────────────────────────
def build_sign_lines(meta: BuildingMeta) -> tuple[str, str, str, str]:
    """组合 4 行文本（已截断到 15 字符）。"""
    short_role, tagline = _ROLE_TAGLINE.get(meta.role, _ROLE_TAGLINE["placeholder"])
    year_text = _format_year(meta.founder_year)
    sigil = _GUILD_SIGIL.get(meta.guild or "", "")
    # L4：灵历年 + 公会徽号（[SCH] 已足够表公会，不再叠公会全名，避免截断）。
    line4 = year_text if not sigil else f"{year_text} {sigil}"

    return (
        _truncate(meta.name, SIGN_LINE_LIMIT),
        _truncate(short_role, SIGN_LINE_LIMIT),
        _truncate(tagline, SIGN_LINE_LIMIT),
        _truncate(line4, SIGN_LINE_LIMIT),
    )


def _door_facing_and_offset(bx: int, bz: int,
                            center_x: int, center_z: int
                            ) -> tuple[str, int, int]:
    """从建筑 origin 计算告示牌 facing 与世界坐标偏移。

    告示牌正面朝向城市中心，挂在 origin 朝心 1 格处。
    返回 (facing, sign_x, sign_z)。
    """
    dx = center_x - bx
    dz = center_z - bz
    if abs(dx) >= abs(dz):
        if dx >= 0:
            return "east", bx + 1, bz
        return "west", bx - 1, bz
    if dz >= 0:
        return "south", bx, bz + 1
    return "north", bx, bz - 1


def _wall_sign_block_id(facing: str, lines: tuple[str, str, str, str]) -> str:
    """拼 oak_wall_sign 的 block id（含 facing + front_text NBT）。"""
    msgs = ",".join(_line_to_snbt_json(line) for line in lines)
    return (f"minecraft:oak_wall_sign[facing={facing}]"
            f"{{front_text:{{messages:[{msgs}]}}}}")


def _line_to_snbt_json(text: str) -> str:
    """SNBT 里 messages 元素须是被引号包裹的 JSON 字符串。

    用 json.dumps 拿到 JSON 字符串（含外层 "），再外面加单引号包成 SNBT 字面量。
    示例：text="灵学者主殿" → '"灵学者主殿"'
    """
    json_str = json.dumps(text, ensure_ascii=False)  # 已含外层 "
    # SNBT 允许单引号字符串；内层 JSON 含双引号不冲突。
    # 若 json 里出现单引号则 escape。
    if "'" in json_str:
        json_str = json_str.replace("'", "\\'")
    return "'" + json_str + "'"


def _truncate(text: str, n: int) -> str:
    return text if len(text) <= n else text[:n]


def _format_year(year: int) -> str:
    """灵历 → Soul Era（SE）。负数 = Before Soul Era（BSE）。"""
    if year == 0:
        return "Soul Era 1"
    if year > 0:
        return f"Soul Era {year}"
    return f"{-year} BSE"


def _chunked(seq: list, n: int) -> Iterable[list]:
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


__all__ = ["place_door_signs", "build_sign_payloads", "build_sign_lines"]
