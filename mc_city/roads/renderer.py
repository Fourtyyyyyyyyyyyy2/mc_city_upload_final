"""道路渲染：找坡 + 削填（自适应地形）。

旧版每列各自贴地表 → 路面跟着地形每格起伏（碎块/"没维护的路"）。新版沿中线
算一条顺滑的限坡高程线，路面统一铺到该线，低处填土支撑、高处削包，坡度处用
连续楼梯成坡道。水柱仍用木板桥 + 栅栏。
"""
from typing import List, Tuple

import numpy as np

from ..config import (
    ROAD_BRIDGE_MAX_SPAN,
    ROAD_BRIDGE_MIN_DEPTH,
    ROAD_BRIDGE_PILLAR_BLOCK,
    ROAD_BRIDGE_PILLAR_SPACING,
    ROAD_BRIDGE_RAIL_BLOCK,
    ROAD_BRIDGING_ENABLED,
    ROAD_GRADE_MAX_DEV,
    ROAD_SMOOTH_RADIUS,
    ROAD_SPIKE_BASE_RADIUS,
    ROAD_FILL_MATCH_TERRAIN,
    ROAD_SPIKE_CUTTHROUGH_ENABLED,
    ROAD_SPIKE_PROMINENCE,
    ROAD_TERRACE_ENABLED,
    ROAD_TERRACE_FLIGHT,
    ROAD_TERRACE_LANDING,
    ROAD_WALKABLE_CLEARANCE,
    ROAD_WALKABLE_CORRIDOR_ENABLED,
    SEA_LEVEL,
)
from ..mc.placement import set_blocks_batch

# 找坡参数（道路找坡卡）。可按观感微调。GRADE_MAX_DEV/SMOOTH 已移到 config。
_GRADE_MAX_STEP = 1         # 相邻中线点最大高差 → 楼梯连成可走坡道
_FILL_CAP = 10              # 单列填土最大深度（防陡横坡堆出高墙）
_CUT_CAP = 10               # 单列削包最大高度
_FILL_BLOCK = "minecraft:cobblestone"   # 路基填充块
# 不能当路基的地表块（植物/雪/水/空气）：采到则往下取一格实心体块。
_BAD_FILL = frozenset({
    "minecraft:air", "minecraft:water", "minecraft:lava",
    "minecraft:dead_bush", "minecraft:short_grass", "minecraft:grass",
    "minecraft:tall_grass", "minecraft:fern", "minecraft:large_fern",
    "minecraft:snow", "minecraft:cactus", "minecraft:bamboo",
})


