"""圈层有机生长（Priority 0 卡 3）。

把 inner/mid/outer 圈层从"同心圆判定 (r < R)"改为
"从中心 Dijkstra 扩展，累计面积分三圈"。

边权 = 1 + 地形惩罚（陡坡 / 起伏）。水域是 inf（永远绕开）。sentinel
也视为 inf。这样：
  - 城市形状沿可建地形生长 → 山脚下变扇形、湖边变 D 字形、谷地变带状
  - 水永远不会被纳入圈层
  - inner 始终被 mid 包围，mid 始终被 outer 包围（连通性由 BFS 本性保证）

公开 API：
    grow_organic_rings(ctx, features, center_xz, target_areas) → RingMasks
    get_ring_from_masks(sx, sz, masks) → "inner"/"mid"/"outer"/None
"""
from __future__ import annotations

import heapq
from dataclasses import dataclass
from typing import Optional

import numpy as np

from ..config import (
    ROUGHNESS_COST_THRESHOLD,
    SLOPE_COST_GENTLE,
    SLOPE_COST_STEEP,
    TARGET_AREAS,
    TERRAIN_COST_FOR_GENTLE,
    TERRAIN_COST_FOR_ROUGH,
    TERRAIN_COST_FOR_STEEP,
)


@dataclass
class RingMasks:
    """三圈层 mask 数据集，形状 (NZ, NX) 与 height_map 同。

    所有 mask 互斥（一格不会同时属于多个圈）。all_city = inner | mid | outer。
    distance_map：每格到中心的"地形友好距离"。未到达的格 = inf（np.float32）。
    """
    inner: np.ndarray         # bool
    mid: np.ndarray           # bool
    outer: np.ndarray         # bool
    all_city: np.ndarray      # bool, = inner | mid | outer
    distance_map: np.ndarray  # float32, 未到达 = inf


# ─────────────────────────────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────────────────────────────

def grow_organic_rings(ctx,
                       features,
                       center_xz: tuple,
                       target_areas: Optional[dict] = None) -> RingMasks:
    """从中心 Dijkstra 扩展，按累计扩展顺序切成三圈。

    Args:
        ctx:          ScanContext。用于 world→scan 转换。
        features:     TerrainFeatures。算边权用。
        center_xz:    (world_x, world_z) 中心点。
        target_areas: {"inner": int, "mid": int, "outer": int}；
                      默认读 config.TARGET_AREAS。

    Returns:
        RingMasks。如果中心格本身就在水里 / sentinel 上，会回退用圆形
        判定（_circular_rings）兜底——这种情况下选址环节本应已经被卡 2 的
        硬约束排除，到这里说明上游异常，打印 warning 并提供可用 mask。
    """
    if target_areas is None:
        target_areas = TARGET_AREAS

    NZ, NX = features.height_map.shape
    cx_w, cz_w = center_xz
    cx, cz = ctx.w2s(int(cx_w), int(cz_w))

    if not (0 <= cx < NX and 0 <= cz < NZ):
        print(f"[RINGS] 中心 ({cx},{cz}) 在 scan 范围外，回退 circular", flush=True)
        return _circular_rings(ctx, features, (cx_w, cz_w), target_areas)

    cost_grid = _build_cost_grid(features)

    if not np.isfinite(cost_grid[cz, cx]):
        # 中心本身不可达——上游应防御，这里走兜底
        print(f"[RINGS] 中心 ({cx},{cz}) cost=inf（水/sentinel），回退 circular",
              flush=True)
        return _circular_rings(ctx, features, (cx_w, cz_w), target_areas)

    total_target = int(target_areas["inner"]) + int(target_areas["mid"]) \
                   + int(target_areas["outer"])

    # ── Dijkstra：堆 + 4 邻居 ─────────────────────────────────────
    INF = np.float32(np.inf)
    distance = np.full((NZ, NX), INF, dtype=np.float32)
    distance[cz, cx] = 0.0
    visited = np.zeros((NZ, NX), dtype=bool)

    # 记录扩展顺序——切圈层用
    visit_order_z = np.empty(total_target, dtype=np.int32)
    visit_order_x = np.empty(total_target, dtype=np.int32)
    n_visited = 0

    heap: list = [(0.0, int(cz), int(cx))]
    DZ = (-1, 1, 0, 0)
    DX = (0, 0, -1, 1)

    while heap and n_visited < total_target:
        d, z, x = heapq.heappop(heap)
        if visited[z, x]:
            continue
        visited[z, x] = True
        visit_order_z[n_visited] = z
        visit_order_x[n_visited] = x
        n_visited += 1

        for k in range(4):
            nz = z + DZ[k]
            nx = x + DX[k]
            if not (0 <= nz < NZ and 0 <= nx < NX):
                continue
            if visited[nz, nx]:
                continue
            c = cost_grid[nz, nx]
            if not np.isfinite(c):
                continue
            nd = d + float(c)
            if nd < distance[nz, nx]:
                distance[nz, nx] = np.float32(nd)
                heapq.heappush(heap, (nd, int(nz), int(nx)))

    if n_visited == 0:
        print("[RINGS] Dijkstra 没扩展出任何格子（中心被孤立？）", flush=True)
        return _circular_rings(ctx, features, (cx_w, cz_w), target_areas)

    # ── 按访问顺序切成三段 ───────────────────────────────────────
    # 充足时按 spec target；不足时按 target 比例分，保证 outer 不会被前两圈吃光。
    # 历史 bug：center 周围被水围时 n_visited < target_total，旧的"inner/mid
    # 占满 target、剩下都给 outer"会让 outer=0（见日志 inner=18850/mid=66060/outer=0）。
    total_t = int(target_areas["inner"]) + int(target_areas["mid"]) \
              + int(target_areas["outer"])
    if n_visited >= total_t:
        n_inner = int(target_areas["inner"])
        n_mid = int(target_areas["mid"])
        n_outer = n_visited - n_inner - n_mid
    else:
        # 按比例分（floor，最后把舍入余数给 outer 不丢格）
        n_inner = int(n_visited * int(target_areas["inner"]) / total_t)
        n_mid = int(n_visited * int(target_areas["mid"]) / total_t)
        n_outer = n_visited - n_inner - n_mid
        print(f"[RINGS] n_visited={n_visited} < target={total_t}, "
              f"按比例分: inner={n_inner} mid={n_mid} outer={n_outer}",
              flush=True)

    inner = np.zeros((NZ, NX), dtype=bool)
    mid = np.zeros((NZ, NX), dtype=bool)
    outer = np.zeros((NZ, NX), dtype=bool)

    inner[visit_order_z[:n_inner], visit_order_x[:n_inner]] = True
    if n_mid > 0:
        s, e = n_inner, n_inner + n_mid
        mid[visit_order_z[s:e], visit_order_x[s:e]] = True
    if n_outer > 0:
        s, e = n_inner + n_mid, n_inner + n_mid + n_outer
        outer[visit_order_z[s:e], visit_order_x[s:e]] = True

    print(f"[RINGS] organic 生长：inner={int(inner.sum())} mid={int(mid.sum())} "
          f"outer={int(outer.sum())} 总扩展={n_visited}/{total_target}",
          flush=True)

    return RingMasks(
        inner=inner, mid=mid, outer=outer,
        all_city=(inner | mid | outer),
        distance_map=distance,
    )


