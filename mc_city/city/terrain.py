"""地形分类与材质映射。

地形枚举：0=plains, 1=desert, 2=mountain, 3=snow, 4=water
"""
from __future__ import annotations

import numpy as np

from ..mc.blocks import (
    AIR_BLOCK,
    classify_surface,
    get_block_id,
    is_jungle_hint_block_id,
    is_surface_decor_block,
    is_tree_block_id,
)
from ..mc.codec import BlockCodec

TERRAIN_NAMES = ["plains", "desert", "mountain", "snow", "water", "jungle", "badlands"]
TERRAIN_ENUM = {
    "plains": 0,
    "desert": 1,
    "mountain": 2,
    "snow": 3,
    "water": 4,
    "jungle": 5,
    "badlands": 6,
}

# 地形 → (柱身填充材质, 顶层材质)
_TERRAIN_FILL = {
    "desert":   ("minecraft:sandstone",  "minecraft:sand"),
    "snow":     ("minecraft:snow_block",  "minecraft:snow_block"),
    "mountain": ("minecraft:stone",       "minecraft:stone"),
    "water":    ("minecraft:gravel",      "minecraft:gravel"),
    "jungle":   ("minecraft:rooted_dirt", "minecraft:moss_block"),
    "badlands": ("minecraft:red_sandstone", "minecraft:red_sand"),
}


def get_terrain_fill_blocks(terrain_type: str) -> tuple[str, str]:
    """返回该地形对应的 (柱身块, 顶面块)。plains 走默认 dirt/grass。"""
    return _TERRAIN_FILL.get(terrain_type, ("minecraft:dirt", "minecraft:grass_block"))


def terrain_name_at(terrain_map: np.ndarray, sx: int, sz: int) -> str:
    """安全读取 terrain_map[sz, sx] 的地形名（越界返回 plains）。"""
    NZ, NX = terrain_map.shape
    if 0 <= sx < NX and 0 <= sz < NZ:
        t = int(terrain_map[sz, sx])
        return TERRAIN_NAMES[t] if t < len(TERRAIN_NAMES) else "plains"
    return "plains"


def get_terrain_material_at(terrain_map: np.ndarray,
                            sx: int, sz: int,
                            search_radius: int = 3) -> str:
    """获取 scan(sx, sz) 处的地形；如果是水，向外搜最近的非水地形。"""
    NZ, NX = terrain_map.shape

    if 0 <= sx < NX and 0 <= sz < NZ:
        name = terrain_name_at(terrain_map, sx, sz)
        if name != "water":
            return name

    for r in range(1, search_radius + 1):
        for dx in range(-r, r + 1):
            for dz in range(-r, r + 1):
                if max(abs(dx), abs(dz)) != r:
                    continue
                nx_, nz_ = sx + dx, sz + dz
                if 0 <= nx_ < NX and 0 <= nz_ < NZ:
                    name = terrain_name_at(terrain_map, nx_, nz_)
                    if name != "water":
                        return name

    return "plains"


def build_terrain_map(scan_volume: np.ndarray,
                      codec: BlockCodec = None) -> np.ndarray:
    """对每个 (z, x) 列从上向下找第一个非空气、非树木方块，按 classify_surface 分类。

    向量化的 uint16 快速路径 vs. 纯 Python fallback，前者快约 200x。
    返回 (NZ, NX) uint8 数组。
    """
    NY, NZ, NX = scan_volume.shape

    if codec is not None:
        # 跳过树木 + 地表小植被（dead_bush/草/花…）→ 落到真实地面分类
        # （否则 badlands 的 dead_bush 把 terracotta 盖住 → 误判 plains）。
        skip_names = [n for n in codec.name_to_code
                      if is_tree_block_id(n.split("[", 1)[0])
                      or is_surface_decor_block(n.split("[", 1)[0])]
        skip_codes = set(codec.codes_for_names(skip_names))
        skip_codes.add(codec.AIR_CODE)
        is_compact = scan_volume.dtype == np.uint16
    else:
        skip_codes = set()
        is_compact = False

    result = np.zeros((NZ, NX), dtype=np.uint8)

    if is_compact:
        jungle_names = [n for n in codec.name_to_code if is_jungle_hint_block_id(n)]
        jungle_codes = set(codec.codes_for_names(jungle_names))
        if jungle_codes:
            jungle_hint = np.isin(scan_volume, list(jungle_codes)).any(axis=0)
        else:
            jungle_hint = np.zeros((NZ, NX), dtype=bool)

        skip_arr = np.isin(scan_volume, list(skip_codes))
        terrain_idx = np.full((NZ, NX), -1, dtype=np.int32)
        for y in range(NY - 1, -1, -1):
            unfound = terrain_idx == -1
            solid = ~skip_arr[y]
            terrain_idx[unfound & solid] = y

        found_mask = terrain_idx >= 0
        zs_idx, xs_idx = np.where(found_mask)
        if len(zs_idx) > 0:
            ys_idx = terrain_idx[found_mask]
            block_codes = scan_volume[ys_idx, zs_idx, xs_idx]
            for i in range(len(zs_idx)):
                name = codec.decode(int(block_codes[i]))
                t = classify_surface(name)
                if t == "plains" and jungle_hint[zs_idx[i], xs_idx[i]]:
                    t = "jungle"
                result[zs_idx[i], xs_idx[i]] = TERRAIN_ENUM.get(t, 0)
        return result

    # fallback
    for zs in range(NZ):
        for xs in range(NX):
            surface_id = AIR_BLOCK
            jungle_hint = False
            for yy in range(NY - 1, -1, -1):
                bid = get_block_id(scan_volume[yy, zs, xs], codec)
                if is_jungle_hint_block_id(bid):
                    jungle_hint = True
                if (bid != AIR_BLOCK and not is_tree_block_id(bid)
                        and not is_surface_decor_block(bid)):
                    surface_id = bid
                    break
            t = classify_surface(surface_id)
            if t == "plains" and jungle_hint:
                t = "jungle"
            result[zs, xs] = TERRAIN_ENUM.get(t, 0)
    return result
