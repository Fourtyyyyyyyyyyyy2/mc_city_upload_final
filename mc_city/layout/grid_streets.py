"""卡 9.6：网格街道渲染——补齐 enumerate_blocks 街区之间的"次道"（中式棋盘）。

enumerate_blocks（blocks.py）把城切成周期网格的 30×30 街区，街区之间留
NEXT_ROAD_WIDTH 宽的缝当"次道"，但缝里的街道一直没渲染（卡 9.2 注释说"留
卡 9.3"，实际从未实现）。结果：建筑坐在棋盘格里，路却是另一套放射/环形系统，
两者对不上 → 观感"碎/不通"。

本模块沿 **与 enumerate_blocks 同一套周期网格**，在所有横/竖街缝中心铺横平
竖直街道，逐列贴地表（复用 RoadRenderer），城内 mid/outer 半径带内、跳水/
sentinel 列。街道自然穿过中心十字主道与环城路 → 每栋街区建筑临街、片区内全连通。

入口：render_grid_streets(...) → 渲染的街道线条数。
"""
from __future__ import annotations

import math
from typing import Callable, List, Tuple

import numpy as np

from ..config import (
    BLOCK_SIZE,
    CARDINAL_ROAD_WIDTH,
    GRID_STREET_CLIP_AT_WALL,
    GRID_STREET_MATERIAL,
    GRID_STREET_WALL_MARGIN,
    MID_RING_START_R,
    NEXT_ROAD_WIDTH,
    OUTER_RING_END_R,
    ROAD_BUILDING_CONNECTOR_MAX_LEN,
    ROAD_BUILDING_CONNECTORS_ENABLED,
    SEA_CITY_ENABLED,
    WALL_RADIUS,
    WALL_SHAPE,
)

_SQUARE = WALL_SHAPE == "square"
from ..roads.renderer import RoadRenderer
from ..scan.coord_frame import ScanContext
from .blocks import _axis_intervals


def _gap_centers(center: int, main_half: int, block_size: int,
                 period: int, reach: int) -> List[int]:
    """沿一条轴，返回相邻街区之间街缝的中心世界坐标。

    复用 enumerate_blocks 的 _axis_intervals 切块，取相邻块之间的缝中心。
    跳过中央那条缝（= 中心十字主道，已在 [4.5] 渲染）。
    """
    intervals = sorted(_axis_intervals(center, main_half, block_size, period, reach))
    centers: List[int] = []
    for (lo0, hi0), (lo1, hi1) in zip(intervals, intervals[1:]):
        gap_lo = hi0 + 1
        gap_hi = lo1 - 1
        if gap_hi < gap_lo:
            continue
        gc = (gap_lo + gap_hi) // 2
        if abs(gc - center) <= main_half:        # 中央缝 = 主道，跳过
            continue
        centers.append(gc)
    return centers


def _render_axis_runs(renderer: RoadRenderer, fixed: int, axis: str,
                      lo: int, hi: int, col_ok: Callable[[int, int], bool],
                      height_map: np.ndarray, ctx: ScanContext) -> int:
    """沿一条街道线扫描，把连续的"可建街道列"切成若干段分别渲染。

    遇到城外 / 水 / sentinel 列就断开（街道不延进虚空或穿过内圈广场）。
    axis="z"：固定 x=fixed 沿 z 走；axis="x"：固定 z=fixed 沿 x 走。
    返回渲染的段数。
    """
    NZ, NX = height_map.shape
    runs = 0
    cur: List[Tuple[int, int, int]] = []

    def _flush(seg):
        nonlocal runs
        if len(seg) >= 2:
            renderer.render_path(seg)
            runs += 1

    for t in range(int(lo), int(hi) + 1):
        wx, wz = (fixed, t) if axis == "z" else (t, fixed)
        if col_ok(wx, wz):
            sx, sz = wx - ctx.origin_x, wz - ctx.origin_z
            cur.append((wx, int(height_map[sz, sx]), wz))
        else:
            _flush(cur)
            cur = []
    _flush(cur)
    return runs


