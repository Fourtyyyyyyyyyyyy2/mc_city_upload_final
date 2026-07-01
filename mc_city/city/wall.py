"""城墙生成：圆形跟随地形，平滑、过水柱、塔楼。"""
from __future__ import annotations

import math

import numpy as np

from ..config import (
    CHINESE_WALL_STYLE_ENABLED,
    CHINESE_WALL_TOWER_HALF_SIZE,
    ENV_BUILDING_STYLE_BY_TERRAIN,
    WALL_FLAT_TOP,
    WALL_SHAPE,
    WALL_WIDTH,
)
from ..mc.blocks import WATER_FLUIDS_EXTENDED
from ..mc.placement import set_blocks_batch
from ..scan.coord_frame import ScanContext
from .placement import boxes_intersect
from .terrain import get_terrain_material_at, terrain_name_at


def _square_perimeter(cx: int, cz: int, r: int) -> list[tuple[int, int]]:
    """方形城墙周边点（顺时针，每格一个，角点不重复）。半边长 = r。"""
    pts: list[tuple[int, int]] = []
    for x in range(cx - r, cx + r + 1):          # 顶边 z=cz-r
        pts.append((x, cz - r))
    for z in range(cz - r + 1, cz + r + 1):      # 右边 x=cx+r
        pts.append((cx + r, z))
    for x in range(cx + r - 1, cx - r - 1, -1):  # 底边 z=cz+r
        pts.append((x, cz + r))
    for z in range(cz + r - 1, cz - r, -1):      # 左边 x=cx-r
        pts.append((cx - r, z))
    return pts


def _circle_perimeter(cx: int, cz: int, r: int) -> list[tuple[int, int]]:
    """圆形城墙周边点（旧行为）。"""
    circ = int(2 * math.pi * r) + 4
    pts = []
    for i in range(circ):
        theta = 2 * math.pi * i / circ
        pts.append((int(round(cx + r * math.cos(theta))),
                    int(round(cz + r * math.sin(theta)))))
    return pts

_WALL_MATERIALS = {
    "plains":   ("minecraft:stone_bricks",     "minecraft:stone_brick_wall"),
    "desert":   ("minecraft:sandstone",        "minecraft:sandstone_wall"),
    "snow":     ("minecraft:smooth_stone",     "minecraft:stone_brick_wall"),
    "mountain": ("minecraft:deepslate_bricks", "minecraft:deepslate_brick_wall"),
    "jungle":   ("minecraft:stone_bricks",     "minecraft:stone_brick_wall"),
    "badlands": ("minecraft:red_sandstone",    "minecraft:red_sandstone_wall"),
}

_CHINESE_WALL_TRIM = "minecraft:polished_andesite"
_CHINESE_WALL_PILLAR = "minecraft:red_terracotta"
_CHINESE_WALL_ROOF = "minecraft:dark_prismarine_slab[type=bottom]"
_CHINESE_WALL_EAVE = "minecraft:dark_prismarine_stairs[facing={facing},half=bottom,shape=straight]"
_CHINESE_TOWER_ROOF = "minecraft:dark_prismarine"


def _wall_materials_for(terrain_name: str) -> tuple[str, str]:
    return _WALL_MATERIALS.get(terrain_name,
                               ("minecraft:stone_bricks", "minecraft:stone_brick_wall"))


def _uses_chinese_wall(terrain_name: str) -> bool:
    if not CHINESE_WALL_STYLE_ENABLED:
        return False
    return ENV_BUILDING_STYLE_BY_TERRAIN.get(terrain_name) == "chinese"


def _facing_from_vec(dx: int, dz: int) -> str:
    if abs(dx) >= abs(dz):
        return "east" if dx >= 0 else "west"
    return "south" if dz >= 0 else "north"


def _wall_normal(center_x: int, center_z: int, xw: int, zw: int) -> tuple[int, int]:
    dx = xw - center_x
    dz = zw - center_z
    if abs(dx) >= abs(dz):
        return (1 if dx >= 0 else -1, 0)
    return (0, 1 if dz >= 0 else -1)


