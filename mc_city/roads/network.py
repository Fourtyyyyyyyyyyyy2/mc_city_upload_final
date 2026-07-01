"""道路网络拓扑：Delaunay 三角剖分 + 最小生成树。

输入建筑位置列表，输出 MST 边的索引对。
权重 = 基础距离 × 惩罚（陡坡 ×10，水域 ×20，越界 inf）。
"""
import math
from typing import List, Set, Tuple

import numpy as np
from scipy.spatial import Delaunay
from scipy.sparse.csgraph import minimum_spanning_tree


class RoadNetworkGenerator:

    def __init__(self, max_slope: float = 0.5,
                 water_blocks: Set[str] = None):
        self.max_slope = max_slope
        self.water_blocks = water_blocks or {
            "minecraft:water", "minecraft:flowing_water",
            "minecraft:lava", "minecraft:flowing_lava",
        }

    def generate_road_graph(self, building_positions: List[Tuple[int, int, int]],
                            height_map: np.ndarray,
                            scan_volume: np.ndarray,
                            origin_x: int = 0,
                            origin_z: int = 0,
                            min_y: int = -64) -> List[Tuple[int, int]]:
        """返回 MST 边索引列表 [(i, j), ...]。建筑数 < 2 直接返回 []。"""
        if len(building_positions) < 2:
            return []

        points_2d = np.array([(x, z) for x, _, z in building_positions])
        tri = Delaunay(points_2d)

        weight_matrix = self._build_weighted_graph(
            building_positions, tri, height_map, scan_volume,
            origin_x=origin_x, origin_z=origin_z, min_y=min_y,
        )

        mst = minimum_spanning_tree(weight_matrix)
        return self._extract_edges_from_mst(mst)

    def _build_weighted_graph(self, positions, tri,
                              height_map, scan_volume,
                              origin_x=0, origin_z=0, min_y=-64) -> np.ndarray:
        n = len(positions)
        weights = np.full((n, n), np.inf)

        for simplex in tri.simplices:
            for i in range(3):
                idx1, idx2 = simplex[i], simplex[(i + 1) % 3]
                cost = self._edge_cost(positions[idx1], positions[idx2],
                                       height_map, scan_volume,
                                       origin_x, origin_z, min_y)
                weights[idx1, idx2] = cost
                weights[idx2, idx1] = cost
        return weights

    def _edge_cost(self, pos1, pos2, height_map, scan_volume,
                   origin_x, origin_z, min_y) -> float:
        x1, _, z1 = pos1
        x2, _, z2 = pos2

        base_distance = math.hypot(x2 - x1, z2 - z1)
        samples = max(int(base_distance / 5), 3)
        penalty = 1.0

        for i in range(samples + 1):
            t = i / max(samples, 1)
            x = int(x1 + t * (x2 - x1))
            z = int(z1 + t * (z2 - z1))

            NZ, NX = height_map.shape
            xs, zs = x - origin_x, z - origin_z
            if not (0 <= zs < NZ and 0 <= xs < NX):
                return np.inf

            if i > 0:
                prev_x = int(x1 + (i - 1) / max(samples, 1) * (x2 - x1))
                prev_z = int(z1 + (i - 1) / max(samples, 1) * (z2 - z1))
                prev_xs, prev_zs = prev_x - origin_x, prev_z - origin_z

                curr_y = height_map[zs, xs]
                prev_y = height_map[prev_zs, prev_xs]
                dy = abs(curr_y - prev_y)
                dx = math.hypot(x - prev_x, z - prev_z)
                if dx > 0 and dy / dx > self.max_slope:
                    penalty *= 10.0

            y = int(height_map[zs, xs])
            if self._is_water_at(x, y, z, scan_volume, origin_x, origin_z, min_y):
                penalty *= 20.0

        return base_distance * penalty

    def _is_water_at(self, x, y_world, z, scan_volume,
                     origin_x, origin_z, min_y) -> bool:
        NY, NZ, NX = scan_volume.shape
        xs, zs = x - origin_x, z - origin_z
        if not (0 <= xs < NX and 0 <= zs < NZ):
            return False
        for dy in range(-1, 2):
            y_scan = (y_world + dy) - min_y
            if not (0 <= y_scan < NY):
                continue
            block = scan_volume[y_scan, zs, xs]
            block_id = self._extract_block_id(block)
            if block_id in self.water_blocks:
                return True
        return False

    @staticmethod
    def _extract_block_id(block) -> str:
        if isinstance(block, dict):
            return block.get("id", "minecraft:air")
        return str(block)

    @staticmethod
    def _extract_edges_from_mst(mst) -> List[Tuple[int, int]]:
        coo = mst.tocoo()
        edges = []
        seen = set()
        for i, j in zip(coo.row, coo.col):
            edge = tuple(sorted([int(i), int(j)]))
            if edge not in seen:
                edges.append(edge)
                seen.add(edge)
        return edges
