"""Priority 2 卡 9.1：4 条 cardinal 中轴主道。

公开 API:
    build_cardinal_axes(center_x, center_z, base_y, height_map, ctx, codec, ...)
        主入口，提交 HTTP，渲染 4 条主道。返回成功渲染的道路数（0~4）。
    cardinal_endpoints(center_x, center_z, plaza_outer, wall_radius)
        几何工具：返回 4 条主道的 (start_world, end_world)。dry-run 用。

设计：
- 4 条 cardinal：E (+X)、W (-X)、S (+Z)、N (-Z)
- 起点 = center 朝该方向 plaza_outer 格（紧贴广场外缘，避免压广场）
- 终点 = center 朝该方向 wall_radius - 2 格（不顶城墙，留 1 格收口）
- 复用现有 RoadRenderer：含 sentinel 修复（_terrain_y 判 min_y）+ 楼梯坡处理
- 道路材质 + 宽度 = CARDINAL_ROAD_MATERIAL / CARDINAL_ROAD_WIDTH
- 单条失败不影响其它
- 不动 RoadRenderer 本身（与现有道路系统兼容，但作为独立调用方）
"""
from __future__ import annotations

from typing import Iterable

import numpy as np

from ..config import (
    CARDINAL_ROAD_MATERIAL,
    CARDINAL_ROAD_WIDTH,
    PLAZA_RADIUS,
    WALL_RADIUS,
)
from ..roads.renderer import RoadRenderer
from ..scan.coord_frame import ScanContext


# 4 个 cardinal 方向 (dx, dz)：east / west / south / north
_CARDINALS: tuple[tuple[str, int, int], ...] = (
    ("east",  +1,  0),
    ("west",  -1,  0),
    ("south",  0, +1),
    ("north",  0, -1),
)


# ── 公共入口 ──────────────────────────────────────────────────────
def build_cardinal_axes(center_x: int, center_z: int,
                        base_y: int,
                        height_map: np.ndarray,
                        ctx: ScanContext,
                        codec=None,
                        wall_radius: int = WALL_RADIUS,
                        plaza_outer: int = PLAZA_RADIUS,
                        road_width: int = CARDINAL_ROAD_WIDTH,
                        material: str = CARDINAL_ROAD_MATERIAL,
                        blocked_boxes: list = None,
                        scan_volume=None,
                        ) -> int:
    """渲染 4 条 cardinal 主道。base_y 留作未来扩展，目前 RoadRenderer 各列
    独立读地表，主道贴地形（含楼梯 / 桥处理）。

    blocked_boxes 给定时（建筑之后渲染），主道遇建筑列自动断开，不被楼覆盖。

    Returns:
        成功渲染的主道数（0~4）。单条失败只 print warning，不抛。
    """
    _ = base_y  # 当前未用；预留给未来"主道独立 terraform"扩展

    renderer = RoadRenderer(
        road_block=material,
        road_width=road_width,
        height_map=height_map,
        origin_x=ctx.origin_x,
        origin_z=ctx.origin_z,
        min_y=int(ctx.min_y),
        blocked_boxes=blocked_boxes,
        scan_volume=scan_volume,
        codec=codec,
    )

    endpoints = cardinal_endpoints(center_x, center_z, plaza_outer, wall_radius)

    success = 0
    for label, (sw, ew) in zip(("E", "W", "S", "N"), endpoints):
        sx_w, sz_w = sw
        ex_w, ez_w = ew
        path = _build_world_path(sx_w, sz_w, ex_w, ez_w, height_map, ctx)
        if not path:
            print(f"  ⚠️ 主道 {label}: 路径为空（端点越界 / 全 sentinel）")
            continue
        try:
            renderer.render_path(path)
            success += 1
            print(f"  ✅ 主道 {label}: 从 ({sx_w},{sz_w}) 到 ({ex_w},{ez_w})，"
                  f"{len(path)} 列")
        except Exception as exc:
            print(f"  ⚠️ 主道 {label} render 异常：{exc!r}")
    return success


# ── 几何工具（dry-run + 卡 9.2 复用） ─────────────────────────────
def cardinal_endpoints(center_x: int, center_z: int,
                       plaza_outer: int = PLAZA_RADIUS,
                       wall_radius: int = WALL_RADIUS,
                       wall_gap: int = 2,
                       ) -> list[tuple[tuple[int, int], tuple[int, int]]]:
    """返回 4 条主道的 ((start_x, start_z), (end_x, end_z)) 世界坐标。

    起点 = center + plaza_outer * cardinal（紧贴广场外缘）
    终点 = center + (wall_radius - wall_gap) * cardinal
    """
    out: list[tuple[tuple[int, int], tuple[int, int]]] = []
    for _name, dx, dz in _CARDINALS:
        sx = int(center_x + dx * plaza_outer)
        sz = int(center_z + dz * plaza_outer)
        ex = int(center_x + dx * (wall_radius - wall_gap))
        ez = int(center_z + dz * (wall_radius - wall_gap))
        out.append(((sx, sz), (ex, ez)))
    return out


# ── 内部 helpers ──────────────────────────────────────────────────
def _build_world_path(sx: int, sz: int, ex: int, ez: int,
                      height_map: np.ndarray,
                      ctx: ScanContext) -> list[tuple[int, int, int]]:
    """生成沿 cardinal 直线的 (wx, wy, wz) 路径点列表。

    每格独立读 height_map，sentinel 列跳过（不入路径）。
    RoadRenderer.render_path 内部会再做一次 _terrain_y snap + slope/water 处理。
    """
    NZ, NX = height_map.shape
    dx = ex - sx
    dz = ez - sz
    length = int(max(abs(dx), abs(dz)))
    if length == 0:
        return []
    path: list[tuple[int, int, int]] = []
    for t in range(length + 1):
        f = t / length
        wx = int(round(sx + f * dx))
        wz = int(round(sz + f * dz))
        xs = wx - ctx.origin_x
        zs = wz - ctx.origin_z
        if not (0 <= xs < NX and 0 <= zs < NZ):
            continue
        y = int(height_map[zs, xs])
        if y <= int(ctx.min_y):
            continue  # sentinel：本列地表无效
        path.append((wx, y, wz))
    return path


__all__ = [
    "build_cardinal_axes", "cardinal_endpoints",
]
