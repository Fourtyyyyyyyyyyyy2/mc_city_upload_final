"""单栋建筑 terraforming（Priority 0 卡 5）。

每栋建筑独立处理：算出一个能在 ±max_cut/max_fill 范围内搞定的 base_y，
生成 fill_blocks（垫土）和 cut_blocks（凿空气）。HTTP 兜底遵循
UPGRADE_FROM_LEGACY.md §5：失败必须回滚内存状态。

公开 API：
    terraform_for_building(footprint, height_map, features, ctx, ...)
        → TerraformResult（不调 HTTP，纯计算）
    apply_terraform(result, ctx, scan_volume, height_map, codec)
        → bool；提交 HTTP，成功才更新内存
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from ..config import (
    FOUNDATION_STRATEGY,
    PLATFORM_CLEARANCE,
    SEA_LEVEL,
    TERRAFORM_DEFAULT_STRATEGY,
    TERRAFORM_MAX_CUT,
    TERRAFORM_MAX_FILL,
)
from ..mc.codec import BlockCodec
from ..mc.placement import set_blocks_batch
from ..scan.coord_frame import ScanContext


# (world_x, world_y, world_z, block_id_str)
FillBlock = tuple
# (world_x, world_y, world_z) — set to air
CutBlock = tuple


@dataclass
class TerraformResult:
    """单栋 terraforming 计算结果。

    fill_blocks / cut_blocks 都是世界坐标，可以直接喂给 set_blocks_batch
    （fill 走 {"x","y","z","id"} dict 格式；本结构里是 4-tuple 节省内存）。
    """
    success: bool
    base_y: int
    fill_blocks: list = field(default_factory=list)
    cut_blocks: list = field(default_factory=list)
    cost: int = 0
    reason: str = ""

    def __post_init__(self):
        if not self.cost:
            self.cost = len(self.fill_blocks) + len(self.cut_blocks)


# ─────────────────────────────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────────────────────────────

def terraform_for_building(footprint_xz: tuple,
                           height_map: np.ndarray,
                           features,
                           ctx: ScanContext,
                           terrain_map: Optional[np.ndarray] = None,
                           max_cut: int = TERRAFORM_MAX_CUT,
                           max_fill: int = TERRAFORM_MAX_FILL,
                           target_strategy: str = TERRAFORM_DEFAULT_STRATEGY
                           ) -> TerraformResult:
    """计算一栋建筑所需的 terraforming（纯计算，不调 HTTP）。

    Args:
        footprint_xz:   (sx0, sz0, sx1, sz1) 闭区间，scan 坐标。
                        注意：和 placement.py 里的 (sx0, sx1, sz0, sz1) 顺序
                        不同——这里用 (x0, z0, x1, z1)（与任务卡一致）。
        height_map:     (NZ, NX) int32。读这里得 footprint 内地表高度。
        features:       TerrainFeatures。用于 sentinel 检测和（未来扩展用）。
        ctx:            ScanContext。用于把 scan 坐标转世界坐标。
        terrain_map:    Optional 地形分类图。给定则按周围地形选材质；否则用 dirt/grass。
        max_cut/max_fill: 单格最大 cut/fill 量，超过 → success=False, reason="too_steep"。
        target_strategy: "p70"/"median"/"min"/"max"。

    Returns:
        TerraformResult。失败时 fill/cut 为空、base_y=0。
    """
    sx0, sz0, sx1, sz1 = (int(v) for v in footprint_xz)
    if sx0 > sx1 or sz0 > sz1:
        return TerraformResult(success=False, base_y=0, reason="empty_footprint")

    NZ, NX = height_map.shape
    if sx0 < 0 or sx1 >= NX or sz0 < 0 or sz1 >= NZ:
        return TerraformResult(success=False, base_y=0, reason="out_of_bounds")

    patch = height_map[sz0:sz1 + 1, sx0:sx1 + 1].astype(np.int32)

    # 含 sentinel 列直接失败——上层应该不会传这种 footprint，但兜底一道。
    if (patch <= int(ctx.min_y)).any():
        return TerraformResult(success=False, base_y=0, reason="contains_sentinel")

    base_y = _pick_base_y(patch, target_strategy)
    delta = patch - base_y  # >0 表示要 cut, <0 表示要 fill

    if int(delta.max()) > int(max_cut):
        return TerraformResult(success=False, base_y=base_y,
                               reason=f"too_steep_cut(max={int(delta.max())})")
    if int((-delta).max()) > int(max_fill):
        return TerraformResult(success=False, base_y=base_y,
                               reason=f"too_steep_fill(max={int((-delta).max())})")

    # 选材质：用 footprint 中心点附近的 terrain_map 投票
    fill_block, top_block = _pick_materials(terrain_map, sx0, sz0, sx1, sz1)

    fill_blocks: list = []
    cut_blocks: list = []

    h_local, w_local = patch.shape
    for dz in range(h_local):
        for dx in range(w_local):
            sx = sx0 + dx
            sz = sz0 + dz
            ground_y = int(patch[dz, dx])
            xw, zw = ctx.s2w(sx, sz)

            if ground_y < base_y:
                # 垫：从 ground_y+1 填到 base_y-1 用 fill_block，顶层 base_y 用 top_block
                for yy in range(ground_y + 1, base_y):
                    fill_blocks.append((xw, yy, zw, fill_block))
                fill_blocks.append((xw, base_y, zw, top_block))
            elif ground_y > base_y:
                # 凿：把 base_y+1 .. ground_y 全清空气（不动 base_y 那层——它是地基顶）
                for yy in range(base_y + 1, ground_y + 1):
                    cut_blocks.append((xw, yy, zw))
                # 把 base_y 那层换成本地形顶面材质，避免凿完留个 stone 顶
                fill_blocks.append((xw, base_y, zw, top_block))
            else:
                # ground_y == base_y：什么都不用动；可选把顶面统一换成 top_block
                # 不动它——保留玩家世界的随机性
                pass

    return TerraformResult(
        success=True,
        base_y=int(base_y),
        fill_blocks=fill_blocks,
        cut_blocks=cut_blocks,
        reason="ok",
    )


def terraform_force_platform(footprint_xz: tuple,
                             height_map: np.ndarray,
                             features,
                             ctx: ScanContext,
                             terrain_map: Optional[np.ndarray] = None,
                             strategy: str = FOUNDATION_STRATEGY,
                             clearance: int = PLATFORM_CLEARANCE
                             ) -> TerraformResult:
    """强制平台兜底：无 cut/fill 上限，保证 footprint 一定能平整出地基。

    terraform_for_building / 高台兜底都失败时调。和它们的区别：
    - 无 max_cut/max_fill 限制：高列全削到 base_y，低列全填到 base_y。
    - **sentinel 列**（地形超扫描天花板、高度未知）：从 base_y+1 削空 clearance 格
      carve 出建筑空间（whatever 山体都凿掉），顶层 base_y 铺平台材质。
    base_y 取 footprint 内**有效列**的分位数；全 sentinel 时退 SEA_LEVEL。
    返回 success=True（除非 footprint 空/越界）。fill/cut 量可能很大。
    """
    sx0, sz0, sx1, sz1 = (int(v) for v in footprint_xz)
    if sx0 > sx1 or sz0 > sz1:
        return TerraformResult(success=False, base_y=0, reason="empty_footprint")
    NZ, NX = height_map.shape
    if sx0 < 0 or sx1 >= NX or sz0 < 0 or sz1 >= NZ:
        return TerraformResult(success=False, base_y=0, reason="out_of_bounds")

    patch = height_map[sz0:sz1 + 1, sx0:sx1 + 1].astype(np.int32)
    miny = int(ctx.min_y)
    valid = patch > miny
    if valid.any():
        base_y = _pick_base_y(patch[valid], strategy)   # 只用有效列定基准
    else:
        base_y = int(SEA_LEVEL)                          # 全 sentinel：无参考，退海平面
    clr = max(1, int(clearance))
    fill_block, top_block = _pick_materials(terrain_map, sx0, sz0, sx1, sz1)

    fill_blocks: list = []
    cut_blocks: list = []
    h_local, w_local = patch.shape
    for dz in range(h_local):
        for dx in range(w_local):
            sx = sx0 + dx
            sz = sz0 + dz
            xw, zw = ctx.s2w(sx, sz)
            ground_y = int(patch[dz, dx])
            if ground_y <= miny:
                # sentinel：地形高度未知 → 削空 clearance 格，顶铺平台
                for yy in range(base_y + 1, base_y + clr + 1):
                    cut_blocks.append((xw, yy, zw))
                fill_blocks.append((xw, base_y, zw, top_block))
            elif ground_y < base_y:
                for yy in range(ground_y + 1, base_y):
                    fill_blocks.append((xw, yy, zw, fill_block))
                fill_blocks.append((xw, base_y, zw, top_block))
            elif ground_y > base_y:
                for yy in range(base_y + 1, ground_y + 1):
                    cut_blocks.append((xw, yy, zw))
                fill_blocks.append((xw, base_y, zw, top_block))
            # ground_y == base_y：不动
    return TerraformResult(
        success=True, base_y=int(base_y),
        fill_blocks=fill_blocks, cut_blocks=cut_blocks, reason="force_platform")


def terraform_water_stilt(footprint_xz: tuple,
                          height_map: np.ndarray,
                          terrain_map: Optional[np.ndarray],
                          scan_volume: np.ndarray,
                          ctx: ScanContext,
                          codec: BlockCodec,
                          deck_offset: int = 2,
                          deck_block: str = "minecraft:dark_oak_planks",
                          post_block: str = "minecraft:dark_prismarine"
                          ) -> TerraformResult:
    """水上吊脚楼地基：甲板抬到 SEA_LEVEL+deck_offset，柱子从海床/陆面打到甲板。

    与普通 terraform 的区别：不填平水面（留可见海面），而是
      - 每列向下扫 scan_volume 找海床/陆面 surf_y；
      - surf_y < deck_y → 实心柱 surf_y+1..deck_y（之后由 _stiltify_fill 抽成栅栏腿）；
      - surf_y ≥ deck_y → 削掉甲板以上的陆面（岛缘列），甲板齐平。
    返回的 fill_blocks 仍是实心柱，调用方负责 _stiltify_fill 抽腿+露水面。
    """
    from ..mc.blocks import WATER_FLUIDS_EXTENDED
    sx0, sz0, sx1, sz1 = (int(v) for v in footprint_xz)
    if sx0 > sx1 or sz0 > sz1:
        return TerraformResult(success=False, base_y=0, reason="empty_footprint")
    NZ, NX = height_map.shape
    if sx0 < 0 or sx1 >= NX or sz0 < 0 or sz1 >= NZ:
        return TerraformResult(success=False, base_y=0, reason="out_of_bounds")

    NY = scan_volume.shape[0]
    miny = int(ctx.min_y)
    deck_y = int(SEA_LEVEL) + int(deck_offset)

    def _surface_y(sx: int, sz: int) -> int:
        """从 deck 上方往下扫第一个非水非空气实体方块的世界 Y（海床/陆面）。"""
        top = max(deck_y, int(height_map[sz, sx])) + 1
        top = min(top, miny + NY - 1)
        for wy in range(top, miny - 1, -1):
            yi = wy - miny
            if not (0 <= yi < NY):
                continue
            code = int(scan_volume[yi, sz, sx])
            bid = codec.decode(code).split("[", 1)[0] if codec is not None else ""
            if bid == "minecraft:air" or bid in WATER_FLUIDS_EXTENDED:
                continue
            return wy
        return int(SEA_LEVEL) - 1                      # 全水/空气 → 短腿兜底

    fill_blocks: list = []
    cut_blocks: list = []
    for sz in range(sz0, sz1 + 1):
        for sx in range(sx0, sx1 + 1):
            xw, zw = ctx.s2w(sx, sz)
            surf_y = _surface_y(sx, sz)
            if surf_y < deck_y:
                for yy in range(surf_y + 1, deck_y):
                    fill_blocks.append((xw, yy, zw, post_block))
                fill_blocks.append((xw, deck_y, zw, deck_block))
            elif surf_y > deck_y:                       # 岛缘陆面高出甲板 → 削平
                for yy in range(deck_y + 1, surf_y + 1):
                    cut_blocks.append((xw, yy, zw))
                fill_blocks.append((xw, deck_y, zw, deck_block))
            else:
                fill_blocks.append((xw, deck_y, zw, deck_block))

    return TerraformResult(success=True, base_y=int(deck_y),
                           fill_blocks=fill_blocks, cut_blocks=cut_blocks,
                           reason="water_stilt")


# ─────────────────────────────────────────────────────────────────
# HTTP 提交 + 内存同步
# ─────────────────────────────────────────────────────────────────

def apply_terraform(result: TerraformResult,
                    ctx: ScanContext,
                    scan_volume: np.ndarray,
                    height_map: np.ndarray,
                    codec: BlockCodec,
                    batch_size: int = 1024) -> bool:
    """把 fill_blocks/cut_blocks 提交到世界，成功才同步内存。

    遵守 UPGRADE_FROM_LEGACY.md §5：任何一批 set_blocks_batch 返回 False
    都视为整次 terraform 失败，**不更新** height_map / scan_volume，
    并打印 warning。这样上层 placement 拿到 False 可以选择回退（跳过粘贴）。

    Returns:
        True  — 所有批次成功；内存已同步。
        False — 至少一批失败；内存未变。
    """
    if not result.success:
        return False
    if not result.fill_blocks and not result.cut_blocks:
        return True  # 完美平地，啥都不用做

    # 1) 准备 HTTP payload。fill 用 {"x","y","z","id"}；cut 同结构但 id=air。
    payload: list = []
    for (xw, yw, zw, bid) in result.fill_blocks:
        payload.append({"x": int(xw), "y": int(yw), "z": int(zw), "id": str(bid)})
    for (xw, yw, zw) in result.cut_blocks:
        payload.append({"x": int(xw), "y": int(yw), "z": int(zw),
                        "id": "minecraft:air"})

    # 2) 分批提交。任意一批失败 → 立即报警 + 返回 False，不更新内存。
    for i in range(0, len(payload), batch_size):
        chunk = payload[i:i + batch_size]
        ok = set_blocks_batch(chunk)
        if not ok:
            print(f"[TERRAFORM] apply_terraform 第 {i // batch_size + 1} 批失败，"
                  f"放弃，内存未更新（base_y={result.base_y} cost={result.cost}）",
                  flush=True)
            return False

    # 3) HTTP 全成功 → 同步内存（height_map + scan_volume）
    _sync_memory(result, ctx, scan_volume, height_map, codec)
    return True


def _sync_memory(result: TerraformResult,
                 ctx: ScanContext,
                 scan_volume: np.ndarray,
                 height_map: np.ndarray,
                 codec: BlockCodec) -> None:
    """把 terraform 改动同步到 height_map + scan_volume。HTTP 成功后才能调。"""
    NY = scan_volume.shape[0]
    air_code = codec.AIR_CODE if codec is not None else 0
    use_compact = (codec is not None and scan_volume.dtype == np.uint16)

    for (xw, yw, zw, bid) in result.fill_blocks:
        sx, sz = ctx.w2s(int(xw), int(zw))
        yi = int(yw) - int(ctx.min_y)
        if 0 <= yi < NY and 0 <= sx < height_map.shape[1] and 0 <= sz < height_map.shape[0]:
            if use_compact:
                scan_volume[yi, sz, sx] = codec.encode(str(bid))
            # 不在 compact 路径就放弃同步 scan_volume——下游兜底机制会读 height_map

    for (xw, yw, zw) in result.cut_blocks:
        sx, sz = ctx.w2s(int(xw), int(zw))
        yi = int(yw) - int(ctx.min_y)
        if 0 <= yi < NY and 0 <= sx < height_map.shape[1] and 0 <= sz < height_map.shape[0]:
            if use_compact:
                scan_volume[yi, sz, sx] = air_code

    # height_map 直接写成 base_y——terraform 后整个 footprint 都齐平。
    # 同时把 features.height_map 也同步（如果 ctx 上挂了 features）。
    base_y = int(result.base_y)
    # 推断 footprint：从 fill+cut blocks 的 xw/zw 范围。
    if result.fill_blocks or result.cut_blocks:
        xs_world = [b[0] for b in result.fill_blocks] + [b[0] for b in result.cut_blocks]
        zs_world = [b[2] for b in result.fill_blocks] + [b[2] for b in result.cut_blocks]
        sxs = [ctx.w2s(int(x), 0)[0] for x in xs_world]
        szs = [ctx.w2s(0, int(z))[1] for z in zs_world]
        sx_lo, sx_hi = min(sxs), max(sxs)
        sz_lo, sz_hi = min(szs), max(szs)
        sx_lo = max(0, sx_lo); sz_lo = max(0, sz_lo)
        sx_hi = min(height_map.shape[1] - 1, sx_hi)
        sz_hi = min(height_map.shape[0] - 1, sz_hi)
        height_map[sz_lo:sz_hi + 1, sx_lo:sx_hi + 1] = base_y


# ─────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────

def _pick_base_y(patch: np.ndarray, strategy: str) -> int:
    """按 strategy 选 base_y。patch shape = (h, w) int32。"""
    if strategy == "median":
        return int(np.median(patch))
    if strategy == "min":
        return int(np.min(patch))
    if strategy == "max":
        return int(np.max(patch))
    if strategy.startswith("p"):
        try:
            pct = float(strategy[1:])
        except ValueError:
            pct = 70.0
        return int(round(float(np.percentile(patch, pct))))
    # 未知策略 → 默认 p70
    return int(round(float(np.percentile(patch, 70.0))))


def _pick_materials(terrain_map: Optional[np.ndarray],
                    sx0: int, sz0: int,
                    sx1: int, sz1: int) -> tuple:
    """从 footprint 内的 terrain_map 投票选材质。

    任务卡规则（卡 5 OUTPUT #2 末尾）：
        plains / 默认  → dirt + grass_block
        desert / sand → sandstone（顶层也 sandstone，因为 sand 物理不稳）
        snow           → snow_block + snow_block
        mountain       → cobblestone + cobblestone
    （注意：与 city/terrain.py 的 _TERRAIN_FILL 略有不同，这里 mountain
    走 cobblestone 而不是 stone，遵循卡 5 spec。）
    """
    DEFAULT = ("minecraft:dirt", "minecraft:grass_block")
    if terrain_map is None:
        return DEFAULT

    NZ, NX = terrain_map.shape
    sx0 = max(0, sx0); sx1 = min(NX - 1, sx1)
    sz0 = max(0, sz0); sz1 = min(NZ - 1, sz1)
    if sx0 > sx1 or sz0 > sz1:
        return DEFAULT

    patch = terrain_map[sz0:sz1 + 1, sx0:sx1 + 1].ravel()
    # TERRAIN_NAMES = ["plains", "desert", "mountain", "snow", "water", "jungle", "badlands"]
    counts = np.bincount(patch, minlength=7)
    dominant = int(np.argmax(counts))

    mapping = {
        0: ("minecraft:dirt",       "minecraft:grass_block"),     # plains
        1: ("minecraft:sandstone",  "minecraft:sandstone"),       # desert
        2: ("minecraft:cobblestone", "minecraft:cobblestone"),    # mountain (卡 5 要求)
        3: ("minecraft:snow_block", "minecraft:snow_block"),      # snow
        4: ("minecraft:dirt",       "minecraft:grass_block"),     # water 不应出现在 footprint
        5: ("minecraft:rooted_dirt", "minecraft:moss_block"),     # jungle
        6: ("minecraft:red_sandstone", "minecraft:red_sand"),     # badlands
    }
    return mapping.get(dominant, DEFAULT)
