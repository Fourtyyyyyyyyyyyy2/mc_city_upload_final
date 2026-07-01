"""Priority 2 卡 9.1：灵魂树前中心广场（八角形）。

公开 API:
    build_central_plaza(center_x, center_z, base_y, height_map, ctx, codec, ...)
        主入口，提交 HTTP。返回放置块数。
    build_plaza_payloads(...)
        dry-run 友好；纯计算，不调 HTTP。
    plaza_mask(NZ, NX, scx, scz, r, factor) -> np.ndarray[bool]
        八角形 mask 工具（卡 9.2 enumerate_blocks 复用）。

设计：
- 形状由两个 不等式 与 chebyshev/manhattan 组合定义：
    in_shape = (|dx| <= r) & (|dz| <= r) & (|dx|+|dz| <= r * factor)
  factor=1.0 是菱形，factor≈1.3 是切角八角，factor>=2.0 才是正方形（无切角）。
  注意：环很窄时切角八角的对角会被 footprint 吃掉，只剩 4 个 cardinal 凸台；
  要连续方框环用 factor>=2.0。
- 广场是"环"：八角形减去灵魂树 footprint 矩形（inner_half_x/z），不进 footprint，
  这样在树之后建也不会覆写树底层（CONSTRAINT「不破坏灵魂树」）。
- 外接圆半径按实际树 footprint 推：r = max(half_x, half_z) + plaza_padding；
  显式传 radius 时优先用 radius；都没有则回退 config.PLAZA_RADIUS。
- 广场内每列：base_y 顶层放 PLAZA_MATERIAL；从原 ground_y+1 到 base_y-1
  全部 PLAZA_SUB_MATERIAL（柱基）；如果 ground_y > base_y 则把 base_y+1..ground_y
  清成 air。
- HTTP 用单次 set_blocks_batch 提交。
- 不依赖 narrative 层（与 books / signs / decor 正交）。
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from ..config import (
    OCTAGON_MANHATTAN_FACTOR,
    PLAZA_MATERIAL,
    PLAZA_MAX_CUT,
    PLAZA_PADDING,
    PLAZA_RADIUS,
    PLAZA_SUB_MATERIAL,
)
from ..mc.placement import set_blocks_batch
from ..scan.coord_frame import ScanContext

PLAZA_BATCH_SIZE = 1024


def _resolve_radius(radius: int, tree_half_x: int, tree_half_z: int,
                    plaza_padding: int) -> int:
    """外接圆半径：显式 radius 优先；否则按树 footprint 推；都没有则 fallback。"""
    if radius and radius > 0:
        return int(radius)
    if tree_half_x > 0 or tree_half_z > 0:
        return int(max(tree_half_x, tree_half_z) + plaza_padding)
    return int(PLAZA_RADIUS)


def plaza_outer_radius(tree_half_x: int, tree_half_z: int,
                       plaza_padding: int = PLAZA_PADDING) -> int:
    """广场外缘半径 = max(half_x, half_z) + padding。

    调用方（builder [4.5] / 真实生成）必须把同一个值喂给 build_cardinal_axes
    的 plaza_outer，否则 golden 树（r~87）主道会起在树 footprint 内。
    """
    return _resolve_radius(0, tree_half_x, tree_half_z, plaza_padding)


# ── 公共入口 ──────────────────────────────────────────────────────
def build_central_plaza(center_x: int, center_z: int,
                        base_y: int,
                        height_map: np.ndarray,
                        ctx: ScanContext,
                        codec=None,
                        tree_half_x: int = 0,
                        tree_half_z: int = 0,
                        plaza_padding: int = PLAZA_PADDING,
                        radius: int = 0,
                        factor: float = OCTAGON_MANHATTAN_FACTOR,
                        top_material: str = PLAZA_MATERIAL,
                        sub_material: str = PLAZA_SUB_MATERIAL,
                        max_cut: int = PLAZA_MAX_CUT,
                        ) -> int:
    """灵魂树外八角"环"广场。地面填到 base_y，顶层 polished_andesite。

    Args:
        center_x, center_z:  世界坐标，广场中心 = 灵魂树中心
        base_y:              广场地面高度（推荐 = 灵魂树 base_y）
        height_map:          (NZ, NX) int32，scan 坐标
        ctx:                 ScanContext
        codec:               兼容签名，不需要
        tree_half_x/z:       灵魂树 footprint 半宽（footprint_xz//2）；用于挖空环内
        plaza_padding:       树外缘呼吸格数（环宽 ≈ 此值）
        radius:              显式外接圆半径（>0 时优先；0 时按 footprint 推）
        factor:              切角因子，见模块 docstring
        top_material:        顶层方块
        sub_material:        填土柱基方块

    Returns:
        实际写入的方块数（fill + cut 总数）。
    """
    _ = codec
    payloads, _, _, n_skip = build_plaza_payloads(
        center_x, center_z, base_y, height_map, ctx,
        tree_half_x=tree_half_x, tree_half_z=tree_half_z,
        plaza_padding=plaza_padding, radius=radius, factor=factor,
        top_material=top_material, sub_material=sub_material,
        max_cut=max_cut,
    )
    if n_skip:
        print(f"  依山而建：跳过 {n_skip} 列陡坡（凿深>{max_cut}），保留原地形")
    if not payloads:
        print("  没有可放置的广场方块")
        return 0

    success = 0
    for i in range(0, len(payloads), PLAZA_BATCH_SIZE):
        batch = payloads[i:i + PLAZA_BATCH_SIZE]
        if set_blocks_batch(batch):
            success += len(batch)
        else:
            print(f"  ⚠️ 广场批次写入失败（{len(batch)} 块），继续下一批")
    return success


# ── dry-run 友好的核心 ────────────────────────────────────────────
def build_plaza_payloads(center_x: int, center_z: int,
                         base_y: int,
                         height_map: np.ndarray,
                         ctx: ScanContext,
                         tree_half_x: int = 0,
                         tree_half_z: int = 0,
                         plaza_padding: int = PLAZA_PADDING,
                         radius: int = 0,
                         factor: float = OCTAGON_MANHATTAN_FACTOR,
                         top_material: str = PLAZA_MATERIAL,
                         sub_material: str = PLAZA_SUB_MATERIAL,
                         max_cut: int = PLAZA_MAX_CUT,
                         ) -> tuple[list[dict], int, int, int]:
    """构造广场 payload（八角环：八角形减去树 footprint 矩形）。

    Returns (payloads, n_fill_cols, n_cut_cols, n_skip_cols)。
    n_fill_cols: 填土列；n_cut_cols: 凿空列；n_skip_cols: 凿深>max_cut 跳过的陡坡列
    （依山而建，保留原地形，不凿沟）。
    """
    NZ, NX = height_map.shape
    scx, scz = ctx.w2s(int(center_x), int(center_z))

    radius = _resolve_radius(radius, tree_half_x, tree_half_z, plaza_padding)
    mask = plaza_mask(NZ, NX, scx, scz, radius, factor,
                      inner_half_x=tree_half_x, inner_half_z=tree_half_z)
    zs_idx, xs_idx = np.where(mask)

    payloads: list[dict] = []
    n_fill = 0
    n_cut = 0
    n_skip = 0

    for sz, sx in zip(zs_idx.tolist(), xs_idx.tolist()):
        ground_y = int(height_map[sz, sx])
        if ground_y <= int(ctx.min_y):
            continue  # sentinel 列跳过（避免铺到 y=min_y）
        wx, wz = ctx.s2w(int(sx), int(sz))

        if ground_y < base_y:
            # 柱基填到 base_y - 1，顶层 base_y 放 top
            for y in range(ground_y + 1, base_y):
                payloads.append({"x": int(wx), "y": int(y), "z": int(wz),
                                 "id": sub_material})
            payloads.append({"x": int(wx), "y": int(base_y), "z": int(wz),
                             "id": top_material})
            n_fill += 1
        elif ground_y > base_y:
            if ground_y - base_y > max_cut:
                n_skip += 1          # 陡坡：凿太深就跳过，保留原地形（依山而建）
                continue
            # 凿掉 base_y+1..ground_y 这些原地形方块；base_y 那层放 top
            for y in range(base_y + 1, ground_y + 1):
                payloads.append({"x": int(wx), "y": int(y), "z": int(wz),
                                 "id": "minecraft:air"})
            payloads.append({"x": int(wx), "y": int(base_y), "z": int(wz),
                             "id": top_material})
            n_cut += 1
        else:
            # ground_y == base_y：只换顶层
            payloads.append({"x": int(wx), "y": int(base_y), "z": int(wz),
                             "id": top_material})

    return payloads, n_fill, n_cut, n_skip


# ── 八角形 mask 工具 ──────────────────────────────────────────────
def plaza_mask(NZ: int, NX: int,
               scx: int, scz: int,
               radius: int = PLAZA_RADIUS,
               factor: float = OCTAGON_MANHATTAN_FACTOR,
               inner_half_x: int = 0,
               inner_half_z: int = 0,
               ) -> np.ndarray:
    """生成广场 mask（scan 坐标系，bool ndarray (NZ, NX)）。

    in_shape = (|dx|<=r) & (|dz|<=r) & (|dx|+|dz| <= r*factor)
    factor>=2 时退化为正方形（无切角）；factor=1 时退化为菱形；中间值是切角八角。
    inner_half_x/z > 0 时挖掉中央 (|dx|<=inner_half_x)&(|dz|<=inner_half_z)
    的矩形（= 树 footprint）→ "环"，不进 footprint。
    """
    zs_idx, xs_idx = np.indices((NZ, NX), dtype=np.int32)
    dx = np.abs(xs_idx - scx)
    dz = np.abs(zs_idx - scz)
    cheb = np.maximum(dx, dz)
    manh = dx + dz
    mask = (cheb <= radius) & (manh <= int(round(radius * factor)))
    if inner_half_x > 0 or inner_half_z > 0:
        inside_tree = (dx <= int(inner_half_x)) & (dz <= int(inner_half_z))
        mask &= ~inside_tree
    return mask


__all__ = [
    "PLAZA_BATCH_SIZE",
    "build_central_plaza", "build_plaza_payloads", "plaza_mask",
    "plaza_outer_radius",
]
