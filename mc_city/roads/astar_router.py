"""A* 路网：GDMC 标准做法，替代"笔直主道 + 找坡/削填/架桥"。

为什么换：旧渲染器假设任意两点间必须有一条笔直路，于是在陡地形上反复
削填架桥 → 22 格断崖、满路 cobblestone_wall 栏杆（用户图 101/213）。
GDMC 标准（见 arXiv 2103.14950 / Temple 报告）：在高度图上做加权 A*，
高差大的边惩罚/不可通行 → 路自动绕缓坡、走不通就不修（宁缺）。

本模块只算几何：高度修正 + A* + 生长网络，返回 scan 坐标路径。
渲染（drape）与 builder 接入在别处，按 ROAD_SYSTEM flag 切换。不碰 HTTP / 禁区。
"""
from __future__ import annotations

import heapq

import numpy as np

from ..mc.blocks import is_tree_block_id
from ..mc.codec import BlockCodec

# 算"可走地面"时跳过的雪/冰盖（落到下面实体），消除雪盖 1-4 格参差给路的噪声。
_GROUND_SKIP_BLOCKS = {
    "minecraft:snow", "minecraft:powder_snow", "minecraft:snow_block",
    "minecraft:ice", "minecraft:packed_ice", "minecraft:blue_ice",
    "minecraft:frosted_ice",
}

# 8 邻接（dz, dx, 步距）。直走 1，斜走 √2。
_NEIGHBORS = (
    (-1, 0, 1.0), (1, 0, 1.0), (0, -1, 1.0), (0, 1, 1.0),
    (-1, -1, 1.41421), (-1, 1, 1.41421), (1, -1, 1.41421), (1, 1, 1.41421),
)


def build_ground_height(scan_volume: np.ndarray,
                        codec: BlockCodec,
                        min_y: int) -> np.ndarray:
    """重算"可走地面"高度：跳过空气/树/雪冰盖，取每列最高实体块的世界 Y。

    与 scan/height_map.py 同思路（向量化 argmax），但额外跳过雪/冰盖 → 路面落在
    雪下的实土/石上，路不再被参差雪盖抬高。无效列（全跳过）保留 sentinel = min_y。
    不修改 height_map.py（禁区），这是道路专用的平行高度。
    """
    NY, NZ, NX = scan_volume.shape
    skip_codes = {int(codec.AIR_CODE)}
    for name, code in codec.name_to_code.items():
        base = name.split("[", 1)[0]
        if base in _GROUND_SKIP_BLOCKS or is_tree_block_id(base):
            skip_codes.add(int(code))

    is_solid = ~np.isin(scan_volume, list(skip_codes))      # (NY,NZ,NX)
    rev = is_solid[::-1, :, :]
    has_surface = rev.any(axis=0)
    top_rev = np.argmax(rev, axis=0)                        # 反向第一个实体
    top_y = (NY - 1 - top_rev)

    height = np.full((NZ, NX), min_y, dtype=np.int32)       # sentinel
    height[has_surface] = top_y[has_surface] + min_y
    return height


def _astar(height: np.ndarray,
           start: tuple[int, int],
           goal_mask: np.ndarray,
           passable: np.ndarray,
           min_y: int,
           hard_step: int,
           step_penalty: float,
           bounds: tuple[int, int, int, int]) -> list[tuple[int, int]] | None:
    """从 start (z,x) Dijkstra 到 goal_mask 任一 True 格，返回 scan 路径或 None。

    多目标 → 启发式取 0（Dijkstra），保证最短且实现简单。
    边规则：目标列高度 sentinel/不可通行 → 跳过；|Δh| > hard_step → 不可通行（绕开陡崖）；
    代价 = 步距 + step_penalty·|Δh|（鼓励走平、少爬升）。
    """
    NZ, NX = height.shape
    z0min, z0max, x0min, x0max = bounds
    sz, sx = start
    open_heap: list[tuple[float, int, int]] = [(0.0, sz, sx)]
    g: dict[tuple[int, int], float] = {(sz, sx): 0.0}
    came: dict[tuple[int, int], tuple[int, int]] = {}

    while open_heap:
        f, z, x = heapq.heappop(open_heap)
        if goal_mask[z, x]:
            path = [(z, x)]
            while (z, x) in came:
                z, x = came[(z, x)]
                path.append((z, x))
            return path[::-1]
        if f > g.get((z, x), 1e18):
            continue
        hc = int(height[z, x])
        for dz, dx, base in _NEIGHBORS:
            nz, nx = z + dz, x + dx
            # 同时卡 bounds 和数组实际尺寸（bound 可能超出 NZ/NX → 否则 height[nz,nx] 越界）
            if not (z0min <= nz <= z0max and x0min <= nx <= x0max):
                continue
            if not (0 <= nz < NZ and 0 <= nx < NX):
                continue
            hn = int(height[nz, nx])
            if hn <= min_y:                          # sentinel 无效列
                continue
            if not goal_mask[nz, nx] and not passable[nz, nx]:
                continue
            dh = abs(hn - hc)
            if dh > hard_step:                       # 太陡 → 绕
                continue
            cost = base + step_penalty * dh
            ng = g[(z, x)] + cost
            if ng < g.get((nz, nx), 1e18):
                g[(nz, nx)] = ng
                came[(nz, nx)] = (z, x)
                heapq.heappush(open_heap, (ng, nz, nx))
    return None


