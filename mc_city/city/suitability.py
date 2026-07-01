"""城市选址评分：适宜度图、道路邻近度、圈层候选点。"""
import math

import numpy as np

from ..config import SEA_CITY_ENABLED, SEA_CITY_WATER_WEIGHT
from ..scan.coord_frame import ScanContext


def compute_suitability_map(height_map: np.ndarray,
                            terrain_map: np.ndarray,
                            ctx: ScanContext,
                            center_x: int, center_z: int,
                            max_radius: int = 260,
                            max_slope: float = 0.35) -> np.ndarray:
    """返回 (NZ, NX) float32 适宜度图，值域 0..1。

    打分：坡度（越平越好） × 地形权重 + 中心邻近度小幅加成。
    水域格子、最大半径以外的格子、坡度超阈值的格子直接为 0。
    """
    NZ, NX = height_map.shape
    suitability = np.zeros((NZ, NX), dtype=np.float32)

    hm_float = height_map.astype(np.float32)
    grad_z, grad_x = np.gradient(hm_float)
    slope_map = np.sqrt(grad_x ** 2 + grad_z ** 2)

    scx, scz = ctx.w2s(center_x, center_z)
    xs_idx = np.arange(NX, dtype=np.float32)
    zs_idx = np.arange(NZ, dtype=np.float32)
    xs_grid, zs_grid = np.meshgrid(xs_idx, zs_idx)
    dist_map = np.sqrt((xs_grid - scx) ** 2 + (zs_grid - scz) ** 2)

    # code 4 = water。海城模式把水当可建平地（SEA_LEVEL 浮楼），否则 0.0 排除。
    water_w = SEA_CITY_WATER_WEIGHT if SEA_CITY_ENABLED else 0.0
    terrain_weights = {0: 1.0, 1: 0.9, 2: 0.7, 3: 0.85, 4: water_w, 5: 0.9, 6: 0.9}

    invalid_y = ctx.min_y  # height_map.py 用 min_y 作为"无 surface / 撞天花板"的 sentinel

    for zs in range(NZ):
        for xs in range(NX):
            if dist_map[zs, xs] > max_radius:
                continue

            if int(height_map[zs, xs]) <= invalid_y:
                continue  # 无效列（被截断的高山顶 / 整列空气）

            t_code = int(terrain_map[zs, xs])
            if t_code == 4 and not SEA_CITY_ENABLED:  # 水：非海城排除；海城当可建
                continue

            slope = float(slope_map[zs, xs])
            if slope > max_slope:
                continue

            slope_score = 1.0 - (slope / max_slope)
            terrain_weight = terrain_weights.get(t_code, 1.0)
            dist_norm = float(dist_map[zs, xs]) / max(max_radius, 1)
            center_bonus = 0.1 * (1.0 - dist_norm)

            suitability[zs, xs] = float(slope_score * terrain_weight + center_bonus)

    np.clip(suitability, 0.0, 1.0, out=suitability)
    return suitability


def compute_road_distance_map(backbone_nodes: list,
                              height_map: np.ndarray,
                              ctx: ScanContext,
                              max_dist: float = 30.0) -> np.ndarray:
    """每个格子到最近骨架道路节点的距离 → 归一化邻近度（越近越高）。"""
    NZ, NX = height_map.shape

    if not backbone_nodes:
        return np.ones((NZ, NX), dtype=np.float32)

    node_scan = []
    for wx, wz in backbone_nodes:
        sx, sz = ctx.w2s(wx, wz)
        if 0 <= sx < NX and 0 <= sz < NZ:
            node_scan.append((sx, sz))

    if not node_scan:
        return np.ones((NZ, NX), dtype=np.float32)

    nodes_arr = np.array(node_scan, dtype=np.float32)
    xs_idx = np.arange(NX, dtype=np.float32)
    zs_idx = np.arange(NZ, dtype=np.float32)
    xs_grid, zs_grid = np.meshgrid(xs_idx, zs_idx)

    CHUNK = 500
    min_dist_map = np.full((NZ, NX), max_dist, dtype=np.float32)

    for i in range(0, len(nodes_arr), CHUNK):
        chunk = nodes_arr[i:i + CHUNK]
        dx = xs_grid[:, :, np.newaxis] - chunk[:, 0]
        dz = zs_grid[:, :, np.newaxis] - chunk[:, 1]
        dists = np.sqrt(dx ** 2 + dz ** 2)
        chunk_min = dists.min(axis=2)
        np.minimum(min_dist_map, chunk_min, out=min_dist_map)

    road_score_map = np.clip(1.0 - min_dist_map / max_dist, 0.0, 1.0)
    return road_score_map.astype(np.float32)


