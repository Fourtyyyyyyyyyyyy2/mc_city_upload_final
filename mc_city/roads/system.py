"""智能道路系统：骨架（环 + 放射）+ 建筑接入。

入口：
    generate_structural_roads(...) → backbone_nodes
    connect_buildings_to_nearest_road(...)
    （可选）generate_roads(...) 一体化，按 Delaunay+MST 连建筑

卡 4 顶层函数（不依赖 SmartRoadSystem 实例，便于 demo 直接调用）：
    select_backbone_endpoints(...) → 终点世界坐标
    plan_main_road_path(...)       → A* scan 路径
"""
import heapq
import math
from typing import Dict, List, Optional, Tuple

import numpy as np

from ..config import (
    NUM_BACKBONE_ENDPOINTS,
    ORGANIC_BACKBONE_ENABLED,
    ROAD_MIN_ENDPOINT_ANGLE_DEG,
    WALL_RADIUS,
    WALL_SHAPE,
)
from .collision import BuildingCollisionDetector
from .network import RoadNetworkGenerator
from .pathfinding import RoadPathfinder
from .renderer import RoadRenderer


def _square_loop(cx: int, cz: int, r: int) -> list:
    """方环路径点（顺时针闭合），半边长 = r。用于方城环城路。"""
    pts = []
    for x in range(cx - r, cx + r + 1):
        pts.append((x, cz - r))
    for z in range(cz - r + 1, cz + r + 1):
        pts.append((cx + r, z))
    for x in range(cx + r - 1, cx - r - 1, -1):
        pts.append((x, cz + r))
    for z in range(cz + r - 1, cz - r - 1, -1):
        pts.append((cx - r, z))
    return pts


# ══════════════════════════════════════════════════════════════════════
# 卡 4：主干道沿地形（顶层函数）
# ══════════════════════════════════════════════════════════════════════

def select_backbone_endpoints(center_xz: Tuple[int, int],
                              ring_masks,
                              features,
                              ctx=None,
                              num_endpoints: int = NUM_BACKBONE_ENDPOINTS,
                              min_angle_deg: float = ROAD_MIN_ENDPOINT_ANGLE_DEG,
                              ) -> List[Tuple[int, int]]:
    """从 outer 圈边缘挑 N 个主干道终点。

    硬约束：候选格必须在 outer mask 内，is_flat=True，非 water，valid。
    评分：距 center 越远越好（鼓励真正"外缘"）；只保留距离前 50% 的候选。
    贪心：按评分降序取，逐步放宽 min_angle 约束直到凑够 N 个。

    Args:
        center_xz:    (world_x, world_z) 城市中心。
        ring_masks:   RingMasks。
        features:     TerrainFeatures。
        ctx:          ScanContext；省略则 center_xz 直接当 scan 坐标用（demo 默认）。
        num_endpoints: 期望端点数。
        min_angle_deg: 相邻端点最小角度差。

    Returns:
        [(world_x, world_z), ...]。如果 outer mask 中没有合格候选，返回 []。
    """
    NZ, NX = ring_masks.outer.shape
    cx_w, cz_w = int(center_xz[0]), int(center_xz[1])
    if ctx is not None:
        cx_s, cz_s = ctx.w2s(cx_w, cz_w)
    else:
        cx_s, cz_s = cx_w, cz_w

    cand_mask = (ring_masks.outer
                 & features.is_flat
                 & (~features.is_water)
                 & features.valid_mask)

    zs_idx, xs_idx = np.where(cand_mask)
    if len(zs_idx) == 0:
        print(f"[BACKBONE] outer mask 中没有合格候选（is_flat & !water）", flush=True)
        return []

    dx = xs_idx.astype(np.float32) - cx_s
    dz = zs_idx.astype(np.float32) - cz_s
    dist = np.hypot(dx, dz)

    # 取距离前 50%（鼓励真正的边缘点）
    if len(dist) > num_endpoints * 4:
        thresh = float(np.percentile(dist, 50))
        keep = dist >= thresh
        zs_idx = zs_idx[keep]; xs_idx = xs_idx[keep]
        dx = dx[keep]; dz = dz[keep]; dist = dist[keep]

    angles = np.arctan2(dz, dx)
    max_dist = float(dist.max()) if dist.size > 0 else 1.0
    score = dist / max(max_dist, 1e-6)

    order = np.argsort(-score)

    selected_idx: list = []
    for relax_factor in (1.0, 0.5, 0.25, 0.0):
        threshold = min_angle_deg * relax_factor
        selected_idx = []
        for i in order:
            ang_i = float(angles[i])
            ok = True
            for j in selected_idx:
                ang_j = float(angles[j])
                d_ang = abs((ang_i - ang_j + math.pi) % (2 * math.pi) - math.pi)
                if math.degrees(d_ang) < threshold:
                    ok = False
                    break
            if ok:
                selected_idx.append(int(i))
                if len(selected_idx) >= num_endpoints:
                    break
        if len(selected_idx) >= num_endpoints:
            break

    endpoints: list = []
    for i in selected_idx:
        sx = int(xs_idx[i]); sz = int(zs_idx[i])
        if ctx is not None:
            wx, wz = ctx.s2w(sx, sz)
        else:
            wx, wz = sx, sz
        endpoints.append((int(wx), int(wz)))

    print(f"[BACKBONE] 选出 {len(endpoints)}/{num_endpoints} 个端点 "
          f"(min_angle 放宽到 {threshold:.1f}°)", flush=True)
    return endpoints


