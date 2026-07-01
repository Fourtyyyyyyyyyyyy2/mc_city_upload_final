"""A* 寻路：避陡坡、避水、避建筑。XZ 平面寻路，Y 从 height_map 取。"""
import heapq
import math
from typing import Dict, List, Optional, Tuple

import numpy as np


class RoadPathfinder:

    def __init__(self, height_map: np.ndarray,
                 scan_volume: np.ndarray,
                 max_slope: float = 0.5,
                 water_blocks: set = None,
                 collision_detector=None,
                 origin_x: int = 0,
                 origin_z: int = 0,
                 min_y: int = -64):
        self.height_map = height_map
        self.scan_volume = scan_volume
        self.max_slope = max_slope
        self.water_blocks = water_blocks or {
            "minecraft:water", "minecraft:flowing_water",
            "minecraft:lava", "minecraft:flowing_lava",
        }
        self.collision_detector = collision_detector
        self.origin_x = origin_x
        self.origin_z = origin_z
        self.min_y = min_y

    def _hm(self, x: int, z: int):
        """世界坐标查 height_map，越界返回 None。"""
        xs, zs = x - self.origin_x, z - self.origin_z
        NZ, NX = self.height_map.shape
        if 0 <= xs < NX and 0 <= zs < NZ:
            return int(self.height_map[zs, xs])
        return None

    def find_path(self, start: Tuple[int, int, int],
                  goal: Tuple[int, int, int]) -> Optional[List[Tuple[int, int, int]]]:
        """A*。失败时返回 None 或部分路径（节点数 > 5）。"""
        start_2d = (start[0], start[2])
        goal_2d = (goal[0], goal[2])

        open_set = []
        heapq.heappush(open_set, (0, start_2d))

        came_from: Dict[Tuple[int, int], Tuple[int, int]] = {}
        g_score: Dict[Tuple[int, int], float] = {start_2d: 0}
        f_score: Dict[Tuple[int, int], float] = {start_2d: self._heuristic(start_2d, goal_2d)}
        closed_set = set()

        MAX_NODES = 200000
        nodes_visited = 0

        while open_set:
            _, current = heapq.heappop(open_set)
            nodes_visited += 1
            if nodes_visited > MAX_NODES:
                if open_set:
                    _, best_so_far = min(open_set, key=lambda x: x[0])
                    partial = self._reconstruct_path_3d(came_from, best_so_far)
                    if partial and len(partial) > 5:
                        return partial
                return None

            if current in closed_set:
                continue
            closed_set.add(current)

            if current == goal_2d:
                return self._reconstruct_path_3d(came_from, current)

            for neighbor in self._get_neighbors(current):
                if neighbor in closed_set:
                    continue
                move_cost = self._calculate_move_cost(current, neighbor)
                if move_cost == np.inf:
                    continue
                tentative_g = g_score[current] + move_cost
                if neighbor not in g_score or tentative_g < g_score[neighbor]:
                    came_from[neighbor] = current
                    g_score[neighbor] = tentative_g
                    f_score[neighbor] = tentative_g + self._heuristic(neighbor, goal_2d)
                    heapq.heappush(open_set, (f_score[neighbor], neighbor))

        return None

    def _get_neighbors(self, pos: Tuple[int, int]) -> List[Tuple[int, int]]:
        x, z = pos
        NZ, NX = self.height_map.shape
        neighbors = []
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1):
                if dx == 0 and dz == 0:
                    continue
                nx, nz = x + dx, z + dz
                if 0 <= nx - self.origin_x < NX and 0 <= nz - self.origin_z < NZ:
                    neighbors.append((nx, nz))
        return neighbors

    def _calculate_move_cost(self, pos1: Tuple[int, int],
                             pos2: Tuple[int, int]) -> float:
        x1, z1 = pos1
        x2, z2 = pos2
        base_cost = math.hypot(x2 - x1, z2 - z1)

        y1 = self._hm(x1, z1)
        y2 = self._hm(x2, z2)
        if y1 is None or y2 is None:
            return np.inf

        if self.collision_detector and self.collision_detector.is_point_in_building(x2, z2):
            return np.inf

        height_diff = abs(y2 - y1)
        if base_cost > 0:
            slope = height_diff / base_cost
            if slope > self.max_slope:
                base_cost *= 10.0

        if self._is_water_at(x2, y2, z2):
            base_cost *= 50.0

        return base_cost

    def _is_water_at(self, x: int, y_world: int, z: int) -> bool:
        NY, NZ, NX = self.scan_volume.shape
        xs, zs = x - self.origin_x, z - self.origin_z
        if not (0 <= xs < NX and 0 <= zs < NZ):
            return False
        for dy in range(-1, 2):
            y_scan = (y_world + dy) - self.min_y
            if not (0 <= y_scan < NY):
                continue
            block = self.scan_volume[y_scan, zs, xs]
            block_id = self._extract_block_id(block)
            if block_id in self.water_blocks:
                return True
        return False

    def _extract_block_id(self, block) -> str:
        if isinstance(block, (int, np.integer)):
            if hasattr(self, '_codec') and self._codec is not None:
                return self._codec.decode(int(block))
            return "minecraft:air"
        if isinstance(block, dict):
            bid = block.get("id") or block.get("name") or "minecraft:air"
        else:
            bid = str(block)
        if "[" in bid:
            bid = bid.split("[", 1)[0]
        return bid

    @staticmethod
    def _heuristic(pos1: Tuple[int, int], pos2: Tuple[int, int]) -> float:
        return math.hypot(pos2[0] - pos1[0], pos2[1] - pos1[1])

    def _reconstruct_path_3d(self,
                             came_from: Dict[Tuple[int, int], Tuple[int, int]],
                             current: Tuple[int, int]) -> List[Tuple[int, int, int]]:
        path_2d = [current]
        while current in came_from:
            current = came_from[current]
            path_2d.append(current)
        path_2d.reverse()

        path_3d = []
        for x, z in path_2d:
            y = self._hm(x, z)
            if y is not None:
                path_3d.append((x, y, z))
        return path_3d