# ─────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────

def terrain_cost(features, sx: int, sz: int) -> float:
    """单格地形通过代价。water/sentinel = inf；其他按 slope/roughness 累加。"""
    if not features.valid_mask[sz, sx]:
        return float("inf")
    if features.is_water[sz, sx]:
        return float("inf")
    cost = 1.0
    slope = float(features.slope_map[sz, sx])
    if slope > SLOPE_COST_STEEP:
        cost += TERRAIN_COST_FOR_STEEP
    elif slope > SLOPE_COST_GENTLE:
        cost += TERRAIN_COST_FOR_GENTLE
    if float(features.roughness_map[sz, sx]) > ROUGHNESS_COST_THRESHOLD:
        cost += TERRAIN_COST_FOR_ROUGH
    return cost


def _build_cost_grid(features) -> np.ndarray:
    """矢量化构建整张 cost 图，比 grow_organic_rings 内逐格调 terrain_cost 快。"""
    NZ, NX = features.height_map.shape
    cost = np.full((NZ, NX), 1.0, dtype=np.float32)

    slope = features.slope_map
    rough = features.roughness_map
    steep = slope > SLOPE_COST_STEEP
    gentle = (slope > SLOPE_COST_GENTLE) & (~steep)
    rough_mask = rough > ROUGHNESS_COST_THRESHOLD

    cost[steep] += TERRAIN_COST_FOR_STEEP
    cost[gentle] += TERRAIN_COST_FOR_GENTLE
    cost[rough_mask] += TERRAIN_COST_FOR_ROUGH

    # 水 + sentinel = inf
    cost[features.is_water] = np.inf
    cost[~features.valid_mask] = np.inf
    return cost


def get_ring_from_masks(sx: int, sz: int,
                        masks: RingMasks) -> Optional[str]:
    """快速查 scan(sx, sz) 属于哪个圈。越界返回 None。"""
    NZ, NX = masks.inner.shape
    if not (0 <= sx < NX and 0 <= sz < NZ):
        return None
    if masks.inner[sz, sx]:
        return "inner"
    if masks.mid[sz, sx]:
        return "mid"
    if masks.outer[sz, sx]:
        return "outer"
    return None


# ─────────────────────────────────────────────────────────────────
# 兜底：圆形 ring（保留供任务卡 CONSTRAINTS 要求的"_circular"路径）
# ─────────────────────────────────────────────────────────────────

def _circular_rings(ctx,
                    features,
                    center_xz: tuple,
                    target_areas: dict,
                    radius_map: Optional[dict] = None) -> RingMasks:
    """旧的同心圆判定，作 grow_organic_rings 失败时的兜底。

    优先用 config.RADIUS_MAP，但允许调用方显式传 radius_map。
    与有机版本不同：water/sentinel 仍然包含在内（mask 不过滤地形）。
    """
    from ..config import RADIUS_MAP
    rmap = radius_map if radius_map is not None else RADIUS_MAP

    NZ, NX = features.height_map.shape
    cx_w, cz_w = center_xz
    cx, cz = ctx.w2s(int(cx_w), int(cz_w))

    zs_grid, xs_grid = np.indices((NZ, NX), dtype=np.float32)
    dist = np.sqrt((xs_grid - cx) ** 2 + (zs_grid - cz) ** 2)

    def _band(name: str) -> np.ndarray:
        r_min, r_max = rmap[name]
        return (dist >= r_min) & (dist <= r_max)

    inner = _band("inner")
    mid = _band("mid")
    outer = _band("outer")
    # 互斥：mid 减 inner、outer 减 mid（圆形重叠的部分给更内圈）
    mid = mid & (~inner)
    outer = outer & (~inner) & (~mid)

    print(f"[RINGS][CIRCULAR] inner={int(inner.sum())} mid={int(mid.sum())} "
          f"outer={int(outer.sum())}", flush=True)

    return RingMasks(
        inner=inner, mid=mid, outer=outer,
        all_city=(inner | mid | outer),
        distance_map=dist.astype(np.float32),
    )
