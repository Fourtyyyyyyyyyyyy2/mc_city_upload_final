"""Priority 2 卡 9.2：次轴 + 30×30 街区切分（纯数据，不渲染）。

公开 API:
    BlockRegion          —— 一个街区矩形 + 元数据（卡 9.3 消费）
    enumerate_blocks(...)—— 在 mid/outer 圈按 grid 切出 BlockRegion 列表

设计：
- grid 对齐 4 条 cardinal 主道（中心十字 = 主道，宽 CARDINAL_ROAD_WIDTH）。
  每个方向上 block_size 街区 + next_road_width 次道，周期 P = block_size + width。
- 街区按中心点：圈层查 ring_masks（无则按距离圆形兜底）；公会查 _sector_guild。
- terrain_score = clip(1 - std(footprint 有效高度)/10, 0, 1)；blocked = 水/sentinel。
- 跨城墙的街区直接丢弃（不放在墙上）；只产数据，次道渲染留卡 9.3。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from ..config import (
    BLOCK_SIZE,
    CARDINAL_ROAD_WIDTH,
    GRID_SUBURB_ENABLED,
    MID_RING_START_R,
    NEXT_ROAD_WIDTH,
    OUTER_RING_END_R,
    SEA_CITY_ENABLED,
    SUBURB_WALL_GAP,
    WALL_RADIUS,
    WALL_SHAPE,
)

_SQUARE = WALL_SHAPE == "square"


def _metric(dx: int, dz: int) -> float:
    """到城心的距离度量：方城用切比雪夫(方距)，圆城用欧氏(圆距)。"""
    return max(abs(dx), abs(dz)) if _SQUARE else math.hypot(dx, dz)
from ..narrative.metadata import _sector_guild
from ..scan.coord_frame import ScanContext


@dataclass
class BlockRegion:
    """一个街区矩形（世界坐标，闭区间）+ 元数据。"""
    x0: int
    z0: int
    x1: int
    z1: int
    ring: str            # "mid" | "outer"
    guild: str           # _sector_guild 推断
    angle_deg: float     # 几何中心相对 city center 的角度
    terrain_score: float # 0..1，1 = 易建造（footprint 高度 std 小）
    blocked: bool        # True = 中心是水 / sentinel


def _axis_intervals(center: int, main_half: int, block_size: int,
                    period: int, reach: int) -> list[tuple[int, int]]:
    """沿一条轴，向 ± 两侧切出街区区间 (lo, hi) 闭区间。

    第一个街区紧贴主道外缘（center ± (main_half+1)），之后每 period 一个。
    直到街区近端超过 reach 为止。
    """
    out: list[tuple[int, int]] = []
    k = 0
    while True:                                  # 正向
        lo = center + main_half + 1 + k * period
        if lo - center > reach:
            break
        out.append((lo, lo + block_size - 1))
        k += 1
    k = 0
    while True:                                  # 负向
        hi = center - main_half - 1 - k * period
        if center - hi > reach:
            break
        out.append((hi - block_size + 1, hi))
        k += 1
    return out


def enumerate_blocks(center_x: int, center_z: int,
                     ctx: ScanContext,
                     ring_masks,
                     features,
                     wall_radius: int = WALL_RADIUS,
                     mid_start_r: int = MID_RING_START_R,
                     outer_end_r: int = OUTER_RING_END_R,
                     block_size: int = BLOCK_SIZE,
                     next_road_width: int = NEXT_ROAD_WIDTH,
                     main_road_width: int = CARDINAL_ROAD_WIDTH,
                     ) -> list[BlockRegion]:
    """切出 mid/outer 圈的 30×30 街区列表（纯数据）。

    ring_masks 给出时按 mask 判圈层（mid/outer，其它丢）；为 None 时按到中心
    距离圆形兜底（mid: <wall_radius, outer: >=wall_radius）。ctx 用于世界↔scan。
    """
    hm = features.height_map
    valid = features.valid_mask
    is_water = features.is_water
    NZ, NX = hm.shape

    main_half = main_road_width // 2
    period = block_size + next_road_width
    xs = _axis_intervals(center_x, main_half, block_size, period, outer_end_r)
    zs = _axis_intervals(center_z, main_half, block_size, period, outer_end_r)

    regions: list[BlockRegion] = []
    for x0, x1 in xs:
        for z0, z1 in zs:
            bx = (x0 + x1) // 2
            bz = (z0 + z1) // 2
            dx = bx - center_x
            dz = bz - center_z

            # 中心落在主道上 → 跳过（构造上不会，但兜底）
            if abs(dx) <= main_half or abs(dz) <= main_half:
                continue

            center_dist = _metric(dx, dz)
            # 卡 11.1：方城开郊区时街区填到 outer_end_r（含城外），否则只到墙内。
            suburb = GRID_SUBURB_ENABLED and _SQUARE
            # 方城：街区填到城墙内（方距 ≤ wall），圆城：填到 outer_end_r。
            upper = outer_end_r if (suburb or not _SQUARE) \
                else (wall_radius - next_road_width)
            if center_dist < mid_start_r or center_dist > upper:
                continue

            # 跨城墙的街区丢弃（街区四角分跨墙内外）
            corner_d = [_metric(cx - center_x, cz - center_z)
                        for cx in (x0, x1) for cz in (z0, z1)]
            if _SQUARE:
                fully_in = max(corner_d) <= wall_radius - 2
                fully_out = min(corner_d) > wall_radius + SUBURB_WALL_GAP
                if fully_in:
                    pass                               # 全在墙内 → 城区街区
                elif suburb and fully_out:
                    pass                               # 卡 11.1：全在墙外 → 郊区街区
                else:
                    continue                           # 横跨墙体 / 郊区未开 → 丢
            elif min(corner_d) < wall_radius < max(corner_d):
                continue

            scx_b, scz_b = ctx.w2s(bx, bz)
            in_bounds = (0 <= scx_b < NX and 0 <= scz_b < NZ)

            # 圈层归属
            if ring_masks is not None:
                if in_bounds and bool(ring_masks.mid[scz_b, scx_b]):
                    ring = "mid"
                elif in_bounds and bool(ring_masks.outer[scz_b, scx_b]):
                    ring = "outer"
                else:
                    continue                      # 中心不在城内圈层
            elif _SQUARE:
                # 方城无 ring_masks：方距内半 = mid，外半 = outer
                ring = "mid" if center_dist < (mid_start_r + wall_radius) // 2 else "outer"
            else:
                ring = "mid" if center_dist < wall_radius else "outer"

            guild = _sector_guild(math.degrees(math.atan2(dz, dx)) % 360.0)

            # terrain_score：footprint 有效高度的 std
            sx0, sz0 = ctx.w2s(x0, z0)
            sx1, sz1 = ctx.w2s(x1, z1)
            sx0 = max(0, sx0); sz0 = max(0, sz0)
            sx1 = min(NX - 1, sx1); sz1 = min(NZ - 1, sz1)
            sub_h = hm[sz0:sz1 + 1, sx0:sx1 + 1]
            sub_v = valid[sz0:sz1 + 1, sx0:sx1 + 1]
            vals = sub_h[sub_v]
            std = float(vals.std()) if vals.size > 0 else 99.0
            terrain_score = float(np.clip(1.0 - std / 10.0, 0.0, 1.0))

            # 海城模式：水格不算 blocked（楼立海面）。否则水中心 → 跳过。
            # 注意 not in_bounds 必须排在最前，短路掉后面的越界 is_water/valid 索引。
            blocked = (not in_bounds
                       or (bool(is_water[scz_b, scx_b]) and not SEA_CITY_ENABLED)
                       or not bool(valid[scz_b, scx_b]))

            regions.append(BlockRegion(
                x0=int(x0), z0=int(z0), x1=int(x1), z1=int(z1),
                ring=ring, guild=guild,
                angle_deg=float(math.degrees(math.atan2(dz, dx)) % 360.0),
                terrain_score=terrain_score, blocked=blocked,
            ))
    return regions


__all__ = ["BlockRegion", "enumerate_blocks"]
