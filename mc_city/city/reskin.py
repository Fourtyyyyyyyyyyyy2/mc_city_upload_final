"""按地形给建筑/树做方块材质重映射（reskin）。

config.TERRAIN_RESKIN_THEMES 给每地形选「木系/石系」主题，这里展开成
base_id -> base_id 替换表，再包成一个**保留方块状态**的 bid->bid 函数。
paste_volume 收到该函数后逐块替换 → 任意风格的房子"入乡随俗"。

设计要点：
- 木系自动扩展（oak/spruce/... 全套后缀 + stripped 变体），主题木种留原样。
- 石系显式列表，只映射到**确实存在**的变体（stone 无 wall → 不映射 wall）。
- 替换只换基础 id，方块状态 [facing=...,half=...] 原样拼回。
"""
from __future__ import annotations

from ..config import (
    EYE_KING_DARK_RESKIN,
    EYE_KING_DARK_RESKIN_ENABLED,
    TERRAIN_RESKIN_ENABLED,
    TERRAIN_RESKIN_THEMES,
    TERRAIN_TREE_REMAP,
    TERRAIN_WISH_TREE_REMAP,
)

# 可整体替换的木种（含完整后缀族）。crimson/warped 是菌类（stem/hyphae，命名不同）
# 故排除，避免造出不存在的方块。
_WOOD_BASES = ("oak", "spruce", "birch", "jungle", "acacia",
               "dark_oak", "mangrove", "cherry")
_WOOD_SUFFIXES = ("planks", "log", "wood", "stairs", "slab", "fence",
                  "fence_gate", "door", "trapdoor", "button",
                  "pressure_plate", "sign", "hanging_sign", "leaves")


def _wood_remap(target: str) -> dict[str, str]:
    """把所有非目标木种的木系方块映射到目标木种（含 stripped 变体）。"""
    out: dict[str, str] = {}
    for base in _WOOD_BASES:
        if base == target:
            continue
        for suf in _WOOD_SUFFIXES:
            out[f"minecraft:{base}_{suf}"] = f"minecraft:{target}_{suf}"
        out[f"minecraft:stripped_{base}_log"] = f"minecraft:stripped_{target}_log"
        out[f"minecraft:stripped_{base}_wood"] = f"minecraft:stripped_{target}_wood"
    return out


# 石系主题：显式列出，只用真实存在的变体（1.21）。
_STONE_REMAP: dict[str, dict[str, str]] = {
    "red_sandstone": {
        "minecraft:cobblestone": "minecraft:red_sandstone",
        "minecraft:cobblestone_stairs": "minecraft:red_sandstone_stairs",
        "minecraft:cobblestone_slab": "minecraft:red_sandstone_slab",
        "minecraft:cobblestone_wall": "minecraft:red_sandstone_wall",
        "minecraft:stone": "minecraft:red_sandstone",
        "minecraft:stone_bricks": "minecraft:cut_red_sandstone",
        "minecraft:stone_brick_stairs": "minecraft:red_sandstone_stairs",
        "minecraft:stone_brick_slab": "minecraft:red_sandstone_slab",
        "minecraft:stone_brick_wall": "minecraft:red_sandstone_wall",
        "minecraft:stone_stairs": "minecraft:red_sandstone_stairs",
        "minecraft:stone_slab": "minecraft:red_sandstone_slab",
    },
    "sandstone": {
        "minecraft:cobblestone": "minecraft:sandstone",
        "minecraft:cobblestone_stairs": "minecraft:sandstone_stairs",
        "minecraft:cobblestone_slab": "minecraft:sandstone_slab",
        "minecraft:cobblestone_wall": "minecraft:sandstone_wall",
        "minecraft:stone": "minecraft:sandstone",
        "minecraft:stone_bricks": "minecraft:cut_sandstone",
        "minecraft:stone_brick_stairs": "minecraft:sandstone_stairs",
        "minecraft:stone_brick_slab": "minecraft:sandstone_slab",
        "minecraft:stone_brick_wall": "minecraft:sandstone_wall",
        "minecraft:stone_stairs": "minecraft:sandstone_stairs",
        "minecraft:stone_slab": "minecraft:sandstone_slab",
    },
    "stone_bricks": {
        "minecraft:cobblestone": "minecraft:stone_bricks",
        "minecraft:cobblestone_stairs": "minecraft:stone_brick_stairs",
        "minecraft:cobblestone_slab": "minecraft:stone_brick_slab",
        "minecraft:cobblestone_wall": "minecraft:stone_brick_wall",
        "minecraft:stone": "minecraft:stone_bricks",
    },
    "mossy_cobblestone": {
        "minecraft:cobblestone": "minecraft:mossy_cobblestone",
        "minecraft:cobblestone_stairs": "minecraft:mossy_cobblestone_stairs",
        "minecraft:cobblestone_slab": "minecraft:mossy_cobblestone_slab",
        "minecraft:cobblestone_wall": "minecraft:mossy_cobblestone_wall",
        "minecraft:stone_bricks": "minecraft:mossy_stone_bricks",
        "minecraft:stone_brick_stairs": "minecraft:mossy_stone_brick_stairs",
        "minecraft:stone_brick_slab": "minecraft:mossy_stone_brick_slab",
        "minecraft:stone_brick_wall": "minecraft:mossy_stone_brick_wall",
    },
    "stone": {
        "minecraft:cobblestone": "minecraft:stone",
        "minecraft:cobblestone_stairs": "minecraft:stone_stairs",
        "minecraft:cobblestone_slab": "minecraft:stone_slab",
        # stone 无 wall → cobblestone_wall 保留不换
    },
    # 火山：黑石（blackstone）+ 磨制黑石砖。变体在 1.21 齐全（stairs/slab/wall 都有）。
    "blackstone": {
        "minecraft:cobblestone": "minecraft:blackstone",
        "minecraft:cobblestone_stairs": "minecraft:blackstone_stairs",
        "minecraft:cobblestone_slab": "minecraft:blackstone_slab",
        "minecraft:cobblestone_wall": "minecraft:blackstone_wall",
        "minecraft:stone": "minecraft:blackstone",
        "minecraft:stone_bricks": "minecraft:polished_blackstone_bricks",
        "minecraft:stone_brick_stairs": "minecraft:polished_blackstone_brick_stairs",
        "minecraft:stone_brick_slab": "minecraft:polished_blackstone_brick_slab",
        "minecraft:stone_brick_wall": "minecraft:polished_blackstone_brick_wall",
        "minecraft:stone_stairs": "minecraft:blackstone_stairs",
        "minecraft:stone_slab": "minecraft:blackstone_slab",
    },
    # 海上：深海晶（dark_prismarine）+ 海晶砖。dark_prismarine 无 wall → 用 prismarine_wall。
    "dark_prismarine": {
        "minecraft:cobblestone": "minecraft:dark_prismarine",
        "minecraft:cobblestone_stairs": "minecraft:dark_prismarine_stairs",
        "minecraft:cobblestone_slab": "minecraft:dark_prismarine_slab",
        "minecraft:cobblestone_wall": "minecraft:prismarine_wall",
        "minecraft:stone": "minecraft:dark_prismarine",
        "minecraft:stone_bricks": "minecraft:prismarine_bricks",
        "minecraft:stone_brick_stairs": "minecraft:prismarine_brick_stairs",
        "minecraft:stone_brick_slab": "minecraft:prismarine_brick_slab",
        "minecraft:stone_brick_wall": "minecraft:prismarine_wall",
        "minecraft:stone_stairs": "minecraft:dark_prismarine_stairs",
        "minecraft:stone_slab": "minecraft:dark_prismarine_slab",
    },
}


