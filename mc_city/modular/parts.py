"""建筑部件生成器（内存版）。

所有函数返回 list of {"x", "y", "z", "id"}，世界坐标，可直接传给 set_blocks_batch。
"""
from typing import List


MATERIAL_SETS = {
    "plains": {
        "wall":        "minecraft:stone_bricks",
        "wall_accent": "minecraft:mossy_stone_bricks",
        "floor":       "minecraft:stone_brick_slab",
        "foundation":  "minecraft:cobblestone",
        "roof":        "minecraft:stone_brick_stairs",
        "roof_flat":   "minecraft:stone_bricks",
        "window":      "minecraft:glass_pane",
        "door":        "minecraft:oak_door",
        "beam":        "minecraft:oak_log",
    },
    "desert": {
        "wall":        "minecraft:sandstone",
        "wall_accent": "minecraft:chiseled_sandstone",
        "floor":       "minecraft:sandstone_slab",
        "foundation":  "minecraft:sandstone",
        "roof":        "minecraft:sandstone_stairs",
        "roof_flat":   "minecraft:smooth_sandstone",
        "window":      "minecraft:glass_pane",
        "door":        "minecraft:acacia_door",
        "beam":        "minecraft:acacia_log",
    },
    "snow": {
        "wall":        "minecraft:smooth_stone",
        "wall_accent": "minecraft:packed_ice",
        "floor":       "minecraft:smooth_stone_slab",
        "foundation":  "minecraft:stone",
        "roof":        "minecraft:smooth_stone_slab",
        "roof_flat":   "minecraft:smooth_stone",
        "window":      "minecraft:glass_pane",
        "door":        "minecraft:spruce_door",
        "beam":        "minecraft:spruce_log",
    },
    "mountain": {
        "wall":        "minecraft:deepslate_bricks",
        "wall_accent": "minecraft:chiseled_deepslate",
        "floor":       "minecraft:deepslate_brick_slab",
        "foundation":  "minecraft:deepslate",
        "roof":        "minecraft:deepslate_brick_stairs",
        "roof_flat":   "minecraft:deepslate_bricks",
        "window":      "minecraft:glass_pane",
        "door":        "minecraft:dark_oak_door",
        "beam":        "minecraft:dark_oak_log",
    },
    "jungle": {
        "wall":        "minecraft:mossy_stone_bricks",
        "wall_accent": "minecraft:bamboo_planks",
        "floor":       "minecraft:jungle_slab",
        "foundation":  "minecraft:mossy_cobblestone",
        "roof":        "minecraft:bamboo_stairs",
        "roof_flat":   "minecraft:bamboo_planks",
        "window":      "minecraft:glass_pane",
        "door":        "minecraft:jungle_door",
        "beam":        "minecraft:jungle_log",
    },
    "badlands": {
        "wall":        "minecraft:red_sandstone",
        "wall_accent": "minecraft:chiseled_red_sandstone",
        "floor":       "minecraft:red_sandstone_slab",
        "foundation":  "minecraft:red_sandstone",
        "roof":        "minecraft:red_sandstone_stairs",
        "roof_flat":   "minecraft:smooth_red_sandstone",
        "window":      "minecraft:glass_pane",
        "door":        "minecraft:acacia_door",
        "beam":        "minecraft:stripped_acacia_log",
    },
}


def get_materials(terrain_type: str) -> dict:
    return MATERIAL_SETS.get(terrain_type, MATERIAL_SETS["plains"])


def gen_foundation_column(wx: int, wz: int,
                          ground_y: int, base_y: int,
                          mat: dict) -> List[dict]:
    """单根地基柱，从 ground_y（含）填到 base_y-1。ground_y >= base_y 时返回 []。"""
    return [{"x": wx, "y": y, "z": wz, "id": mat["foundation"]}
            for y in range(ground_y, base_y)]


def gen_wall_section(wx: int, wz: int,
                     y_bottom: int,
                     floor_height: int,
                     face: str,
                     is_corner: bool,
                     has_window: bool,
                     has_door: bool,
                     mat: dict) -> List[dict]:
    """一格宽 × floor_height 高的墙面单元。"""
    blocks = []
    for dy in range(floor_height):
        y = y_bottom + dy
        if is_corner:
            block_id = mat["wall_accent"]
        elif has_door and dy <= 1:
            half = "lower" if dy == 0 else "upper"
            block_id = f'{mat["door"]}[half={half},facing={face}]'
        elif has_window and dy == floor_height // 2:
            block_id = f'{mat["window"]}[{_window_props(face)}]'
        else:
            block_id = mat["wall"]
        blocks.append({"x": wx, "y": y, "z": wz, "id": block_id})
    return blocks


def _window_props(face: str) -> str:
    if face in ("north", "south"):
        return "east=true,west=true,north=false,south=false"
    return "north=true,south=true,east=false,west=false"


def gen_floor_plate(x0: int, x1: int, z0: int, z1: int,
                    y: int, mat: dict) -> List[dict]:
    return [{"x": x, "y": y, "z": z, "id": mat["floor"]}
            for x in range(x0, x1 + 1) for z in range(z0, z1 + 1)]


def gen_roof_flat(x0: int, x1: int, z0: int, z1: int,
                  y: int, mat: dict) -> List[dict]:
    """平屋顶 + 边缘矮墙。"""
    blocks = []
    for x in range(x0, x1 + 1):
        for z in range(z0, z1 + 1):
            blocks.append({"x": x, "y": y, "z": z, "id": mat["roof_flat"]})
            if x == x0 or x == x1 or z == z0 or z == z1:
                blocks.append({"x": x, "y": y + 1, "z": z, "id": mat["wall_accent"]})
    return blocks


def gen_roof_gabled(x0: int, x1: int, z0: int, z1: int,
                    y_base: int, mat: dict) -> List[dict]:
    """人字屋顶，沿 X 方向延伸，屋脊在 Z 中线。"""
    blocks = []
    width_z = z1 - z0 + 1
    half_z = width_z // 2

    for x in range(x0, x1 + 1):
        for step in range(half_z + 1):
            z_left = z0 + step
            z_right = z1 - step
            y = y_base + step

            if z_left <= z1:
                facing = "east"
                block_id = f'{mat["roof"]}[facing={facing},half=bottom,shape=straight]'
                blocks.append({"x": x, "y": y, "z": z_left, "id": block_id})
            if z_right >= z0 and z_right != z_left:
                facing = "west"
                block_id = f'{mat["roof"]}[facing={facing},half=bottom,shape=straight]'
                blocks.append({"x": x, "y": y, "z": z_right, "id": block_id})
    return blocks