def find_flat_slots_in_ring(suitability_map: np.ndarray,
                            ctx: ScanContext,
                            center_x: int, center_z: int,
                            r_min: float, r_max: float,
                            grid_step: int = 12,
                            min_suitability: float = 0.45,
                            top_k: int = 200,
                            road_score_map: np.ndarray = None,
                            road_weight: float = 2.0,
                            ring_mask: np.ndarray = None) -> list:
    """在圈层内按步长采样候选建造位置。

    卡 3：如果传入 ring_mask（来自 city.rings.grow_organic_rings），则用 mask 判定
    属于本圈；否则退回旧的 r_min/r_max 同心圆判定（_circular fallback 路径）。

    返回 [(world_x, world_z, final_score), ...]，按分数降序，最多 top_k 个。
    final_score = suitability + road_weight × road_score。
    """
    NZ, NX = suitability_map.shape
    scx, scz = ctx.w2s(center_x, center_z)

    candidates = []
    use_mask = ring_mask is not None

    if use_mask:
        # mask 路径：扫描整张图，mask=True 的格子才考虑（采样步长照样起效）
        xs_start, xs_end = 0, NX - 1
        zs_start, zs_end = 0, NZ - 1
    else:
        # 圆形兜底：margin 限定窗口，跟旧版一致
        margin = int(r_max) + grid_step
        xs_start = max(0, scx - margin)
        xs_end = min(NX - 1, scx + margin)
        zs_start = max(0, scz - margin)
        zs_end = min(NZ - 1, scz + margin)

    for zs in range(zs_start, zs_end + 1, grid_step):
        for xs in range(xs_start, xs_end + 1, grid_step):

            score = float(suitability_map[zs, xs])
            if score < min_suitability:
                continue

            if use_mask:
                if not bool(ring_mask[zs, xs]):
                    continue
            else:
                dist = math.hypot(xs - scx, zs - scz)
                if not (r_min <= dist <= r_max):
                    continue

            world_x, world_z = ctx.s2w(xs, zs)

            if road_score_map is not None:
                road_score = float(road_score_map[zs, xs])
            else:
                road_score = 0.5

            final_score = score + road_weight * road_score
            candidates.append((world_x, world_z, final_score))

    candidates.sort(key=lambda c: -c[2])
    return candidates[:top_k]


def compute_footprint_complexity(height_map: np.ndarray,
                                 sx0: int, sx1: int,
                                 sz0: int, sz1: int) -> dict:
    """计算 footprint 范围内的地形复杂度。

    Returns:
        std:   高度标准差，越小越平坦
        range: 最高减最低，高度跨度
        max_y: footprint 内最高点（地基柱对齐目标）
        min_y: footprint 内最低点
    """
    H, W = height_map.shape
    sx0 = max(0, sx0); sx1 = min(W - 1, sx1)
    sz0 = max(0, sz0); sz1 = min(H - 1, sz1)

    if sx0 > sx1 or sz0 > sz1:
        return {"std": 999.0, "range": 999, "max_y": 64, "min_y": 64}

    patch = height_map[sz0:sz1 + 1, sx0:sx1 + 1].astype(np.int32)

    return {
        "std":   float(np.std(patch)),
        "range": int(np.max(patch) - np.min(patch)),
        "max_y": int(np.max(patch)),
        "min_y": int(np.min(patch)),
    }
