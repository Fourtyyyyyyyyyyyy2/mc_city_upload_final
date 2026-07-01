"""方块 ID 工具：解码、分类、可穿过/水/树判定。"""
import numpy as np

from .codec import BlockCodec


AIR_BLOCK = "minecraft:air"

# 选址时视为可穿过的非固体方块（草、花、雪层等）。
# Bug 历史：旧版含 "minecraft:tulip" 不是合法名，1.21 服务器实际方块是
# white_tulip / orange_tulip / pink_tulip / red_tulip 4 种子类。
# Playtest 还发现 leaf_litter / wildflowers / bush / firefly_bush 等 1.21 新增
# 植被在山地随地散落，把大量 mid/outer 候选位置误判为"已占用"。
PASSTHROUGH_BLOCKS = {
    # 草本
    "minecraft:grass", "minecraft:tall_grass", "minecraft:short_grass",
    "minecraft:fern", "minecraft:large_fern", "minecraft:dead_bush",
    # 1.21 新增地表植被
    "minecraft:leaf_litter", "minecraft:wildflowers",
    "minecraft:bush", "minecraft:firefly_bush",
    # 花（1.20- 经典）
    "minecraft:poppy", "minecraft:dandelion", "minecraft:cornflower",
    "minecraft:azure_bluet", "minecraft:oxeye_daisy", "minecraft:allium",
    "minecraft:blue_orchid", "minecraft:lily_of_the_valley",
    "minecraft:sunflower", "minecraft:lilac", "minecraft:rose_bush",
    "minecraft:peony", "minecraft:pink_petals",
    # 4 种 tulip 子类（必须分别列出，没有 "minecraft:tulip" 这个方块）
    "minecraft:white_tulip", "minecraft:orange_tulip",
    "minecraft:pink_tulip", "minecraft:red_tulip",
    # 古迹/苗
    "minecraft:torchflower", "minecraft:pitcher_plant",
    "minecraft:spore_blossom", "minecraft:glow_lichen",
    "minecraft:moss_carpet", "minecraft:hanging_roots",
    # 树苗
    "minecraft:oak_sapling", "minecraft:birch_sapling",
    "minecraft:spruce_sapling", "minecraft:jungle_sapling",
    "minecraft:acacia_sapling", "minecraft:dark_oak_sapling",
    "minecraft:cherry_sapling", "minecraft:mangrove_propagule",
    "minecraft:azalea", "minecraft:flowering_azalea",
    # 雪 / 蜘蛛网
    "minecraft:snow_layer", "minecraft:snow", "minecraft:cobweb",
    # 水底植物
    "minecraft:seagrass", "minecraft:tall_seagrass",
    "minecraft:kelp", "minecraft:kelp_plant",
    "minecraft:sea_pickle",
    # 蘑菇
    "minecraft:mushroom", "minecraft:red_mushroom", "minecraft:brown_mushroom",
    # 藤蔓 / 杂项
    "minecraft:vine", "minecraft:glow_vines", "minecraft:cave_vines",
    "minecraft:cave_vines_plant", "minecraft:weeping_vines",
    "minecraft:twisting_vines",
    "minecraft:sugar_cane", "minecraft:bamboo_sapling", "minecraft:bamboo",
    # 自然生成的功能性方块（不算建筑物）
    "minecraft:bee_nest",
}

# 高度图：水域算作"表面找到"的方块
WATER_IDS = {"minecraft:water", "minecraft:flowing_water"}

# 城市底板、城墙处理时认作水的扩展集合
WATER_FLUIDS_EXTENDED = {
    "minecraft:water", "minecraft:flowing_water",
    "minecraft:kelp", "minecraft:kelp_plant",
    "minecraft:seagrass", "minecraft:tall_seagrass",
}

# 自然地形方块：is_location_occupied 不把这些算"占用"。
# 否则相邻列的山体方块（在候选列地表上方 1~10 格范围内）会把整片山坡判为占用，
# 让大量 mid/outer 候选被错拒（见 ~/playtest log: location_occupied=62/68）。
# 树（_log/_leaves）已被 clear_trees_in_scan 处理，不在此列。
NATURAL_TERRAIN_BLOCKS = {
    # stone / mountain
    "minecraft:stone", "minecraft:deepslate", "minecraft:gravel",
    "minecraft:cobblestone", "minecraft:andesite", "minecraft:diorite",
    "minecraft:granite", "minecraft:tuff", "minecraft:basalt",
    "minecraft:smooth_basalt", "minecraft:calcite",
    # dirt family
    "minecraft:grass_block", "minecraft:dirt", "minecraft:coarse_dirt",
    "minecraft:podzol", "minecraft:moss_block", "minecraft:mycelium",
    "minecraft:dirt_path", "minecraft:rooted_dirt", "minecraft:mud",
    "minecraft:muddy_mangrove_roots", "minecraft:clay",
    # sand / desert
    "minecraft:sand", "minecraft:red_sand", "minecraft:sandstone",
    "minecraft:red_sandstone",
    "minecraft:terracotta",
    "minecraft:white_terracotta", "minecraft:orange_terracotta",
    "minecraft:magenta_terracotta", "minecraft:light_blue_terracotta",
    "minecraft:yellow_terracotta", "minecraft:lime_terracotta",
    "minecraft:pink_terracotta", "minecraft:gray_terracotta",
    "minecraft:light_gray_terracotta", "minecraft:cyan_terracotta",
    "minecraft:purple_terracotta", "minecraft:blue_terracotta",
    "minecraft:brown_terracotta", "minecraft:green_terracotta",
    "minecraft:red_terracotta", "minecraft:black_terracotta",
    # snow / ice
    "minecraft:snow_block", "minecraft:powder_snow",
    "minecraft:ice", "minecraft:packed_ice", "minecraft:blue_ice",
    "minecraft:frosted_ice",
    # 水/岩浆
    "minecraft:water", "minecraft:flowing_water",
    "minecraft:lava", "minecraft:flowing_lava",
    # 底岩 / 地狱
    "minecraft:bedrock", "minecraft:netherrack",
}


