"""模块化建筑装配器。

从 suitability_map 找连通适宜区，每个区求最大内接矩形并切分成地块，
对每个地块按地形 + 圈层动态生成住宅/商铺。
"""
import random
from typing import List, Optional, Tuple

import numpy as np
from scipy.ndimage import label as ndimage_label

from ..mc.codec import BlockCodec
from ..mc.placement import set_blocks_batch
from ..scan.coord_frame import ScanContext
# city.terraform 在函数体内延迟 import，避免 modular ↔ city.builder 循环。
from .parts import (
    gen_floor_plate, gen_foundation_column,
    gen_roof_flat, gen_roof_gabled,
    gen_wall_section, get_materials,
)


MIN_BUILDING_X = 5
MIN_BUILDING_Z = 5
MAX_BUILDING_X = 16
MAX_BUILDING_Z = 16
FLOOR_HEIGHT = 4
MIN_SUITABILITY = 0.45
TERRAIN_NAMES = ["plains", "desert", "mountain", "snow", "water", "jungle",
                 "badlands", "volcano"]


# ══════════════════════════════════════════════════════════════════
# 1. 找可建地块
# ══════════════════════════════════════════════════════════════════

def find_buildable_plots(suitability_map: np.ndarray,
                         ctx: ScanContext,
                         center_x: int, center_z: int,
                         r_min: float, r_max: float,
                         threshold: float = MIN_SUITABILITY,
                         ring_mask: Optional[np.ndarray] = None,
                         ) -> List[Tuple[int, int, int, int]]:
    """二值化 + 圈层 mask + 连通域分析 + 最大内接矩形 + 切分。

    卡 3：如果传入 ring_mask（有机圈层）则直接用；否则回退到同心圆 r_min/r_max。

    返回 [(sx0, sz0, sx1, sz1), ...]（scan 坐标，闭区间）。
    """
    NZ, NX = suitability_map.shape
    scx, scz = ctx.w2s(center_x, center_z)

    binary = (suitability_map >= threshold).astype(np.uint8)

    if ring_mask is None:
        # 圆形兜底
        xs_idx = np.arange(NX, dtype=np.float32)
        zs_idx = np.arange(NZ, dtype=np.float32)
        xs_grid, zs_grid = np.meshgrid(xs_idx, zs_idx)
        dist_map = np.sqrt((xs_grid - scx) ** 2 + (zs_grid - scz) ** 2)
        effective_mask = (dist_map >= r_min) & (dist_map <= r_max)
    else:
        effective_mask = ring_mask

    binary = binary * effective_mask.astype(np.uint8)

    labeled, num_features = ndimage_label(binary)
    print(f"  找到 {num_features} 个连通适宜区域")

    all_plots: list = []
    for region_id in range(1, num_features + 1):
        region_mask = (labeled == region_id)
        if int(np.sum(region_mask)) < MIN_BUILDING_X * MIN_BUILDING_Z:
            continue
        rect = largest_interior_rectangle(region_mask)
        if rect is None:
            continue
        rx0, rz0, rx1, rz1 = rect
        if rx1 - rx0 + 1 < MIN_BUILDING_X or rz1 - rz0 + 1 < MIN_BUILDING_Z:
            continue
        all_plots.extend(split_rectangle(rx0, rz0, rx1, rz1,
                                         MAX_BUILDING_X, MAX_BUILDING_Z,
                                         MIN_BUILDING_X, MIN_BUILDING_Z))

    print(f"  处理后得到 {len(all_plots)} 个可建地块")
    return all_plots


# ══════════════════════════════════════════════════════════════════
# 2. 最大内接矩形（直方图 + 单调栈，O(NZ*NX)）
# ══════════════════════════════════════════════════════════════════