def plan_network(height: np.ndarray,
                 anchors: list[tuple[int, int]],
                 passable: np.ndarray,
                 min_y: int,
                 *,
                 hard_step: int = 2,
                 step_penalty: float = 4.0,
                 bounds: tuple[int, int, int, int] | None = None,
                 ) -> tuple[list[list[tuple[int, int]]], list[tuple[int, int]]]:
    """生长网络：把锚点逐个 A* 连进路网（每个到当前网络最近格），返回 (路径列表, 未连通锚点)。

    第一个锚点作种子；其余每个从自己 A* 到网络。走不通 → 记入 unreachable，不强连（宁缺）。
    height/passable 为 (NZ,NX)。anchors 为 scan (z,x)。
    """
    NZ, NX = height.shape
    if bounds is None:
        bounds = (0, NZ - 1, 0, NX - 1)
    network = np.zeros((NZ, NX), dtype=bool)
    paths: list[list[tuple[int, int]]] = []
    unreachable: list[tuple[int, int]] = []

    if not anchors:
        return paths, unreachable

    z0, x0 = anchors[0]
    network[z0, x0] = True
    for (z, x) in anchors[1:]:
        if network[z, x]:
            continue
        path = _astar(height, (z, x), network, passable, min_y,
                      hard_step, step_penalty, bounds)
        if path is None:
            unreachable.append((z, x))
            continue
        for (pz, px) in path:
            network[pz, px] = True
        paths.append(path)
    return paths, unreachable


def footprint_blocked_mask(placed_boxes, ctx, shape) -> np.ndarray:
    """把建筑占地框（世界 (x0,x1,z0,z1)）烧进 (NZ,NX) bool 掩码 → 路绕楼。"""
    NZ, NX = shape
    blocked = np.zeros((NZ, NX), dtype=bool)
    for (x0, x1, z0, z1) in placed_boxes:
        sx0, sz0 = ctx.w2s(int(min(x0, x1)), int(min(z0, z1)))
        sx1, sz1 = ctx.w2s(int(max(x0, x1)), int(max(z0, z1)))
        sx0, sx1 = sorted((sx0, sx1))
        sz0, sz1 = sorted((sz0, sz1))
        sx0 = max(0, sx0); sz0 = max(0, sz0)
        sx1 = min(NX - 1, sx1); sz1 = min(NZ - 1, sz1)
        blocked[sz0:sz1 + 1, sx0:sx1 + 1] = True
    return blocked


def _snap_valid(ground_height, min_y, sz, sx, radius=4):
    """把 (sz,sx) 吸附到最近的有效（非 sentinel）地面列；找不到返回 None。"""
    NZ, NX = ground_height.shape
    if 0 <= sz < NZ and 0 <= sx < NX and ground_height[sz, sx] > min_y:
        return (sz, sx)
    for r in range(1, radius + 1):
        for dz in range(-r, r + 1):
            for dx in range(-r, r + 1):
                if max(abs(dz), abs(dx)) != r:
                    continue
                nz, nx = sz + dz, sx + dx
                if 0 <= nz < NZ and 0 <= nx < NX and ground_height[nz, nx] > min_y:
                    return (nz, nx)
    return None