def _render_building_connectors(renderer: RoadRenderer,
                                building_boxes: list,
                                gx_centers: List[int],
                                gz_centers: List[int],
                                col_ok: Callable[[int, int], bool],
                                height_map: np.ndarray,
                                ctx: ScanContext) -> int:
    if not ROAD_BUILDING_CONNECTORS_ENABLED or not building_boxes:
        return 0

    def _blocked(wx: int, wz: int) -> bool:
        for bx0, bx1, bz0, bz1 in building_boxes:
            if bx0 <= wx <= bx1 and bz0 <= wz <= bz1:
                return True
        return False

    def _line_ok(x0: int, z0: int, x1: int, z1: int) -> bool:
        steps = max(abs(x1 - x0), abs(z1 - z0))
        if steps <= 0:
            return False
        for i in range(steps + 1):
            t = i / steps
            wx = int(round(x0 + (x1 - x0) * t))
            wz = int(round(z0 + (z1 - z0) * t))
            if _blocked(wx, wz) or not col_ok(wx, wz):
                return False
        return True

    def _path(x0: int, z0: int, x1: int, z1: int):
        sx0, sz0 = x0 - ctx.origin_x, z0 - ctx.origin_z
        sx1, sz1 = x1 - ctx.origin_x, z1 - ctx.origin_z
        return [(x0, int(height_map[sz0, sx0]), z0),
                (x1, int(height_map[sz1, sx1]), z1)]

    rendered = 0
    max_len = int(ROAD_BUILDING_CONNECTOR_MAX_LEN)
    for bx0, bx1, bz0, bz1 in building_boxes:
        cx = (int(bx0) + int(bx1)) // 2
        cz = (int(bz0) + int(bz1)) // 2
        candidates = []
        for gx in gx_centers:
            if bx0 <= gx <= bx1:
                continue
            if gx < bx0:
                candidates.append((abs(bx0 - gx), bx0 - 1, cz, gx, cz))
            else:
                candidates.append((abs(gx - bx1), bx1 + 1, cz, gx, cz))
        for gz in gz_centers:
            if bz0 <= gz <= bz1:
                continue
            if gz < bz0:
                candidates.append((abs(bz0 - gz), cx, bz0 - 1, cx, gz))
            else:
                candidates.append((abs(gz - bz1), cx, bz1 + 1, cx, gz))
        for dist, x0, z0, x1, z1 in sorted(candidates):
            if dist > max_len:
                break
            if _line_ok(x0, z0, x1, z1):
                renderer.render_path(_path(x0, z0, x1, z1))
                rendered += 1
                break
    return rendered


def render_grid_streets(center_x: int, center_z: int,
                        ctx: ScanContext,
                        features,
                        height_map_original: np.ndarray,
                        *,
                        block_size: int = BLOCK_SIZE,
                        next_road_width: int = NEXT_ROAD_WIDTH,
                        main_road_width: int = CARDINAL_ROAD_WIDTH,
                        mid_start_r: int = MID_RING_START_R,
                        outer_end_r: int = OUTER_RING_END_R,
                        wall_radius: int = WALL_RADIUS,
                        material: str = GRID_STREET_MATERIAL,
                        building_boxes: list = None,
                        scan_volume=None,
                        codec=None) -> int:
    """渲染横平竖直网格街道（中式棋盘）。返回渲染的街道段数。

    街道线 = enumerate_blocks 街区之间的缝（同周期网格）。每条线逐列贴地表铺，
    限制在 mid_start_r..outer_end_r 半径带内、非水非 sentinel 列。
    building_boxes 给定时街道遇建筑列自动断开（不穿楼、不被楼覆盖）。
    """
    # §10.f：街道外缘 clip 到城墙内侧 → 街道不穿/不盖城墙，墙体完整。
    # 收紧 outer_end_r 同时约束 _gap_centers 的 reach、扫描范围和 _col_ok 的 r2_hi。
    if GRID_STREET_CLIP_AT_WALL:
        outer_end_r = min(outer_end_r, wall_radius - GRID_STREET_WALL_MARGIN)

    main_half = main_road_width // 2
    period = block_size + next_road_width
    gx_centers = _gap_centers(center_x, main_half, block_size, period, outer_end_r)
    gz_centers = _gap_centers(center_z, main_half, block_size, period, outer_end_r)

    renderer = RoadRenderer(
        road_block=material, road_width=next_road_width,
        height_map=height_map_original,
        origin_x=ctx.origin_x, origin_z=ctx.origin_z, min_y=int(ctx.min_y),
        blocked_boxes=building_boxes,
        scan_volume=scan_volume, codec=codec,
    )

    valid = features.valid_mask
    is_water = features.is_water
    NZ, NX = height_map_original.shape
    r2_lo = mid_start_r * mid_start_r
    r2_hi = outer_end_r * outer_end_r

    def _col_ok(wx: int, wz: int) -> bool:
        if _SQUARE:                                   # 方城：切比雪夫方距带
            m = max(abs(wx - center_x), abs(wz - center_z))
            if not (mid_start_r <= m <= outer_end_r):
                return False
        else:
            d2 = (wx - center_x) ** 2 + (wz - center_z) ** 2
            if not (r2_lo <= d2 <= r2_hi):
                return False
        sx, sz = wx - ctx.origin_x, wz - ctx.origin_z
        if not (0 <= sx < NX and 0 <= sz < NZ):
            return False
        # 海城：水面也铺街（成栈桥）；否则水格不铺。
        if SEA_CITY_ENABLED:
            return bool(valid[sz, sx])
        return bool(valid[sz, sx]) and not bool(is_water[sz, sx])

    count = 0
    for gx in gx_centers:                         # 竖街（固定 x，沿 z）
        count += _render_axis_runs(
            renderer, gx, "z",
            center_z - outer_end_r, center_z + outer_end_r,
            _col_ok, height_map_original, ctx)
    for gz in gz_centers:                         # 横街（固定 z，沿 x）
        count += _render_axis_runs(
            renderer, gz, "x",
            center_x - outer_end_r, center_x + outer_end_r,
            _col_ok, height_map_original, ctx)
    count += _render_building_connectors(
        renderer, building_boxes or [], gx_centers, gz_centers,
        _col_ok, height_map_original, ctx)
    return count


__all__ = ["render_grid_streets"]