def largest_interior_rectangle(mask: np.ndarray
                               ) -> Optional[Tuple[int, int, int, int]]:
    NZ, NX = mask.shape
    heights = np.zeros(NX, dtype=np.int32)

    best_area = 0
    best_rect = None

    for z in range(NZ):
        for x in range(NX):
            heights[x] = heights[x] + 1 if mask[z, x] else 0

        rect = _max_rect_in_histogram(heights, z)
        if rect is not None:
            x0, z0, x1, z1 = rect
            area = (x1 - x0 + 1) * (z1 - z0 + 1)
            if area > best_area:
                best_area = area
                best_rect = rect

    return best_rect


def _max_rect_in_histogram(heights: np.ndarray, current_z: int
                           ) -> Optional[Tuple[int, int, int, int]]:
    stack = []
    best_area = 0
    best_rect = None
    NX = len(heights)

    for x in range(NX + 1):
        h = int(heights[x]) if x < NX else 0
        x_start = x
        while stack and stack[-1][1] > h:
            x_pop, h_pop = stack.pop()
            x_start = x_pop
            area = h_pop * (x - x_pop)
            if area > best_area:
                best_area = area
                best_rect = (x_pop, current_z - h_pop + 1, x - 1, current_z)
        stack.append((x_start, h))

    return best_rect


# ══════════════════════════════════════════════════════════════════
# 3. 切分大矩形（留 GAP 间距）
# ══════════════════════════════════════════════════════════════════

def split_rectangle(x0: int, z0: int, x1: int, z1: int,
                    max_w: int, max_h: int,
                    min_w: int, min_h: int
                    ) -> List[Tuple[int, int, int, int]]:
    GAP = 2
    x_starts = []
    cur_x = x0
    while cur_x <= x1:
        w = min(max_w, x1 - cur_x + 1)
        if w < min_w:
            break
        x_starts.append((cur_x, cur_x + w - 1))
        cur_x += w + GAP

    z_starts = []
    cur_z = z0
    while cur_z <= z1:
        h = min(max_h, z1 - cur_z + 1)
        if h < min_h:
            break
        z_starts.append((cur_z, cur_z + h - 1))
        cur_z += h + GAP

    return [(sx0, sz0, sx1, sz1)
            for (sx0, sx1) in x_starts
            for (sz0, sz1) in z_starts]


# ══════════════════════════════════════════════════════════════════
# 4. 单地块建筑装配
# ══════════════════════════════════════════════════════════════════