def _wall_half_width(width: int = None) -> int:
    """城墙横截面半宽（格）。width=None 时用全局 WALL_WIDTH（大图默认）。"""
    w = WALL_WIDTH if width is None else width
    return max(0, int(w) // 2)


def _wall_cross_cells(center_x: int, center_z: int,
                      xw: int, zw: int,
                      half: int = None) -> list[tuple[int, int, int]]:
    """沿墙法线方向展开的横截面格子 [(wx, wz, off), ...]，宽度 = 2*half+1。"""
    nx, nz = _wall_normal(center_x, center_z, xw, zw)
    if half is None:
        half = _wall_half_width()
    return [(xw + nx * off, zw + nz * off, off)
            for off in range(-half, half + 1)]


def _append_chinese_wall_cap(batch: list, xw: int, top_y: int, zw: int,
                             center_x: int, center_z: int, half: int = None):
    nx, nz = _wall_normal(center_x, center_z, xw, zw)
    if half is None:
        half = _wall_half_width()
    facing_out = _facing_from_vec(nx, nz)
    facing_in = _facing_from_vec(-nx, -nz)
    roof_y = top_y + 1
    for off in range(-half, half + 1):
        batch.append({"x": xw + nx * off, "y": roof_y,
                      "z": zw + nz * off, "id": _CHINESE_WALL_ROOF})
    batch.append({"x": xw + nx * (half + 1), "y": roof_y,
                  "z": zw + nz * (half + 1),
                  "id": _CHINESE_WALL_EAVE.format(facing=facing_out)})
    batch.append({"x": xw - nx * (half + 1), "y": roof_y,
                  "z": zw - nz * (half + 1),
                  "id": _CHINESE_WALL_EAVE.format(facing=facing_in)})


def _append_chinese_wall_body(batch: list, xw: int, zw: int,
                              bottom_y: int, top_y: int,
                              center_x: int, center_z: int,
                              wall_block: str, pillar: bool, half: int = None):
    if half is None:
        half = _wall_half_width()
    for wx, wz, off in _wall_cross_cells(center_x, center_z, xw, zw, half=half):
        is_face = abs(off) == half
        block_id = _CHINESE_WALL_PILLAR if pillar and is_face else wall_block
        for y in range(bottom_y, top_y + 1):
            batch.append({"x": wx, "y": y, "z": wz, "id": block_id})
        batch.append({"x": wx, "y": top_y, "z": wz, "id": _CHINESE_WALL_TRIM})
    _append_chinese_wall_cap(batch, xw, top_y, zw, center_x, center_z, half=half)


def build_city_wall(center_x: int, center_z: int,
                    radius: int,
                    height_map: np.ndarray,
                    ctx: ScanContext,
                    placed_boxes: list = None,
                    terrain_map: np.ndarray = None,
                    scan_volume: np.ndarray = None,
                    codec=None,
                    wall_height: int = 5,
                    gate_interval: int = 90,
                    gate_width: int = 7,
                    tower_interval: int = 45,
                    batch_size: int = 4096,
                    width: int = None,
                    flat_top: bool = None):
    """城墙（方形/圆形），底部跟随地形，宽度=width（None→全局 WALL_WIDTH）。

    墙顶：flat_top=True（None→全局 WALL_FLAT_TOP）时整圈拉平到「最高有效地表点 +
      wall_height」，雉堞齐平（低处墙更高）；False 时随各点地形起伏。
    小图可传 width=1, flat_top=False, wall_height=2 得到窄矮墙。
    水柱：向下扫描找水底实体地面，从水底填到墙顶。
    陆地：用 ±2 范围内的最低 ground_y 作为底部锚点，墙不会浮空。
    塔楼按 tower_interval 度间隔放置（方形=4 角），门洞按 gate_interval 度。
    """
    placed_boxes = placed_boxes or []
    H, W = height_map.shape
    batch = []
    # 宽度/平顶：参数优先，None 回退全局（大图默认行为不变）。
    eff_half = _wall_half_width(width)
    eff_flat_top = WALL_FLAT_TOP if flat_top is None else flat_top

    gate_angles = set(range(0, 360, gate_interval))

    def _is_gate_zone(deg: float) -> bool:
        return any(abs((deg - ga + 180) % 360 - 180) <= gate_width // 2
                   for ga in gate_angles)

    def _overlaps_building(xw: int, zw: int, margin: int = 2) -> bool:
        test_box = (xw - margin, xw + margin, zw - margin, zw + margin)
        return any(boxes_intersect(test_box, b) for b in placed_boxes)

    # ── Step 1: 采集周边点和 ground_y（方形 / 圆形）──
    if WALL_SHAPE == "square":
        perimeter = _square_perimeter(center_x, center_z, radius)
    else:
        perimeter = _circle_perimeter(center_x, center_z, radius)
    wall_points = []  # [(xw, zw, deg, ground_y, is_valid), ...]

    for xw, zw in perimeter:
        deg = math.degrees(math.atan2(zw - center_z, xw - center_x)) % 360
        xs, zs = ctx.w2s(xw, zw)

        if not (0 <= xs < W and 0 <= zs < H):
            wall_points.append((xw, zw, deg, 64, False))
            continue

        raw_y = int(height_map[zs, xs])
        if raw_y <= ctx.min_y:
            wall_points.append((xw, zw, deg, raw_y, False))
            continue

        # 水柱：向下扫到水底实体地面
        if (terrain_map is not None and scan_volume is not None
                and int(terrain_map[zs, xs]) == 4):
            found_bottom = False
            scan_start = min(raw_y - ctx.min_y, scan_volume.shape[0] - 1)
            for dy in range(scan_start, -1, -1):
                block = scan_volume[dy, zs, xs]
                if codec is not None:
                    bid = codec.decode(int(block)).split("[")[0]
                else:
                    bid = str(block).split("[")[0]
                if bid not in WATER_FLUIDS_EXTENDED and bid != "minecraft:air":
                    raw_y = dy + ctx.min_y
                    found_bottom = True
                    break
            if not found_bottom:
                wall_points.append((xw, zw, deg, raw_y, False))
                continue

        is_valid = not _overlaps_building(xw, zw)
        wall_points.append((xw, zw, deg, raw_y, is_valid))

    # ── Step 2: 滑动平均平滑 ground_y ──
    n = len(wall_points)
    window = 5
    smoothed_y = []
    for i in range(n):
        ys = [wall_points[(i + di) % n][3]
              for di in range(-window // 2, window // 2 + 1)]
        smoothed_y.append(int(round(sum(ys) / len(ys))))

    # WALL_FLAT_TOP：整圈墙顶拉平到「最高有效地表点 + wall_height」，雉堞齐平。
    # 低处的墙会更高（从各自地表建到统一顶）。None = 关闭（顶随地形）。
    flat_top_y = None
    if eff_flat_top:
        valid_gys = [smoothed_y[i] for i in range(n) if wall_points[i][4]]
        if valid_gys:
            flat_top_y = max(valid_gys) + wall_height

    # ── Step 3: 生成墙体方块 ──
    seen_xz = set()
    for i in range(n):
        xw, zw, deg, raw_gy, is_valid = wall_points[i]
        if not is_valid or (xw, zw) in seen_xz:
            continue
        seen_xz.add((xw, zw))

        gy = smoothed_y[i]
        next_gy = smoothed_y[(i + 1) % n]

        xs_w, zs_w = ctx.w2s(xw, zw)
        terrain = terrain_name_at(terrain_map, xs_w, zs_w) if terrain_map is not None else "plains"
        if terrain == "water":
            terrain = "plains"
        wblock, mblock = _wall_materials_for(terrain)
        chinese_wall = _uses_chinese_wall(terrain)

        if _is_gate_zone(deg):
            # 门洞：地面铺满整个墙宽的台阶板（不砌墙身），中式/非中式一致。
            for wx, wz, _off in _wall_cross_cells(center_x, center_z, xw, zw,
                                                  half=eff_half):
                batch.append({"x": wx, "y": gy, "z": wz,
                              "id": "minecraft:stone_brick_slab"})
        else:
            bottom_y = min(gy, next_gy) - 3

            # 水柱：从水底填到水面 + wall_height
            if (terrain_map is not None and 0 <= xs_w < W and 0 <= zs_w < H
                    and int(terrain_map[zs_w, xs_w]) == 4):
                water_surface_y = int(height_map[zs_w, xs_w])
                effective_base = max(gy, water_surface_y)
                if flat_top_y is not None:
                    top_y = max(flat_top_y, effective_base + 1)
                else:
                    top_y = effective_base + wall_height
                if chinese_wall:
                    _append_chinese_wall_body(
                        batch, xw, zw, bottom_y, top_y,
                        center_x, center_z, wblock, pillar=(i % 6 == 0),
                        half=eff_half)
                else:
                    for wx, wz, _off in _wall_cross_cells(center_x, center_z, xw, zw,
                                                          half=eff_half):
                        for y in range(bottom_y, top_y + 1):
                            batch.append({"x": wx, "y": y, "z": wz, "id": wblock})
                    if i % 2 == 0:
                        batch.append({"x": xw, "y": top_y + 1, "z": zw, "id": mblock})
                if len(batch) >= batch_size:
                    set_blocks_batch(batch)
                    batch = []
                continue

            # 陆地：锚到 ±2 范围内的最低 ground_y
            local_min_y = gy
            for _dz in range(-2, 3):
                for _dx in range(-2, 3):
                    _nxs = xs_w + _dx
                    _nzs = zs_w + _dz
                    if 0 <= _nxs < W and 0 <= _nzs < H:
                        h = int(height_map[_nzs, _nxs])
                        if h > ctx.min_y:
                            local_min_y = min(local_min_y, h)
            bottom_y = local_min_y - 3
            top_y = flat_top_y if flat_top_y is not None else gy + wall_height
            if chinese_wall:
                _append_chinese_wall_body(
                    batch, xw, zw, bottom_y, top_y,
                    center_x, center_z, wblock, pillar=(i % 6 == 0),
                    half=eff_half)
            else:
                for wx, wz, _off in _wall_cross_cells(center_x, center_z, xw, zw,
                                                      half=eff_half):
                    for y in range(bottom_y, top_y + 1):
                        batch.append({"x": wx, "y": y, "z": wz, "id": wblock})
                if i % 2 == 0:
                    batch.append({"x": xw, "y": top_y + 1, "z": zw, "id": mblock})

        if len(batch) >= batch_size:
            set_blocks_batch(batch)
            batch = []

    # ── Step 4: 塔楼（方形=4 角；圆形=按角度间隔）──
    if WALL_SHAPE == "square":
        tower_anchors = [(center_x - radius, center_z - radius),
                         (center_x + radius, center_z - radius),
                         (center_x + radius, center_z + radius),
                         (center_x - radius, center_z + radius)]
    else:
        tower_anchors = [(int(round(center_x + radius * math.cos(math.radians(d)))),
                          int(round(center_z + radius * math.sin(math.radians(d)))))
                         for d in range(0, 360, tower_interval)]
    for txw, tzw in tower_anchors:
        txs, tzs = ctx.w2s(txw, tzw)
        if not (0 <= txs < W and 0 <= tzs < H):
            continue
        if _overlaps_building(txw, tzw, margin=max(4, int(CHINESE_WALL_TOWER_HALF_SIZE) + 2)):
            continue

        ground_y = int(height_map[tzs, txs])
        if ground_y <= ctx.min_y:
            continue
        # 塔顶：flat-top 时跟随统一墙顶再抬高 3 格（塔仍突出）；否则随本地地表。
        if flat_top_y is not None:
            tower_top_y = flat_top_y + 3
        else:
            tower_top_y = ground_y + wall_height + 3
        tower_min_y = ground_y
        for _dz in range(-2, 3):
            for _dx in range(-2, 3):
                _nxs = txs + _dx
                _nzs = tzs + _dz
                if 0 <= _nxs < W and 0 <= _nzs < H:
                    h = int(height_map[_nzs, _nxs])
                    if h > ctx.min_y:
                        tower_min_y = min(tower_min_y, h)
        bottom_y = tower_min_y - 4

        terrain = terrain_name_at(terrain_map, txs, tzs) if terrain_map is not None else "plains"
        if terrain == "water":
            terrain = "plains"
        tw_block, tm_block = _wall_materials_for(terrain)
        chinese_tower = _uses_chinese_wall(terrain)
        tower_half = int(CHINESE_WALL_TOWER_HALF_SIZE) if chinese_tower else 2

        for dy_abs in range(bottom_y, tower_top_y + 1):
            for dx in range(-tower_half, tower_half + 1):
                for dz in range(-tower_half, tower_half + 1):
                    block_id = tw_block
                    if chinese_tower and (abs(dx) == tower_half or abs(dz) == tower_half):
                        block_id = _CHINESE_WALL_PILLAR
                    batch.append({"x": txw + dx, "y": dy_abs,
                                  "z": tzw + dz, "id": block_id})

        for dx in range(-tower_half, tower_half + 1):
            for dz in range(-tower_half, tower_half + 1):
                if abs(dx) == tower_half or abs(dz) == tower_half:
                    cap_id = _CHINESE_TOWER_ROOF if chinese_tower else tm_block
                    batch.append({"x": txw + dx,
                                  "y": tower_top_y + 1,
                                  "z": tzw + dz,
                                  "id": cap_id})
        if chinese_tower:
            roof_half = tower_half + 1
            for dx in range(-roof_half, roof_half + 1):
                for dz in range(-roof_half, roof_half + 1):
                    if abs(dx) == roof_half or abs(dz) == roof_half:
                        batch.append({"x": txw + dx,
                                      "y": tower_top_y,
                                      "z": tzw + dz,
                                      "id": "minecraft:dark_prismarine_slab[type=bottom]"})

        if len(batch) >= batch_size:
            set_blocks_batch(batch)
            batch = []

    if batch:
        set_blocks_batch(batch)

    print(f"City wall built: radius={radius}, wall_points={n}")