def plan_main_road_path(start_sxz: Tuple[int, int],
                        end_sxz: Tuple[int, int],
                        features,
                        ring_masks=None,
                        ) -> List[Tuple[int, int]]:
    """A* 寻路（scan 坐标）。cost 复用 city.rings._build_cost_grid。

    Args:
        start_sxz / end_sxz: (sx, sz) scan 坐标。
        features:           TerrainFeatures。
        ring_masks:         未使用（占位，便于 future 扩展为"路径只能在 all_city 内"）。

    Returns:
        路径 [(sx, sz), ...] 含端点。失败返回 []。
    """
    from ..city.rings import _build_cost_grid

    NZ, NX = features.height_map.shape
    sx0, sz0 = int(start_sxz[0]), int(start_sxz[1])
    sx1, sz1 = int(end_sxz[0]), int(end_sxz[1])
    if not (0 <= sx0 < NX and 0 <= sz0 < NZ):
        return []
    if not (0 <= sx1 < NX and 0 <= sz1 < NZ):
        return []

    cost = _build_cost_grid(features)
    if not np.isfinite(cost[sz0, sx0]) or not np.isfinite(cost[sz1, sx1]):
        return []

    INF = float("inf")
    g_score = np.full((NZ, NX), INF, dtype=np.float32)
    g_score[sz0, sx0] = 0.0
    came_from: dict = {}

    def heur(x: int, z: int) -> float:
        return math.hypot(x - sx1, z - sz1)  # 最小边权 = 1.0，admissible

    heap = [(heur(sx0, sz0), 0.0, sx0, sz0)]
    DX = (-1, 1, 0, 0)
    DZ = (0, 0, -1, 1)

    while heap:
        _f, g, x, z = heapq.heappop(heap)
        if (x, z) == (sx1, sz1):
            path = [(x, z)]
            while (x, z) in came_from:
                x, z = came_from[(x, z)]
                path.append((x, z))
            return path[::-1]
        if g > g_score[z, x]:
            continue
        for k in range(4):
            nx_, nz_ = x + DX[k], z + DZ[k]
            if not (0 <= nx_ < NX and 0 <= nz_ < NZ):
                continue
            c = cost[nz_, nx_]
            if not np.isfinite(c):
                continue
            ng = g + float(c)
            if ng < g_score[nz_, nx_]:
                g_score[nz_, nx_] = np.float32(ng)
                came_from[(nx_, nz_)] = (x, z)
                heapq.heappush(heap, (ng + heur(nx_, nz_), ng, nx_, nz_))

    return []