def assemble_building(plot: Tuple[int, int, int, int],
                      height_map: np.ndarray,
                      terrain_map: np.ndarray,
                      ctx: ScanContext,
                      center_x: int, center_z: int,
                      ring_name: str,
                      scan_volume: Optional[np.ndarray] = None,
                      codec: Optional[BlockCodec] = None,
                      batch_size: int = 4096) -> bool:
    """在地块上动态建一栋建筑。

    流程：terraform 选 base_y → 楼层数 → 朝向 → 墙面/楼板 → 屋顶 → 批量写入。

    Returns True 时表示成功放置；False 表示因 terraform 失败而跳过。
    """
    sx0, sz0, sx1, sz1 = plot
    NZ, NX = height_map.shape

    sx0 = max(0, sx0); sx1 = min(NX - 1, sx1)
    sz0 = max(0, sz0); sz1 = min(NZ - 1, sz1)
    if sx0 >= sx1 or sz0 >= sz1:
        return False

    building_w = sx1 - sx0 + 1
    building_d = sz1 - sz0 + 1

    # ── Step 1：base_y 由 terraform 决定（卡 5），失败则跳过此地块 ──
    terraformed = False
    terraform_result = None
    if getattr(ctx, "terrain_features", None) is not None:
        from ..city.terraform import apply_terraform, terraform_for_building
        terraform_result = terraform_for_building(
            footprint_xz=(sx0, sz0, sx1, sz1),
            height_map=height_map,
            features=ctx.terrain_features,
            ctx=ctx,
            terrain_map=terrain_map,
        )
        if not terraform_result.success:
            print(f"    ⏭️ terraform 失败 ({terraform_result.reason})，跳过此地块")
            return False
        base_y = int(terraform_result.base_y)
        terraformed = True
    else:
        # 退化路径（无 terrain_features）：保留旧行为
        patch = height_map[sz0:sz1 + 1, sx0:sx1 + 1].astype(np.int32)
        base_y = int(np.max(patch))

    mid_sx = (sx0 + sx1) // 2
    mid_sz = (sz0 + sz1) // 2
    t_code = int(terrain_map[mid_sz, mid_sx])
    terrain_type = TERRAIN_NAMES[t_code] if t_code < len(TERRAIN_NAMES) else "plains"
    if terrain_type == "water":
        return False

    mat = get_materials(terrain_type)

    # Step 2: 楼层数
    area = building_w * building_d
    if ring_name == "inner":
        max_floors = 2
    elif ring_name == "mid":
        max_floors = 2 if area >= 8 * 8 else 1
    else:
        max_floors = 1
    num_floors = random.randint(1, max_floors)

    # Step 3: 朝向（门朝向城市中心）
    world_cx, world_cz = ctx.s2w(mid_sx, mid_sz)
    dx = center_x - world_cx
    dz = center_z - world_cz
    if abs(dx) >= abs(dz):
        door_face = "west" if dx > 0 else "east"
        door_wall = "x_min" if dx > 0 else "x_max"
    else:
        door_face = "north" if dz > 0 else "south"
        door_wall = "z_min" if dz > 0 else "z_max"

    # ── 先提交 terraform（如果走的是 terraform 路径）──────────────
    # 这一步要在收集建筑方块之前做：terraform 失败时整栋地块作废，
    # 不能让墙体/屋顶先 paste 了再发现 terrain 没法垫——会出鬼魂方块。
    if terraformed:
        ok = apply_terraform(terraform_result, ctx,
                             scan_volume=scan_volume,
                             height_map=height_map, codec=codec)
        if not ok:
            print(f"    ⚠️ apply_terraform HTTP 失败，跳过此地块")
            return False

    # Step 4: 收集所有方块
    all_blocks = []

    # 4a) 地基柱：terraform 已经把 footprint 内的列垫齐到 base_y，跳过；
    #     无 terraform 时仍用 gen_foundation_column 兜底。
    if not terraformed:
        for zs in range(sz0, sz1 + 1):
            for xs in range(sx0, sx1 + 1):
                ground_y = int(height_map[zs, xs])
                if ground_y < base_y:
                    xw, zw = ctx.s2w(xs, zs)
                    all_blocks.extend(gen_foundation_column(xw, zw, ground_y, base_y, mat))

    # 4b) 室内地板
    world_x0, world_z0 = ctx.s2w(sx0, sz0)
    world_x1, world_z1 = ctx.s2w(sx1, sz1)
    all_blocks.extend(gen_floor_plate(world_x0, world_x1, world_z0, world_z1,
                                       base_y, mat))

    # 4c) 逐层墙面
    for floor_idx in range(num_floors):
        y_bottom = base_y + 1 + floor_idx * FLOOR_HEIGHT

        for zs in range(sz0, sz1 + 1):
            for xs in range(sx0, sx1 + 1):
                xw, zw = ctx.s2w(xs, zs)

                is_north = (zs == sz0)
                is_south = (zs == sz1)
                is_west = (xs == sx0)
                is_east = (xs == sx1)
                is_wall = is_north or is_south or is_west or is_east

                if not is_wall:
                    if floor_idx > 0:
                        all_blocks.append({
                            "x": xw, "y": y_bottom - 1, "z": zw,
                            "id": mat["floor"],
                        })
                    continue

                is_corner = sum([is_north, is_south, is_west, is_east]) >= 2

                mid_x_wall = (sx0 + sx1) // 2
                mid_z_wall = (sz0 + sz1) // 2
                is_door = (
                    floor_idx == 0 and not is_corner
                    and (
                        (door_wall == "z_min" and is_north and xs == mid_x_wall)
                        or (door_wall == "z_max" and is_south and xs == mid_x_wall)
                        or (door_wall == "x_min" and is_west and zs == mid_z_wall)
                        or (door_wall == "x_max" and is_east and zs == mid_z_wall)
                    )
                )
                is_window = (
                    not is_door and not is_corner
                    and (
                        ((is_north or is_south) and (xs - sx0) % 3 == 1)
                        or ((is_west or is_east) and (zs - sz0) % 3 == 1)
                    )
                )

                if is_north:   face = "north"
                elif is_south: face = "south"
                elif is_west:  face = "west"
                else:          face = "east"

                all_blocks.extend(gen_wall_section(
                    xw, zw,
                    y_bottom=y_bottom,
                    floor_height=FLOOR_HEIGHT,
                    face=face,
                    is_corner=is_corner,
                    has_window=is_window,
                    has_door=is_door,
                    mat=mat,
                ))

    # 4d) 屋顶
    roof_y = base_y + 1 + num_floors * FLOOR_HEIGHT
    if building_w <= 8 and building_d <= 8:
        all_blocks.extend(gen_roof_gabled(world_x0, world_x1, world_z0, world_z1,
                                          y_base=roof_y, mat=mat))
    else:
        all_blocks.extend(gen_roof_flat(world_x0, world_x1, world_z0, world_z1,
                                        y=roof_y, mat=mat))

    # Step 5: 批量写入
    for i in range(0, len(all_blocks), batch_size):
        set_blocks_batch(all_blocks[i:i + batch_size])

    return True