class RoadRenderer:

    def __init__(self,
                 road_block: str = "minecraft:stone_bricks",
                 road_width: int = 4,
                 smoothing_radius: int = ROAD_SMOOTH_RADIUS,
                 height_map: np.ndarray = None,
                 origin_x: int = 0,
                 origin_z: int = 0,
                 min_y: int = -64,
                 blocked_boxes: list = None,
                 scan_volume=None,
                 codec=None):
        self.road_block = road_block
        self.road_width = road_width
        self.smoothing_radius = smoothing_radius
        self.height_map = height_map
        self.origin_x = origin_x
        self.origin_z = origin_z
        # UPGRADE_FROM_LEGACY.md §10.a: height_map sentinel = min_y。
        # 不判这条会把道路方块铺到 y=min_y（虚空），地表看不到。
        self.min_y = int(min_y)
        # 建筑包围盒 [(min_x,max_x,min_z,max_z), ...]（世界坐标）。落在盒内的列
        # 不铺路 → 路遇到建筑自动断开，不再被建筑覆盖/穿过建筑。
        self.blocked_boxes = blocked_boxes or []
        # 路基贴地材质用（卡：路基换贴地）。给定时填土采样该列地表块；缺时回退 _FILL_BLOCK。
        self.scan_volume = scan_volume
        self.codec = codec

    def _in_blocked(self, wx: int, wz: int) -> bool:
        for (bx0, bx1, bz0, bz1) in self.blocked_boxes:
            if bx0 <= wx <= bx1 and bz0 <= wz <= bz1:
                return True
        return False

    def _terrain_y(self, x: int, z: int):
        """世界坐标查 height_map，越界 / sentinel 列返回 None。"""
        if self.height_map is None:
            return None
        xs = x - self.origin_x
        zs = z - self.origin_z
        NZ, NX = self.height_map.shape
        if not (0 <= xs < NX and 0 <= zs < NZ):
            return None
        y = int(self.height_map[zs, xs])
        if y <= self.min_y:
            return None
        return y

    def render_path(self, path: List[Tuple[int, int, int]]):
        """找坡 + 削填渲染（卡：道路找坡）。

        旧版每列各自贴地表 → 路面跟着地形每格起伏，碎块/"没维护"。新版：
        1) 沿中线取地表高程，滑动平均 + 限坡（每格 ≤1）算出顺滑高程线 gy；
        2) 每列把路面统一铺到 gy，低于 gy 的填土支撑（不漂浮），高于 gy 的削掉（去包）；
        3) gy 有坡度处铺楼梯连成可走坡道，平处铺 road_block。
        """
        if not path:
            return
        dense = self._densify_raw(path)
        if not dense:
            return
        graded, bridge = self._grade_profile(dense)
        blocks = []
        n = len(graded)
        for i, (x, gy, z) in enumerate(graded):
            step = (graded[i + 1][1] - gy) if i + 1 < n else \
                   (gy - graded[i - 1][1] if i > 0 else 0)
            dirx = (graded[i + 1][0] - x) if i + 1 < n else \
                   (x - graded[i - 1][0] if i > 0 else 0)
            dirz = (graded[i + 1][2] - z) if i + 1 < n else \
                   (z - graded[i - 1][2] if i > 0 else 0)
            is_bridge = bridge[i]
            is_pillar = is_bridge and (i % ROAD_BRIDGE_PILLAR_SPACING == 0)
            if ROAD_WALKABLE_CORRIDOR_ENABLED and self._is_turn(graded, i):
                step = 0
            blocks.extend(self._road_section(x, gy, z, step, dirx, dirz,
                                             is_bridge=is_bridge,
                                             is_pillar=is_pillar))
        self._place_blocks_in_batches(blocks)

    def _densify_raw(self, path):
        """沿 XZ 每 1 格插点，取该列地表 Y（sentinel/越界列丢弃）。"""
        dense = []
        for i in range(len(path) - 1):
            x1, _, z1 = path[i]
            x2, _, z2 = path[i + 1]
            dist = max(abs(x2 - x1), abs(z2 - z1))
            if dist == 0:
                ty = self._terrain_y(x1, z1)
                if ty is not None:
                    dense.append((x1, ty, z1))
                continue
            for t in np.linspace(0, 1, max(dist, 2), endpoint=False):
                x = int(x1 + t * (x2 - x1))
                z = int(z1 + t * (z2 - z1))
                ty = self._terrain_y(x, z)
                if ty is not None:
                    dense.append((x, ty, z))
        last = path[-1]
        ty = self._terrain_y(last[0], last[2])
        if ty is not None:
            dense.append((last[0], ty, last[2]))
        return dense

    def _grade_profile(self, dense):
        """中线高程找坡：滑动平均 → 限制对地表偏离 ±MAX_DEV → 限坡每格 ≤1。

        限坡用前后两遍夹逼，保证相邻高程差 ≤1 → 楼梯能连成可走坡道，不再碎块。
        干裂谷（地表比两侧崖肩深 ≥MIN_DEPTH、跨度 ≤MAX_SPAN）改"架桥"：把路面
        线拉成两崖肩间的平直线（不沉底），对应中线点标记 bridge=True 交给渲染。

        返回 (graded, bridge_flags)，两者按 index 对齐。
        """
        n = len(dense)
        if n == 0:
            return dense, []
        ys = [p[1] for p in dense]
        w = max(1, int(self.smoothing_radius))

        # 卡55：算局部中位数基线 base。孤立高柱(冰锥)拉不动中位数 → 找坡时把超出
        # base+PROM 的列压到 base+PROM(只供找坡用，不改实际地表 ys；削方在渲染处做)。
        # 偏离钳位也对 base 而非每列地表 → 路面不被冰锥顶上去。flag 关时退回旧行为。
        if ROAD_SPIKE_CUTTHROUGH_ENABLED:
            sw = max(w, int(ROAD_SPIKE_BASE_RADIUS))     # 基线用更宽窗口，滤离群锥
            base = []
            for i in range(n):
                lo = max(0, i - sw); hi = min(n, i + sw + 1)
                base.append(int(np.median(ys[lo:hi])))
            ys_grade = [min(ys[i], base[i] + ROAD_SPIKE_PROMINENCE) for i in range(n)]
        else:
            base = ys
            ys_grade = ys

        gy = []
        for i in range(n):                                   # 1) 滑动平均(去尖高程)
            lo = max(0, i - w); hi = min(n, i + w + 1)
            gy.append(int(round(sum(ys_grade[lo:hi]) / (hi - lo))))
        for i in range(n):                                   # 2) 限制偏离基线
            gy[i] = max(base[i] - ROAD_GRADE_MAX_DEV,
                        min(base[i] + ROAD_GRADE_MAX_DEV, gy[i]))
        for i in range(1, n):                                # 3a) 前向限坡
            if gy[i] - gy[i - 1] > _GRADE_MAX_STEP:
                gy[i] = gy[i - 1] + _GRADE_MAX_STEP
            elif gy[i] - gy[i - 1] < -_GRADE_MAX_STEP:
                gy[i] = gy[i - 1] - _GRADE_MAX_STEP
        for i in range(n - 2, -1, -1):                       # 3b) 后向限坡
            if gy[i] - gy[i + 1] > _GRADE_MAX_STEP:
                gy[i] = gy[i + 1] + _GRADE_MAX_STEP
            elif gy[i] - gy[i + 1] < -_GRADE_MAX_STEP:
                gy[i] = gy[i + 1] - _GRADE_MAX_STEP

        # 3c) 梯化：把零散单台阶重排成「梯段+平台」节奏（陡坡读成阶梯街）。
        if ROAD_TERRACE_ENABLED:
            gy = self._terrace(gy, ROAD_TERRACE_FLIGHT, ROAD_TERRACE_LANDING,
                               ROAD_GRADE_MAX_DEV)

        # 4) 干裂谷架桥：把路面线跨过裂谷拉平（覆盖步骤 2 的"沉底"钳制）。
        bridge = [False] * n
        if ROAD_BRIDGING_ENABLED:
            for a, b in self._bridge_spans(ys):
                ga, gb, span = gy[a], gy[b], (b - a)
                for i in range(a + 1, b):
                    gy[i] = int(round(ga + (gb - ga) * (i - a) / span))
                    bridge[i] = True
        return [(dense[i][0], gy[i], dense[i][2]) for i in range(n)], bridge

    @staticmethod
    def _is_turn(graded, i: int) -> bool:
        if i <= 0 or i >= len(graded) - 1:
            return False
        px, _py, pz = graded[i - 1]
        x, _y, z = graded[i]
        nx, _ny, nz = graded[i + 1]
        prev_dir = (0 if x == px else (1 if x > px else -1),
                    0 if z == pz else (1 if z > pz else -1))
        next_dir = (0 if nx == x else (1 if nx > x else -1),
                    0 if nz == z else (1 if nz > z else -1))
        return prev_dir != next_dir

    @staticmethod
    def _terrace(gy, flight, landing, max_lag):
        """把 1-Lipschitz 的 gy 重排成「梯段（≤flight 级连续台阶）+ 平台（≥landing 格平）」。

        贪心沿 gy 走：想升/降时，仅当①刚歇够 landing 格可起新梯段 ②或正在梯段内且
        未超 flight 级，才走一级台阶；否则强制铺平（攒落差当平台）。路面对原 gy 的滞后
        用 max_lag 封顶——超了就不管节奏强行追，防陡坡上平台拖出巨大削填。输出仍 ±1/格
        可走。flat 地 gy 恒定 → 不产生台阶，本函数空转。
        """
        n = len(gy)
        if n < 2:
            return gy
        out = [gy[0]]
        flight_run = 0          # 当前梯段已铺台阶数
        flat_run = landing      # 距上一级台阶的平格数（初值放行首级）
        for i in range(1, n):
            cur = out[-1]
            lag = gy[i] - cur
            want = 1 if lag > 0 else (-1 if lag < 0 else 0)
            if want != 0:
                forced = abs(lag) > max_lag
                can = (0 < flight_run < flight) or (flight_run == 0 and flat_run >= landing)
                if not (forced or can):
                    want = 0
            if want != 0:
                out.append(cur + want)
                flight_run += 1
                flat_run = 0
            else:
                out.append(cur)
                flight_run = 0
                flat_run += 1
        return out

    def _bridge_spans(self, ys):
        """找可架桥的干裂谷区间 [(a, b), ...]：a/b 为两侧崖肩 index。

        从每个候选崖肩 a 向前扫，地表跌破 a 高度 ≥MIN_DEPTH 后又在 MAX_SPAN 内
        回到 a 同高(±1) → 判为裂谷，桥架在 a..b 间。回不到同高（单边悬崖）或太
        宽 → 不架，照旧下行。
        """
        n = len(ys)
        spans = []
        i = 0
        while i < n - 1:
            rim = ys[i]
            j = i + 1
            deepest = rim
            end = None
            while j < n and (j - i) <= ROAD_BRIDGE_MAX_SPAN:
                if ys[j] >= rim - 1:                 # 地表回到崖肩高度
                    if rim - deepest >= ROAD_BRIDGE_MIN_DEPTH:
                        end = j
                    break
                if ys[j] < deepest:
                    deepest = ys[j]
                j += 1
            if end is not None:
                spans.append((i, end))
                i = end                              # 从对岸崖肩续扫
            else:
                i += 1
        return spans

    def _fill_block_at(self, wx: int, wz: int) -> str:
        """路基填土材质：贴该列实际地表方块（红恶地→红陶等），缺数据回退 _FILL_BLOCK。

        采样工作态 scan_volume 在 height_map 地表 Y 处的方块。空气/解码失败/越界 → 回退。
        """
        if not ROAD_FILL_MATCH_TERRAIN or self.scan_volume is None or self.codec is None:
            return _FILL_BLOCK
        ty = self._terrain_y(wx, wz)
        if ty is None:
            return _FILL_BLOCK
        xs, zs = wx - self.origin_x, wz - self.origin_z
        yi = int(ty) - self.min_y
        NY = self.scan_volume.shape[0]
        try:
            for yy in (yi, yi - 1):                  # 地表是植物/雪→往下取一格实心体块
                if not (0 <= yy < NY):
                    continue
                name = self.codec.decode(int(self.scan_volume[yy, zs, xs]))
                if name and name not in _BAD_FILL:
                    return name
        except Exception as exc:
            print(f"[WARN] road fill material sample failed at ({wx},{wz}): {exc!r}")
            return _FILL_BLOCK
        return _FILL_BLOCK

    def _is_water_column(self, x: int, z: int) -> bool:
        y = self._terrain_y(x, z)
        return y is not None and y < SEA_LEVEL - 1

    @staticmethod
    def _facing_from_dir(dirx: int, dirz: int, climbing: bool) -> str:
        """由路径前进方向取楼梯朝向。

        MC 楼梯 facing 指向高的一侧（台阶顺着 facing 反向往上走）。climbing=True
        表示前进方向是上坡 → facing 朝前进方向；下坡则朝来向。
        （实测上一版放反了，这版翻转过来。）
        """
        if abs(dirx) >= abs(dirz):
            fwd = "east" if dirx > 0 else "west"
            back = "west" if dirx > 0 else "east"
        else:
            fwd = "south" if dirz > 0 else "north"
            back = "north" if dirz > 0 else "south"
        return fwd if climbing else back

    def _bridge_section(self, x: int, gy: int, z: int,
                        is_pillar: bool) -> List[dict]:
        """干裂谷桥面截面：桥面(road_block) + 两侧栏杆 + 桥墩，下方留空。

        is_pillar=True 的中线点在两侧边列从谷底地表竖一根桥墩到桥面下。
        """
        blocks = []
        lo = -(self.road_width // 2)
        hi = self.road_width - self.road_width // 2
        for dx in range(lo, hi):
            for dz in range(lo, hi):
                wx, wz = int(x + dx), int(z + dz)
                if self.blocked_boxes and self._in_blocked(wx, wz):
                    continue
                is_edge = (dx == lo or dx == hi - 1 or dz == lo or dz == hi - 1)
                blocks.append({"x": wx, "y": gy, "z": wz, "id": self.road_block})
                if is_edge:                                  # 栏杆
                    blocks.append({"x": wx, "y": gy + 1, "z": wz,
                                   "id": ROAD_BRIDGE_RAIL_BLOCK})
                head_lo = gy + 2 if is_edge else gy + 1      # 上方净空
                clearance_top = gy + max(3, int(ROAD_WALKABLE_CLEARANCE))
                for fy in range(head_lo, clearance_top + 1):
                    blocks.append({"x": wx, "y": fy, "z": wz, "id": "minecraft:air"})
                if is_pillar and is_edge:                    # 桥墩落到谷底
                    ty = self._terrain_y(wx, wz)
                    if ty is not None and ty < gy - 1:
                        for fy in range(ty + 1, gy):
                            blocks.append({"x": wx, "y": fy, "z": wz,
                                           "id": ROAD_BRIDGE_PILLAR_BLOCK})
        return blocks

    def _road_section(self, x: int, gy: int, z: int,
                      step: int, dirx: int, dirz: int,
                      is_bridge: bool = False,
                      is_pillar: bool = False) -> List[dict]:
        """中线点 (x,gy,z) 的 road_width 宽截面：路面统一铺到 gy + 削填支撑。

        - is_bridge：走桥面渲染（桥面+栏杆+桥墩，下方架空），不填不削。
        - 路面块：有纵向坡度→楼梯（连续坡道）；平→road_block。
        - 列地表 < gy：填土到 gy-1（_FILL_BLOCK 路基），不漂浮。
        - 列地表 > gy：gy+1..地表 削成空气（去包）。
        - 水列：保留架桥逻辑。
        削填各设上限，避免在陡横坡上堆/挖出大墙。
        """
        if is_bridge:
            return self._bridge_section(x, gy, z, is_pillar)

        blocks = []
        lo = -(self.road_width // 2)
        hi = self.road_width - self.road_width // 2          # 开区间上界

        sloped = abs(step) >= 1
        facing = self._facing_from_dir(dirx, dirz, climbing=(step > 0)) \
            if sloped else None
        if ROAD_WALKABLE_CORRIDOR_ENABLED:
            surf_id = self.road_block
        else:
            surf_id = (f"minecraft:cobblestone_stairs[facing={facing},"
                       f"half=bottom,shape=straight]") if sloped else self.road_block

        for dx in range(lo, hi):
            for dz in range(lo, hi):
                wx = int(x + dx)
                wz = int(z + dz)
                if self.blocked_boxes and self._in_blocked(wx, wz):
                    continue                                 # 落在建筑里 → 断开
                ty = self._terrain_y(wx, wz)
                if ty is None:
                    continue
                is_edge = (dx == lo or dx == hi - 1 or dz == lo or dz == hi - 1)

                if self._is_water_column(wx, wz) and not ROAD_WALKABLE_CORRIDOR_ENABLED:
                    for fy in range(ty + 1, SEA_LEVEL):
                        blocks.append({"x": wx, "y": fy, "z": wz, "id": "minecraft:air"})
                    blocks.append({"x": wx, "y": SEA_LEVEL, "z": wz,
                                   "id": "minecraft:oak_planks"})
                    blocks.append({"x": wx, "y": SEA_LEVEL + 1, "z": wz,
                                   "id": "minecraft:oak_fence" if is_edge
                                   else "minecraft:air"})
                    continue

                # 填土支撑（地表低于路面）。路基材质贴该列地表块（红恶地不再糊灰）。
                fill_id = self._fill_block_at(wx, wz)
                if ROAD_WALKABLE_CORRIDOR_ENABLED:
                    fill_lo = max(int(ty), gy - max(_FILL_CAP, ROAD_GRADE_MAX_DEV + 2))
                else:
                    fill_lo = max(int(ty), gy - _FILL_CAP)
                for fy in range(fill_lo, gy):
                    blocks.append({"x": wx, "y": fy, "z": wz, "id": fill_id})
                # 削包（地表高于路面）。卡55：切过冰锥时把高于路面的地形全削掉
                # (不受 _CUT_CAP 限)，否则高出 >10 的锥顶留在路上挡路。
                cut_hi = int(ty) if ROAD_SPIKE_CUTTHROUGH_ENABLED \
                    else min(int(ty), gy + _CUT_CAP)
                for fy in range(gy + 1, cut_hi + 1):
                    blocks.append({"x": wx, "y": fy, "z": wz, "id": "minecraft:air"})
                # 路面 + 上方净空
                blocks.append({"x": wx, "y": gy, "z": wz, "id": surf_id})
                clearance_top = gy + max(3, int(ROAD_WALKABLE_CLEARANCE))
                for fy in range(max(gy + 1, cut_hi + 1), clearance_top + 1):
                    blocks.append({"x": wx, "y": fy, "z": wz, "id": "minecraft:air"})

        return blocks

    @staticmethod
    def _place_blocks_in_batches(blocks: List[dict], batch_size: int = 4096):
        if not blocks:
            return
        for i in range(0, len(blocks), batch_size):
            batch = blocks[i:i + batch_size]
            if not set_blocks_batch(batch):
                print(f"警告：批次 {i // batch_size + 1} 放置失败")