class SmartRoadSystem:

    def __init__(self,
                 max_slope: float = 0.5,
                 road_block: str = "minecraft:cobblestone",
                 road_width: int = 4,
                 use_astar: bool = True,
                 building_buffer: int = 5,
                 avoid_buildings: bool = True):
        self.network_gen = RoadNetworkGenerator(max_slope=max_slope)
        self._road_block = road_block
        self._road_width = road_width
        self.renderer = None
        self.use_astar = use_astar
        self.pathfinder = None
        self.max_slope = max_slope
        self.building_buffer = building_buffer
        self.avoid_buildings = avoid_buildings
        self.collision_detector = None
        self.origin_x = 0
        self.origin_z = 0
        self.min_y = -64

    # ──────────────────────────────────────────────────────────────
    # 环城路渲染（可在 [2] 早渲染，或 [9] 建筑后带 blocked_boxes 渲染）
    # ──────────────────────────────────────────────────────────────
    def render_ring_roads(self, center_x: int, center_z: int,
                          radius_map: dict,
                          height_map_original: np.ndarray,
                          origin_x: int = 0, origin_z: int = 0,
                          min_y: int = -64,
                          blocked_boxes: list = None) -> None:
        """渲染各圈层环城路（圆周）。blocked_boxes 给定时遇建筑列自动断开。"""
        NZ, NX = height_map_original.shape
        renderer = RoadRenderer(
            road_block="minecraft:cobblestone", road_width=5,
            height_map=height_map_original,
            origin_x=origin_x, origin_z=origin_z, min_y=min_y,
            blocked_boxes=blocked_boxes,
        )
        square = WALL_SHAPE == "square"
        for ring_name, (r_min, r_max) in radius_map.items():
            r_ring = (r_min + r_max) // 2
            if square and r_ring > WALL_RADIUS:        # 方城外的环不画
                print(f"    环形 {ring_name}：r={r_ring} 在方墙外，跳过")
                continue
            if square:
                pts = _square_loop(center_x, center_z, r_ring)
            else:
                num_pts = max(int(2 * math.pi * r_ring), 360)
                pts = [(int(center_x + r_ring * math.cos(2 * math.pi * i / num_pts)),
                        int(center_z + r_ring * math.sin(2 * math.pi * i / num_pts)))
                       for i in range(num_pts + 1)]
            path = []
            for wx, wz in pts:
                xs, zs = wx - origin_x, wz - origin_z
                if 0 <= xs < NX and 0 <= zs < NZ:
                    y = int(height_map_original[zs, xs])
                    path.append((wx, y, wz))
            if path:
                renderer.render_path(path)
            shape = "方环" if square else "圆环"
            print(f"    {shape} {ring_name}：r={r_ring}, 点数={len(path)}")

    # ──────────────────────────────────────────────────────────────
    # Phase 1：骨架道路（环 + 放射）
    # ──────────────────────────────────────────────────────────────
    def generate_structural_roads(self,
                                  center_x: int, center_z: int,
                                  radius_map: dict,
                                  height_map: np.ndarray,
                                  origin_x: int = 0,
                                  origin_z: int = 0,
                                  radial_count: int = 8,
                                  height_map_original: np.ndarray = None,
                                  features=None,
                                  ring_masks=None,
                                  ctx=None,
                                  render_radials: bool = True,
                                  render_rings: bool = True) -> list:
        """环形主干道 + 放射主干道。

        卡 4：传入 features + ring_masks + ctx 时走"沿地形蜿蜒"路径
        （select_backbone_endpoints + plan_main_road_path）。否则退回旧的
        等角度 radial_count 条直线放射。

        卡 9.6：render_radials=False 时只渲染环形（环城路），跳过放射道——
        放射被网格街道取代（中式棋盘）。backbone_nodes 仍照常返回（含环形节点）。

        返回 backbone_nodes = [(world_x, world_z), ...] 用于建筑选址评分。
        """
        if height_map_original is None:
            height_map_original = height_map

        self.origin_x = origin_x
        self.origin_z = origin_z
        # 同步 ctx.min_y 到 self.min_y，让后续 connect_buildings_to_nearest_road
        # 用 self.min_y 默认值时也能正确过滤 sentinel 列。
        if ctx is not None:
            self.min_y = int(ctx.min_y)

        NZ, NX = height_map.shape

        # 放射次干道渲染器（环形渲染抽到 render_ring_roads，便于延后到建筑之后）
        min_y = int(ctx.min_y) if ctx is not None else -64
        radial_renderer = RoadRenderer(
            road_block="minecraft:cobblestone", road_width=3,
            height_map=height_map_original,
            origin_x=origin_x, origin_z=origin_z,
            min_y=min_y,
        )

        organic_mode = (ORGANIC_BACKBONE_ENABLED
                        and features is not None
                        and ring_masks is not None
                        and ctx is not None)
        backbone_label = "有机蜿蜒" if organic_mode else f"{radial_count} 条等角度"
        print(f"  骨架道路：{len(radius_map)} 条环形 + 放射[{backbone_label}]")

        # 1) 环形道路。render_rings=False 时跳过——延后到 [9] 建筑之后渲染
        #    （避免被建筑覆盖；那次调用会带 blocked_boxes 让环路遇建筑自动断开）。
        if render_rings:
            self.render_ring_roads(center_x, center_z, radius_map,
                                   height_map_original, origin_x, origin_z, min_y)
        else:
            print("    [9.6] 环城路延后到 [9] 渲染（建筑之后，遇楼断开）")

        # 2) 放射道路：卡 4 有机路径 / 老的等角度
        # 卡 9.6：render_radials=False 时整段跳过（放射被网格街道取代）。
        max_r = max(r_max for _, (_, r_max) in radius_map.items())
        organic_paths: list = []  # [(wx, wz), ...] 列表的列表
        if not render_radials:
            print("    [9.6] 跳过放射道（网格街道取代），仅渲染环城路")
        else:
            if organic_mode:
                organic_paths = self._render_organic_radials(
                    center_x, center_z, radius_map, features, ring_masks, ctx,
                    height_map, height_map_original, radial_renderer,
                )
            if not organic_mode or not organic_paths:
                # 任何原因导致有机模式失败，整体回退等角度
                if organic_mode:
                    print("    ⚠️ 有机放射全部失败，回退等角度直线")
                for i in range(radial_count):
                    theta = 2 * math.pi * i / radial_count
                    path = []
                    for dist in range(0, max_r + 1, 1):
                        wx = int(center_x + dist * math.cos(theta))
                        wz = int(center_z + dist * math.sin(theta))
                        xs, zs = wx - origin_x, wz - origin_z
                        if 0 <= xs < NX and 0 <= zs < NZ:
                            y = int(height_map[zs, xs])
                            path.append((wx, y, wz))
                    if path:
                        radial_renderer.render_path(path)

        # 收集骨架节点（建筑选址评分用）
        backbone_nodes = []
        for ring_name, (r_min, r_max) in radius_map.items():
            r_ring = (r_min + r_max) // 2
            num_pts = max(int(2 * math.pi * r_ring / 4), 90)
            for i in range(num_pts):
                theta = 2 * math.pi * i / num_pts
                wx = int(center_x + r_ring * math.cos(theta))
                wz = int(center_z + r_ring * math.sin(theta))
                backbone_nodes.append((wx, wz))
        if organic_mode and organic_paths:
            # 有机路径上每 4 格采一次作为节点
            for path in organic_paths:
                for k in range(0, len(path), 4):
                    backbone_nodes.append(path[k])
        else:
            for i in range(radial_count):
                theta = 2 * math.pi * i / radial_count
                for dist in range(0, max_r + 1, 4):
                    wx = int(center_x + dist * math.cos(theta))
                    wz = int(center_z + dist * math.sin(theta))
                    backbone_nodes.append((wx, wz))

        print(f"  ✅ 骨架道路完成，节点数={len(backbone_nodes)}")
        return backbone_nodes

    def _render_organic_radials(self, center_x: int, center_z: int,
                                radius_map: dict, features, ring_masks, ctx,
                                height_map: np.ndarray,
                                height_map_original: np.ndarray,
                                renderer: "RoadRenderer") -> list:
        """卡 4：从 center 到选出的 N 个 outer 端点用 A* 寻路并渲染。

        单个端点寻路失败时该端点回退直线（不影响其它端点）。
        返回成功渲染的路径列表 [[(wx, wz), ...], ...]，全部失败时返回 []。
        """
        endpoints = select_backbone_endpoints(
            (center_x, center_z), ring_masks, features, ctx=ctx,
        )
        if not endpoints:
            return []

        scx, scz = ctx.w2s(center_x, center_z)
        NZ, NX = height_map.shape
        max_r = max(r_max for _, (_, r_max) in radius_map.items())
        rendered_paths: list = []

        for ep_idx, (ex, ez) in enumerate(endpoints):
            esx, esz = ctx.w2s(ex, ez)
            scan_path = plan_main_road_path((scx, scz), (esx, esz), features)
            label = f"端点{ep_idx + 1} world=({ex},{ez})"

            if not scan_path:
                # 兜底：单端点直线
                print(f"    {label}: A* 失败，回退直线", flush=True)
                dx, dz = ex - center_x, ez - center_z
                length = int(max(abs(dx), abs(dz)))
                world_path = []
                for t in range(length + 1):
                    f = t / max(length, 1)
                    wx = int(center_x + f * dx)
                    wz = int(center_z + f * dz)
                    xs, zs = wx - self.origin_x, wz - self.origin_z
                    if 0 <= xs < NX and 0 <= zs < NZ:
                        y = int(height_map[zs, xs])
                        world_path.append((wx, y, wz))
                if world_path:
                    renderer.render_path(world_path)
                    rendered_paths.append([(p[0], p[2]) for p in world_path])
                continue

            # scan → world (含 y from height_map)
            world_path = []
            for sx, sz in scan_path:
                wx, wz = ctx.s2w(sx, sz)
                if 0 <= sx < NX and 0 <= sz < NZ:
                    y = int(height_map[sz, sx])
                    world_path.append((int(wx), int(y), int(wz)))
            if world_path:
                renderer.render_path(world_path)
                rendered_paths.append([(p[0], p[2]) for p in world_path])
                print(f"    {label}: A* 成功，{len(scan_path)} 格", flush=True)

        return rendered_paths

    # ──────────────────────────────────────────────────────────────
    # Phase 2：建筑接入
    # ──────────────────────────────────────────────────────────────
    def connect_buildings_to_nearest_road(self,
                                          building_positions: List[Tuple[int, int, int]],
                                          height_map: np.ndarray,
                                          origin_x: int = 0,
                                          origin_z: int = 0,
                                          center_x: int = 0,
                                          center_z: int = 0,
                                          radius_map: dict = None,
                                          height_map_original: np.ndarray = None,
                                          node_spacing: int = 16,
                                          min_y: int = None):
        """每栋建筑用 A* 连接到最近骨架节点。A* 失败用直线兜底。"""
        if height_map_original is None:
            height_map_original = height_map
        if radius_map is None:
            print("  ⚠️ radius_map 未提供，跳过接入道路")
            return

        NZ, NX = height_map.shape
        if min_y is None:
            min_y = int(self.min_y)

        access_renderer = RoadRenderer(
            road_block="minecraft:dirt_path",
            road_width=2,
            height_map=height_map_original,
            origin_x=origin_x, origin_z=origin_z,
            min_y=int(min_y),
        )

        road_nodes = []
        max_r = max(r_max for _, (_, r_max) in radius_map.items())

        # 环形骨架节点
        for ring_name, (r_min, r_max) in radius_map.items():
            r_ring = (r_min + r_max) // 2
            circumference = int(2 * math.pi * r_ring)
            num_pts = max(circumference // node_spacing, 8)
            for i in range(num_pts):
                theta = 2 * math.pi * i / num_pts
                wx = int(center_x + r_ring * math.cos(theta))
                wz = int(center_z + r_ring * math.sin(theta))
                road_nodes.append((wx, wz))

        # 放射骨架节点
        radial_count = 8
        for i in range(radial_count):
            theta = 2 * math.pi * i / radial_count
            for dist in range(node_spacing, max_r + 1, node_spacing):
                wx = int(center_x + dist * math.cos(theta))
                wz = int(center_z + dist * math.sin(theta))
                road_nodes.append((wx, wz))

        if not road_nodes:
            print("  ⚠️ 骨架节点为空，跳过接入道路")
            return

        road_nodes_arr = np.array(road_nodes, dtype=np.float32)
        print(f"  骨架节点数: {len(road_nodes)}（间距 {node_spacing} 格）")

        success = 0
        for bx, by, bz in building_positions:
            dists = np.hypot(road_nodes_arr[:, 0] - bx, road_nodes_arr[:, 1] - bz)
            nearest_idx = int(np.argmin(dists))
            nx, nz = road_nodes[nearest_idx]

            if float(dists[nearest_idx]) < node_spacing // 2:
                continue

            nxs, nzs = nx - origin_x, nz - origin_z
            if 0 <= nxs < NX and 0 <= nzs < NZ:
                ny = int(height_map_original[nzs, nxs])
            else:
                ny = by

            path = None
            if self.use_astar and self.pathfinder is not None:
                path = self.pathfinder.find_path((bx, by, bz), (nx, ny, nz))

            if path is None:
                path = self._fallback_straight_path(
                    (bx, by, bz), (nx, ny, nz), height_map_original)

            if path:
                access_renderer.render_path(path)
                success += 1

        print(f"  ✅ 建筑接入：{success}/{len(building_positions)} 栋成功")

    # ──────────────────────────────────────────────────────────────
    # 备用：完整 Delaunay+MST 道路（含建筑-建筑连接）
    # ──────────────────────────────────────────────────────────────
    def generate_roads(self, building_positions: List[Tuple[int, int, int]],
                       height_map: np.ndarray,
                       scan_volume: np.ndarray,
                       building_info: Dict = None,
                       origin_x: int = 0, origin_z: int = 0, min_y: int = -64,
                       center_x: int = None, center_z: int = None,
                       radius_map: dict = None):
        self.origin_x = origin_x
        self.origin_z = origin_z
        self.min_y = min_y

        print("=== 智能道路系统启动 ===")
        print(f"建筑数量: {len(building_positions)}")

        if len(building_positions) < 2:
            print("建筑数量少于2个，跳过道路生成")
            return

        # Step 0: 碰撞检测
        if self.avoid_buildings:
            print("[0/4] 初始化建筑避障...")
            self.collision_detector = BuildingCollisionDetector(buffer_radius=self.building_buffer)
            self._setup_building_collisions(building_positions, building_info)
            print(f"已注册 {len(self.collision_detector.building_bounds)} 个建筑")
        else:
            print("[0/4] 跳过建筑避障...")

        # Step 1: 拓扑
        print("[1/4] 生成道路网络拓扑...")
        if center_x is not None and radius_map is not None:
            raw_edges = self._ring_constrained_edges(
                building_positions, height_map, scan_volume,
                center_x=center_x, center_z=center_z, radius_map=radius_map,
                origin_x=origin_x, origin_z=origin_z, min_y=min_y,
            )
            pos_to_idx = {pos: i for i, pos in enumerate(building_positions)}
            edges = [(pos_to_idx[a], pos_to_idx[b])
                     for a, b in raw_edges
                     if a in pos_to_idx and b in pos_to_idx]
        else:
            edges = self.network_gen.generate_road_graph(
                building_positions, height_map, scan_volume,
                origin_x=origin_x, origin_z=origin_z, min_y=min_y)

        print(f"生成 {len(edges)} 条道路边")
        if not edges:
            print("无法生成道路，拓扑连接失败")
            return

        # Step 2: A*
        if self.use_astar:
            print("[2/4] 初始化 A*...")
            self.pathfinder = RoadPathfinder(
                height_map, scan_volume,
                max_slope=self.max_slope,
                collision_detector=self.collision_detector,
                origin_x=origin_x, origin_z=origin_z, min_y=min_y,
            )
        else:
            print("[2/4] 使用直线连接模式...")

        self.renderer = RoadRenderer(
            road_block=self._road_block, road_width=self._road_width,
            height_map=height_map, origin_x=origin_x, origin_z=origin_z,
            min_y=int(self.min_y),
        )

        # Step 3: 渲染
        print("[3/4] 生成并渲染道路...")
        success_count = 0
        fallback_count = 0
        for idx, (i, j) in enumerate(edges):
            start = building_positions[i]
            goal = building_positions[j]
            path = None
            if self.use_astar:
                path = self.pathfinder.find_path(start, goal)
                if path is None:
                    print(f"  道路 {idx+1}/{len(edges)}: A* 失败，回退到直线")
                    path = self._fallback_straight_path(start, goal, height_map)
                    fallback_count += 1
            else:
                path = self._fallback_straight_path(start, goal, height_map)

            if path:
                self.renderer.render_path(path)
                success_count += 1

        print(f"[4/4] 完成。成功: {success_count}, 回退: {fallback_count}")

    # ──────────────────────────────────────────────────────────────
    # 内部辅助
    # ──────────────────────────────────────────────────────────────
    def _assign_rings(self, building_positions, center_x, center_z, radius_map):
        ring_of = {}
        for pos in building_positions:
            x, _, z = pos
            r = math.hypot(x - center_x, z - center_z)
            assigned = "outer"
            for ring, (r_min, r_max) in radius_map.items():
                if r_min <= r <= r_max:
                    assigned = ring
                    break
            ring_of[pos] = assigned
        return ring_of

    def _ring_constrained_edges(self, building_positions, height_map, scan_volume,
                                center_x, center_z, radius_map,
                                origin_x=0, origin_z=0, min_y=-64):
        """每个圈层内 MST，再用辐条把相邻圈层最靠中心的建筑串起来。"""
        ring_of = self._assign_rings(building_positions, center_x, center_z, radius_map)
        rings: dict = {}
        for pos, ring in ring_of.items():
            rings.setdefault(ring, []).append(pos)

        all_edges = []
        for ring_name, members in rings.items():
            if len(members) < 2:
                continue
            intra_edges = self.network_gen.generate_road_graph(
                members, height_map, scan_volume,
                origin_x=origin_x, origin_z=origin_z, min_y=min_y)
            for i, j in intra_edges:
                all_edges.append((members[i], members[j]))

        prev_closest = None
        for ring_name in ("outer", "mid", "inner"):
            members = rings.get(ring_name, [])
            if not members:
                continue
            closest = min(members,
                          key=lambda p: math.hypot(p[0] - center_x, p[2] - center_z))
            if prev_closest is not None:
                all_edges.append((closest, prev_closest))
            prev_closest = closest

        return all_edges

    def _setup_building_collisions(self, building_positions, building_info):
        if not self.collision_detector:
            return
        if building_info and "npy_paths" in building_info:
            success = 0
            for pos, npy_path in zip(building_positions, building_info["npy_paths"]):
                try:
                    self.collision_detector.add_building_from_origin(pos, npy_path)
                    success += 1
                except Exception as e:
                    print(f"加载失败 {npy_path}: {e}")
            print(f"  已加载 {success}/{len(building_info['npy_paths'])} 个建筑文件")
        elif building_info and "sizes" in building_info:
            sizes = building_info["sizes"]
            for pos, sx, sz, sy in zip(building_positions,
                                        sizes["x"], sizes["z"],
                                        sizes.get("y", [20] * len(building_positions))):
                self.collision_detector.add_building_from_size(pos, sx, sz, sy)
        else:
            for pos in building_positions:
                self.collision_detector.add_building_from_size(pos, 20, 20, 15)

    def _fallback_straight_path(self, start, goal, height_map):
        x1, y1, z1 = start
        x2, y2, z2 = goal
        length = int(max(abs(x2 - x1), abs(z2 - z1)))
        if length == 0:
            return [(x1, y1, z1)]
        path = []
        for i in range(length + 1):
            t = i / max(length, 1)
            x = int(x1 + t * (x2 - x1))
            z = int(z1 + t * (z2 - z1))
            NZ, NX = height_map.shape
            xs = x - self.origin_x
            zs = z - self.origin_z
            if 0 <= xs < NX and 0 <= zs < NZ:
                y = int(height_map[zs, xs])
                path.append((x, y, z))
        return path
