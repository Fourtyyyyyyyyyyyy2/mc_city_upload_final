"""地基处理：单列柱、地基柱网、城市底板（水域填充）。"""
import numpy as np

from ..config import SEA_CITY_ENABLED, WALL_SHAPE
from ..mc.blocks import WATER_FLUIDS_EXTENDED
from ..mc.placement import set_blocks_batch
from ..scan.coord_frame import ScanContext
from .terrain import get_terrain_fill_blocks, get_terrain_material_at

_SQUARE = WALL_SHAPE == "square"


def fill_column_solid(xw: int, zw: int,
                      from_y: int, to_y: int,
                      fill_block: str, top_block: str) -> list:
    """从 from_y（含）连续填到 to_y（顶层用 top_block），保证不漂浮。"""
    blocks = []
    if from_y >= to_y:
        return blocks
    for yy in range(from_y, to_y):
        blocks.append({"x": xw, "y": yy, "z": zw, "id": fill_block})
    blocks.append({"x": xw, "y": to_y, "z": zw, "id": top_block})
    return blocks


def build_stilt_foundation(height_map: np.ndarray,
                           sx0: int, sx1: int,
                           sz0: int, sz1: int,
                           base_y: int,
                           ctx: ScanContext,
                           terrain_map: np.ndarray = None,
                           batch_size: int = 4096):
    """每列独立从 ground_y 打桩到 base_y，不平整地形。

    柱子材质按高度差选择：
      gap ≤ 2：地形材质（几乎看不出来）
      gap 3~6：cobblestone（矮基础）
      gap > 6：stone_bricks（高柱子，有建筑感）
    """
    H, W = height_map.shape
    sx0 = max(0, sx0); sx1 = min(W - 1, sx1)
    sz0 = max(0, sz0); sz1 = min(H - 1, sz1)

    batch = []

    for zs in range(sz0, sz1 + 1):
        for xs in range(sx0, sx1 + 1):
            ground_y = int(height_map[zs, xs])

            if ground_y >= base_y:
                continue

            gap = base_y - ground_y

            if gap <= 2:
                if terrain_map is not None:
                    t_name = get_terrain_material_at(terrain_map, xs, zs, search_radius=2)
                    col_block, top_block = get_terrain_fill_blocks(t_name)
                else:
                    col_block = "minecraft:dirt"
                    top_block = "minecraft:grass_block"
            elif gap <= 6:
                col_block = "minecraft:cobblestone"
                top_block = "minecraft:cobblestone"
            else:
                col_block = "minecraft:stone_bricks"
                top_block = "minecraft:stone_bricks"

            xw, zw = ctx.s2w(xs, zs)

            # 柱身
            for yy in range(ground_y, base_y - 1):
                batch.append({"x": xw, "y": yy, "z": zw, "id": col_block})
            # 柱头
            if base_y - 1 >= ground_y:
                batch.append({"x": xw, "y": base_y - 1, "z": zw, "id": top_block})

            if len(batch) >= batch_size:
                set_blocks_batch(batch)
                batch = []

    if batch:
        set_blocks_batch(batch)


def fill_water_only(center_x: int, center_z: int,
                    wall_radius: int,
                    height_map: np.ndarray,
                    terrain_map: np.ndarray,
                    scan_volume: np.ndarray,
                    ctx: ScanContext,
                    codec,
                    batch_size: int = 1024):
    """卡 5 入口：城墙范围内仅填水域，陆地完全不动。

    与旧 prepare_city_floor 行为等价（这版代码本就没有"平整全城"的副作用，
    本函数把语义显式化）。水柱处理逻辑：向下扫 scan_volume 找水底实体方块 →
    用周围地形材质从水底填到水面。
    """
    # 海城模式：保留可见水面（吐脚楼立海面），不填水。
    if SEA_CITY_ENABLED:
        print("   [SEA_CITY] 跳过填水：保留可见海面（楼立海面）")
        return []

    NZ, NX = height_map.shape
    NY = scan_volume.shape[0]
    scx, scz = ctx.w2s(center_x, center_z)

    shape_label = "方城" if _SQUARE else "圆城"
    print(f"   处理城墙范围 {wall_radius}（{shape_label}）以内的水域...")

    batch = []
    processed = 0
    filled_cells = []                             # 填成陆地的 (xs, zs)，回传给 builder 刷 is_water

    for zs in range(NZ):
        for xs in range(NX):
            if _SQUARE:                               # 方城：方距，修补到方墙的角
                if max(abs(xs - scx), abs(zs - scz)) > wall_radius:
                    continue
            else:
                dist = ((xs - scx) ** 2 + (zs - scz) ** 2) ** 0.5
                if dist > wall_radius:
                    continue

            from .terrain import terrain_name_at
            if terrain_name_at(terrain_map, xs, zs) != "water":
                continue

            surface_terrain = get_terrain_material_at(terrain_map, xs, zs, search_radius=5)
            t_fill, t_top = get_terrain_fill_blocks(surface_terrain)

            xw, zw = ctx.s2w(xs, zs)
            surface_y = int(height_map[zs, xs])

            water_bottom_y = surface_y
            for yi in range(NY - 1, -1, -1):
                world_y = yi + ctx.min_y
                if world_y > surface_y:
                    continue
                block = scan_volume[yi, zs, xs]
                if hasattr(codec, 'decode'):
                    bid = codec.decode(int(block)).split("[")[0]
                else:
                    bid = str(block).split("[")[0]
                if bid not in WATER_FLUIDS_EXTENDED and bid != "minecraft:air":
                    water_bottom_y = world_y
                    break

            if water_bottom_y < surface_y:
                blocks = fill_column_solid(xw, zw,
                                           from_y=water_bottom_y, to_y=surface_y,
                                           fill_block=t_fill, top_block=t_top)
                batch.extend(blocks)
            else:
                batch.append({"x": xw, "y": surface_y, "z": zw, "id": t_top})

            processed += 1
            filled_cells.append((xs, zs))

            if len(batch) >= batch_size:
                set_blocks_batch(batch)
                batch = []

    if batch:
        set_blocks_batch(batch)

    print(f"   城市底板完成：处理了 {processed} 个水域格子")
    return filled_cells


def prepare_city_floor(center_x: int, center_z: int,
                       wall_radius: int,
                       height_map: np.ndarray,
                       terrain_map: np.ndarray,
                       scan_volume: np.ndarray,
                       ctx: ScanContext,
                       codec,
                       batch_size: int = 1024):
    """兼容入口（卡 5 起内部只调 fill_water_only）。

    保留这个名字是因为 city/builder.py 和外部调用方还在用它。卡 5 后的语义：
    只填水，不平整陆地。如果未来想恢复"整城找平"必须新写一个函数，不要走这。
    """
    print("[FOUNDATION] prepare_city_floor → fill_water_only "
          "（卡 5 起整城平整行为已移除，仅填水）", flush=True)
    return fill_water_only(center_x=center_x, center_z=center_z,
                           wall_radius=wall_radius,
                           height_map=height_map, terrain_map=terrain_map,
                           scan_volume=scan_volume,
                           ctx=ctx, codec=codec, batch_size=batch_size)