# ══════════════════════════════════════════════════════════════════
# 5. 圈层主入口
# ══════════════════════════════════════════════════════════════════

def build_modular_ring(ring_name: str,
                       r_min: float, r_max: float,
                       suitability_map: np.ndarray,
                       height_map: np.ndarray,
                       terrain_map: np.ndarray,
                       ctx: ScanContext,
                       center_x: int, center_z: int,
                       max_buildings: int = 20,
                       scan_volume: Optional[np.ndarray] = None,
                       codec: Optional[BlockCodec] = None,
                       ring_mask: Optional[np.ndarray] = None):
    """在指定圈层内生成最多 max_buildings 栋模块化建筑。

    scan_volume + codec 是卡 5 新增可选参数：terraform 需要它们同步内存。
    省略时 terraform 仍会 HTTP 提交，只是 scan_volume 不会被更新——下游若
    复读这些格子会拿到 stale 数据。生产路径请始终传入。

    卡 3：ring_mask 是有机圈层 mask；省略时 find_buildable_plots 回退 r_min/r_max。
    """
    mode = "organic" if ring_mask is not None else "circular"
    print(f"\n=== 模块化建筑：{ring_name.upper()} 圈 ({mode}) ===")

    plots = find_buildable_plots(
        suitability_map=suitability_map,
        ctx=ctx,
        center_x=center_x, center_z=center_z,
        r_min=r_min, r_max=r_max,
        ring_mask=ring_mask,
    )

    if not plots:
        print(f"  {ring_name} 圈没有找到可建地块")
        return

    random.shuffle(plots)
    plots = plots[:max_buildings]

    print(f"  开始生成 {len(plots)} 栋建筑...")
    placed = 0
    for i, plot in enumerate(plots):
        sx0, sz0, sx1, sz1 = plot
        print(f"  [{i + 1}/{len(plots)}] 地块: scan({sx0},{sz0})~({sx1},{sz1}), "
              f"尺寸={sx1 - sx0 + 1}x{sz1 - sz0 + 1}")
        ok = assemble_building(
            plot=plot,
            height_map=height_map,
            terrain_map=terrain_map,
            ctx=ctx,
            center_x=center_x, center_z=center_z,
            ring_name=ring_name,
            scan_volume=scan_volume,
            codec=codec,
        )
        if ok:
            placed += 1

    print(f"  {ring_name.upper()} 圈模块化建筑完成（{placed}/{len(plots)}）")