def is_natural_terrain_block(bid: str) -> bool:
    """该方块是不是自然地形（不算"已占用"）。

    用名单 + 末缀启发。末缀覆盖所有 _ore 变种（含 deepslate_xxx_ore）。
    """
    if bid in NATURAL_TERRAIN_BLOCKS:
        return True
    if bid.endswith("_ore"):
        return True
    return False


def get_block_id(block, codec: BlockCodec = None) -> str:
    """从任意格式的方块取出干净的 minecraft:xxx 名字。

    支持 uint16 code、dict、str 三种格式。属性 [foo=bar] 会被剥掉。
    """
    if isinstance(block, (int, np.integer)):
        return codec.decode(int(block)) if codec else AIR_BLOCK
    if isinstance(block, dict):
        bid = block.get("id") or block.get("name") or block.get("Name") or AIR_BLOCK
    else:
        bid = str(block)
    if "[" in bid:
        bid = bid.split("[", 1)[0]
    return bid


def is_tree_block_id(bid: str) -> bool:
    """判定一个方块名是不是树的一部分（树干/树叶/藤蔓/根/azalea/竹子）。"""
    if not bid or bid == AIR_BLOCK:
        return False

    if bid.endswith("_log") or bid.endswith("_wood") or bid.endswith("_leaves"):
        return True

    return bid in {
        "minecraft:mangrove_roots",
        "minecraft:muddy_mangrove_roots",
        "minecraft:hanging_roots",
        "minecraft:vine",
        "minecraft:twisting_vines",
        "minecraft:weeping_vines",
        "minecraft:azalea_leaves",
        "minecraft:flowering_azalea_leaves",
        "minecraft:bamboo",
    }


def is_jungle_hint_block_id(bid: str) -> bool:
    """Return True for blocks that strongly suggest a jungle column."""
    if not bid or bid == AIR_BLOCK:
        return False
    return (
        "jungle_" in bid
        or bid in {
            "minecraft:bamboo",
            "minecraft:bamboo_sapling",
            "minecraft:cocoa",
            "minecraft:vine",
            "minecraft:melon",
            "minecraft:melon_stem",
            "minecraft:attached_melon_stem",
        }
    )


# 地表装饰植被：枯灌木/草/花/藤蔓等小植物。build_terrain_map 要**跳过**它们落到真实
# 地面（否则 badlands 上的 dead_bush 把 terracotta 盖住 → 误判 plains）。注意不含 snow/
# 雪层（雪原顶面就该判 snow）、不含 _leaves（树叶归 is_tree_block_id）。
_SURFACE_DECOR_BLOCKS = {
    "minecraft:dead_bush", "minecraft:bush", "minecraft:firefly_bush",
    "minecraft:short_grass", "minecraft:grass", "minecraft:tall_grass",
    "minecraft:fern", "minecraft:large_fern",
    "minecraft:short_dry_grass", "minecraft:tall_dry_grass",
    "minecraft:leaf_litter", "minecraft:pink_petals", "minecraft:wildflowers",
    "minecraft:dandelion", "minecraft:poppy", "minecraft:blue_orchid",
    "minecraft:allium", "minecraft:azure_bluet", "minecraft:red_tulip",
    "minecraft:orange_tulip", "minecraft:white_tulip", "minecraft:pink_tulip",
    "minecraft:oxeye_daisy", "minecraft:cornflower", "minecraft:lily_of_the_valley",
    "minecraft:wither_rose", "minecraft:torchflower", "minecraft:closed_eyeblossom",
    "minecraft:open_eyeblossom", "minecraft:sunflower", "minecraft:lilac",
    "minecraft:rose_bush", "minecraft:peony", "minecraft:pitcher_plant",
    "minecraft:sweet_berry_bush", "minecraft:sugar_cane", "minecraft:cactus",
    "minecraft:seagrass", "minecraft:tall_seagrass",
    "minecraft:kelp", "minecraft:kelp_plant",
}


def is_surface_decor_block(bid: str) -> bool:
    """该方块是不是地表小植被（枯灌木/草/花…），分类地形时应跳过落到真实地面。"""
    return bid in _SURFACE_DECOR_BLOCKS


def classify_surface(bid: str) -> str:
    """根据表面方块名推断地形类型：plains/desert/snow/mountain/water。"""
    if is_jungle_hint_block_id(bid):
        return "jungle"

    if bid in {"minecraft:water"}:
        return "water"

    if "terracotta" in bid or bid in {"minecraft:red_sand", "minecraft:red_sandstone"}:
        return "badlands"

    if bid in {"minecraft:sand", "minecraft:sandstone"}:
        return "desert"

    if bid in {"minecraft:snow", "minecraft:snow_block",
               "minecraft:powder_snow", "minecraft:ice",
               "minecraft:packed_ice", "minecraft:blue_ice"}:
        return "snow"

    if bid in {"minecraft:stone", "minecraft:deepslate",
               "minecraft:gravel", "minecraft:cobblestone",
               "minecraft:andesite", "minecraft:diorite", "minecraft:granite"}:
        return "mountain"

    if bid in {"minecraft:grass_block", "minecraft:dirt",
               "minecraft:coarse_dirt", "minecraft:podzol", "minecraft:moss_block"}:
        return "plains"

    return "plains"