def building_door_anchors(placed_boxes, center_x, center_z, ctx,
                          ground_height, min_y, door_offset=2):
    """每栋建筑取"朝城心那条边外侧 door_offset 格"的门当锚点（scan (z,x)，吸附到有效列）。"""
    NZ, NX = ground_height.shape
    anchors = []
    for (x0, x1, z0, z1) in placed_boxes:
        bx0, bx1 = sorted((int(x0), int(x1)))
        bz0, bz1 = sorted((int(z0), int(z1)))
        bcx, bcz = (bx0 + bx1) // 2, (bz0 + bz1) // 2
        dx, dz = center_x - bcx, center_z - bcz
        if abs(dx) >= abs(dz):                      # 门开在 ±X 边
            doorx = (bx1 + door_offset) if dx > 0 else (bx0 - door_offset)
            doorz = bcz
        else:                                       # 门开在 ±Z 边
            doorz = (bz1 + door_offset) if dz > 0 else (bz0 - door_offset)
            doorx = bcx
        sx, sz = ctx.w2s(int(doorx), int(doorz))
        snapped = _snap_valid(ground_height, min_y, sz, sx)
        if snapped is not None:
            anchors.append(snapped)
    return anchors


def _stair_facing(dz: int, dx: int) -> str:
    """前进方向 → 楼梯 facing（上坡朝前进方向）。"""
    if abs(dx) >= abs(dz):
        return "east" if dx > 0 else "west"
    return "south" if dz > 0 else "north"


def network_to_blocks(paths, ground_height, ctx, *,
                      min_y=-64, road_width=3, surface="minecraft:cobblestone",
                      stairs="minecraft:cobblestone_stairs", clearance=3,
                      fill_depth=3):
    """把 A* 路径 drape 成 block 列表。

    关键修法（图 333/334）：
    - 方形笔刷（每个中线格刷 (2·half+1)² 方块）→ 对角段相邻笔刷重叠，不再棋盘镂空。
    - 整条路宽统一用**中线格高度** gy（非每列各自高度）→ 路面平整，不再单块凸起。
    - 清顶（gy+1..+clearance 设空气）削掉穿出路面的地形凸块；填底（gy-1 向下到地表，
      限 fill_depth）补悬空边，避免漂浮。无桥/无栏杆。
    - 中线爬升格用台阶。重叠格"后写胜"，天然去重。
    """
    NZ, NX = ground_height.shape
    half = road_width // 2
    cells: dict[tuple[int, int], tuple[int, str]] = {}   # (cz,cx) -> (gy, id)
    for path in paths:
        n = len(path)
        for i, (z, x) in enumerate(path):
            gy = int(ground_height[z, x])
            if gy <= min_y:
                continue
            if i + 1 < n:
                dz, dx = path[i + 1][0] - z, path[i + 1][1] - x
            elif i > 0:
                dz, dx = z - path[i - 1][0], x - path[i - 1][1]
            else:
                dz, dx = 0, 0
            climbing = i > 0 and gy > int(ground_height[path[i - 1]])
            facing = _stair_facing(dz, dx)
            for cz in range(z - half, z + half + 1):
                for cx in range(x - half, x + half + 1):
                    if not (0 <= cz < NZ and 0 <= cx < NX):
                        continue
                    if cz == z and cx == x and climbing:
                        bid = (f"{stairs}[facing={facing},"
                               f"half=bottom,shape=straight]")
                    else:
                        bid = surface
                    cells[(cz, cx)] = (gy, bid)          # 后写胜（去重）

    blocks = []
    for (cz, cx), (gy, bid) in cells.items():
        xw, zw = ctx.s2w(cx, cz)
        blocks.append({"x": xw, "y": gy, "z": zw, "id": bid})
        for cy in range(gy + 1, gy + 1 + clearance):    # 清顶：削凸块
            blocks.append({"x": xw, "y": cy, "z": zw, "id": "minecraft:air"})
        terrain = int(ground_height[cz, cx])            # 填底：补悬空边
        floor = max(terrain, gy - fill_depth)
        for cy in range(gy - 1, floor - 1, -1):
            blocks.append({"x": xw, "y": cy, "z": zw, "id": surface})
    return blocks


__all__ = [
    "build_ground_height", "plan_network",
    "footprint_blocked_mask", "building_door_anchors", "network_to_blocks",
]