def _build_table(terrain: str) -> dict[str, str]:
    theme = TERRAIN_RESKIN_THEMES.get(terrain)
    if not theme:
        return {}
    table: dict[str, str] = {}
    wood = theme.get("wood")
    if wood:
        table.update(_wood_remap(wood))
    stone = theme.get("stone")
    if stone:
        stone_table = _STONE_REMAP.get(stone)
        if stone_table:
            table.update(stone_table)
        else:
            print(f"[reskin] 未知石系主题 {stone!r}（地形 {terrain}），已跳过石系替换")
    return table


def _suffix(bid: str) -> str:
    """方块名末段（minecraft:spruce_leaves -> leaves），用于判同类。"""
    return bid.split(":", 1)[-1].rsplit("_", 1)[-1]


def _make_state_preserving(table: dict[str, str]):
    """把 base_id 替换表包成 bid->bid 函数。

    只有源/目标"同类"（末段相同，如 leaves->leaves、wood->wood、stairs->stairs）才保留
    方块状态；跨类（leaves->ice / leaves->air）丢状态，避免 ice[distance=7] 这种非法块。
    """
    def remap(bid: str) -> str:
        if not bid:
            return bid
        br = bid.find("[")
        if br == -1:
            base, state = bid, ""
        else:
            base, state = bid[:br], bid[br:]
        new = table.get(base)
        if not new:
            return bid
        if state and _suffix(base) == _suffix(new):
            return new + state
        return new                          # 跨类/无状态 → 不带状态
    return remap


_CACHE: dict[str, object] = {}


def make_remap(terrain: str | None):
    """返回该地形的建筑材质替换函数（保留方块状态）；无主题/未启用返回 None。"""
    if not TERRAIN_RESKIN_ENABLED or not terrain:
        return None
    if terrain in _CACHE:
        return _CACHE[terrain]
    table = _build_table(terrain)
    fn = _make_state_preserving(table) if table else None
    _CACHE[terrain] = fn
    return fn


def make_tree_remap(terrain: str | None):
    """树（灵魂树/景观树）专用替换；无主题/未启用返回 None。卡 14.2 接线。"""
    if not TERRAIN_RESKIN_ENABLED or not terrain:
        return None
    table = TERRAIN_TREE_REMAP.get(terrain)
    if not table:
        return None
    return _make_state_preserving(table)


def make_wish_tree_remap(terrain: str | None):
    """许愿树（小图核心）专用地形换材质；无主题/未启用返回 None。

    许愿树主材与灵魂树(cherry_*)不同(spruce/moss/pink_*)，故用独立的
    TERRAIN_WISH_TREE_REMAP 表。plains 不设主题 → None，保留原生粉花许愿树。
    """
    if not TERRAIN_RESKIN_ENABLED or not terrain:
        return None
    table = TERRAIN_WISH_TREE_REMAP.get(terrain)
    if not table:
        return None
    return _make_state_preserving(table)


def make_eye_king_remap(base_remap=None):
    """Return the Eye King dark statue remap, optionally chained after terrain remap."""
    dark = (_make_state_preserving(EYE_KING_DARK_RESKIN)
            if EYE_KING_DARK_RESKIN_ENABLED else None)
    if dark is None:
        return base_remap
    if base_remap is None:
        return dark

    def remap(bid: str) -> str:
        return dark(base_remap(bid))

    return remap


__all__ = [
    "make_remap",
    "make_tree_remap",
    "make_wish_tree_remap",
    "make_eye_king_remap",
]
