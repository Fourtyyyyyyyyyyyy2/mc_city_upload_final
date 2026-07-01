"""建筑碰撞检测：记录每栋建筑的包围盒（含缓冲区），供 A* 寻路查询。"""
from typing import List, Set, Tuple

import numpy as np


class BuildingCollisionDetector:

    def __init__(self, buffer_radius: int = 5):
        """buffer_radius: 建筑周围的缓冲半径，保证道路不会贴脸。"""
        self.buffer_radius = buffer_radius
        # (x1, z1, x2, z2, y_min, y_max)
        self.building_bounds: List[Tuple[int, int, int, int, int, int]] = []
        self.building_centers: List[Tuple[int, int, int]] = []

    def add_building_from_origin(self, origin: Tuple[int, int, int], npy_path: str) -> None:
        """从建筑 .npy 自动算尺寸 + 加缓冲区注册边界。"""
        try:
            volume = np.load(npy_path, allow_pickle=True)
            if volume.size == 0:
                return
            ny, nz, nx = volume.shape
            self._add_box(origin, nx, nz, ny)
        except Exception as e:
            print(f"无法加载建筑文件 {npy_path}: {e}")
            self.add_building_from_size(origin, 20, 20, 15)

    def add_building_from_size(self, origin: Tuple[int, int, int],
                               size_x: int, size_z: int, size_y: int = 20) -> None:
        """直接用指定尺寸注册（不读 .npy）。"""
        self._add_box(origin, size_x, size_z, size_y)

    def _add_box(self, origin: Tuple[int, int, int],
                 size_x: int, size_z: int, size_y: int) -> None:
        x_origin, y_origin, z_origin = origin
        x_min = x_origin - self.buffer_radius
        x_max = x_origin + size_x + self.buffer_radius
        z_min = z_origin - self.buffer_radius
        z_max = z_origin + size_z + self.buffer_radius
        y_min = y_origin
        y_max = y_origin + size_y
        self.building_bounds.append((x_min, z_min, x_max, z_max, y_min, y_max))
        self.building_centers.append(origin)

    def is_point_in_building(self, x: int, z: int, y: int = None) -> bool:
        """点是否在任意建筑的占用范围内（含缓冲）。提供 y 则做 3D 检查。"""
        for x_min, z_min, x_max, z_max, y_min, y_max in self.building_bounds:
            if x_min <= x <= x_max and z_min <= z <= z_max:
                if y is None or y_min <= y <= y_max:
                    return True
        return False

    def get_blocked_positions(self, height_map: np.ndarray) -> Set[Tuple[int, int]]:
        """枚举所有被建筑占用的 (x, z)。"""
        blocked = set()
        for x_min, z_min, x_max, z_max, _, _ in self.building_bounds:
            for x in range(max(0, x_min), min(height_map.shape[1], x_max + 1)):
                for z in range(max(0, z_min), min(height_map.shape[0], z_max + 1)):
                    blocked.add((x, z))
        return blocked

    def get_nearest_valid_point(self, x: int, z: int,
                                search_radius: int = 10) -> Tuple[int, int]:
        """从 (x, z) 螺旋向外找最近的非建筑点。"""
        if not self.is_point_in_building(x, z):
            return (x, z)
        for radius in range(1, search_radius + 1):
            for dx in range(-radius, radius + 1):
                for dz in range(-radius, radius + 1):
                    if max(abs(dx), abs(dz)) != radius:
                        continue
                    nx, nz = x + dx, z + dz
                    if not self.is_point_in_building(nx, nz):
                        return (nx, nz)
        return (x, z)

    def get_distance_to_nearest_building(self, x: int, z: int) -> float:
        """到最近建筑的曼哈顿/欧式距离（点在矩形内返回 0）。"""
        if not self.building_bounds:
            return float('inf')
        min_dist = float('inf')
        for x_min, z_min, x_max, z_max, _, _ in self.building_bounds:
            if x_min <= x <= x_max and z_min <= z <= z_max:
                dist = 0.0
            else:
                dx = max(x_min - x, 0, x - x_max)
                dz = max(z_min - z, 0, z - z_max)
                dist = (dx ** 2 + dz ** 2) ** 0.5
            min_dist = min(min_dist, dist)
        return min_dist

    def clear(self):
        self.building_bounds.clear()
        self.building_centers.clear()
