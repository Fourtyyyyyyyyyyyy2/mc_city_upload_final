"""城市绿化（卡 16.1）：把标准树木模型散布到空地/路边，按地形 reskin。

设计：建筑/道路放完后追加一步（不改 [0]~[9] 顺序）。在城墙内、广场外按网格采样
候选点 + 抖动 → 拒掉建筑 footprint/道路/水/陡坡/sentinel → 贴地种一棵随机树。
树材质走 make_remap(local_terrain)（云杉→地形木+叶：jungle→丛林、badlands→相思…），
plains 无主题保留云杉。flag GREENERY_ENABLED，密度 GREENERY_SPACING。
"""
from __future__ import annotations

import glob
import os
import random

import numpy as np

from ..config import (
    COMPONENT_ROOT,
    GREENERY_JITTER,
    GREENERY_MAX,
    GREENERY_MAX_SLOPE,
    GREENERY_SPACING,
    GREENERY_TREE_GLOB,
    WALL_RADIUS,
    WALL_SHAPE,
)
from ..mc.placement import paste_volume
from ..scan.coord_frame import ScanContext
from .placement import footprint_xz
from .reskin import make_remap
from .terrain import terrain_name_at

_SQUARE = WALL_SHAPE == "square"


def list_greenery_trees() -> list[str]:
    """列出所有标准树木模型（components/**/<glob>）。"""
    pat = os.path.join(COMPONENT_ROOT, "**", GREENERY_TREE_GLOB)
    return sorted(glob.glob(pat, recursive=True))


def _metric(dx: int, dz: int) -> float:
    return max(abs(dx), abs(dz)) if _SQUARE else (dx * dx + dz * dz) ** 0.5


def scatter_greenery(center_x: int, center_z: int,
                     ctx: ScanContext, codec,
                     height_map: np.ndarray, terrain_map: np.ndarray,
                     features,
                     placed_boxes: list,
                     road_cells=None,
                     wall_radius: int = WALL_RADIUS,
                     plaza_r: int = 40,
                     spacing: int = GREENERY_SPACING,
                     max_trees: int = GREENERY_MAX,
                     seed: int = 1234) -> int:
    """在城内空地/路边散种绿化树。返回实际种下的棵数。

    placed_boxes: 建筑世界盒 (x0,x1,z0,z1)，用于避让；road_cells: 道路 scan 格 (sz,sx)
    集合（astar 模式有，其它模式可为 None 只避建筑）。
    """
    trees = list_greenery_trees()
    if not trees:
        print("   ⚠️ 没找到绿化树模型（GREENERY_TREE_GLOB 无匹配），跳过")
        return 0

    NZ, NX = height_map.shape
    miny = int(ctx.min_y)
    valid = features.valid_mask if features is not None else None
    is_water = features.is_water if features is not None else None

    road_mask = None
    if road_cells:
        road_mask = np.zeros((NZ, NX), dtype=bool)
        for sz, sx in road_cells:
            if 0 <= sz < NZ and 0 <= sx < NX:
                road_mask[sz, sx] = True

    rng = random.Random(seed)
    step = max(4, int(spacing))
    plaza_lim = max(0, int(plaza_r))
    upper = int(wall_radius) - 2

    placed = 0
    radius_thresh2 = upper * upper            # 圆城用平方距比较

    gz = center_z - wall_radius
    while gz <= center_z + wall_radius:
        gx = center_x - wall_radius
        while gx <= center_x + wall_radius:
            if placed >= max_trees:
                return _finish(placed)

            wx = gx + rng.randint(-GREENERY_JITTER, GREENERY_JITTER)
            wz = gz + rng.randint(-GREENERY_JITTER, GREENERY_JITTER)
            gx += step

            # 圈层：广场外、城墙内
            dist = _metric(wx - center_x, wz - center_z)
            if dist <= plaza_lim or dist > upper:
                continue

            sx, sz = ctx.w2s(wx, wz)
            if not (0 <= sx < NX and 0 <= sz < NZ):
                continue
            if valid is not None and not bool(valid[sz, sx]):
                continue
            if is_water is not None and bool(is_water[sz, sx]):
                continue
            base_y = int(height_map[sz, sx])
            if base_y <= miny:                     # sentinel
                continue
            if terrain_name_at(terrain_map, sx, sz) == "water":
                continue
            if road_mask is not None and bool(road_mask[sz, sx]):
                continue

            path = rng.choice(trees)
            fpx, fpz = footprint_xz(path, rotation_deg=0)
            hx, hz = fpx // 2, fpz // 2

            # 陡坡拒绝：footprint 高度跨度太大 → 树半埋/悬空
            x0 = max(0, sx - hx); x1 = min(NX - 1, sx + hx)
            z0 = max(0, sz - hz); z1 = min(NZ - 1, sz + hz)
            patch = height_map[z0:z1 + 1, x0:x1 + 1]
            pv = patch[patch > miny]
            if pv.size == 0 or float(pv.max() - pv.min()) > GREENERY_MAX_SLOPE:
                continue

            # 建筑避让：树 footprint 盒与任一建筑盒相交 → 拒（含半宽外扩）
            tb = (wx - hx, wx + hx, wz - hz, wz + hz)
            if any(_boxes_overlap(tb, b) for b in placed_boxes):
                continue
            # 道路避让：footprint 内压到路 → 拒（树干别长在路上）
            if road_mask is not None and bool(road_mask[z0:z1 + 1, x0:x1 + 1].any()):
                continue

            origin = (int(wx - hx), base_y, int(wz - hz))
            try:
                paste_volume(path, origin=origin, clear_target=False, rotation=0,
                             block_remap=make_remap(terrain_name_at(terrain_map, sx, sz)))
                placed += 1
                placed_boxes.append(tb)            # 让后续树也避开这棵
            except Exception as exc:
                print(f"  ⚠️ 绿化树渲染失败 ({wx},{wz}): {exc!r}")
        gz += step

    return _finish(placed)


def _finish(placed: int) -> int:
    print(f"   🌳 城市绿化：种下 {placed} 棵树")
    return placed


def _boxes_overlap(a, b) -> bool:
    """两个世界盒 (x0,x1,z0,z1) 是否相交（闭区间）。"""
    return not (a[1] < b[0] or a[0] > b[1] or a[3] < b[2] or a[2] > b[3])


__all__ = ["scatter_greenery", "list_greenery_trees"]
