"""Grid Layout 子包（Priority 2 卡 9.1+）。

中心广场 + 中轴主道 + grid 街区切分 + 街区驱动 placement。

公开 API：
    build_central_plaza         —— 卡 9.1 灵魂树前广场（环）
    build_cardinal_axes         —— 卡 9.1 4 条 cardinal 主道
    plaza_mask / plaza_outer_radius —— 卡 9.1 几何工具
    BlockRegion / enumerate_blocks  —— 卡 9.2 街区切分（纯数据）
    GUILD_BLOCK_DECOR_RECIPE / decorate_blocks —— 卡 9.4 主殿前广场装饰
"""
from .block_decor import GUILD_BLOCK_DECOR_RECIPE, decorate_blocks
from .blocks import BlockRegion, enumerate_blocks
from .cardinal_road import build_cardinal_axes
from .grid_streets import render_grid_streets
from .plaza import build_central_plaza, plaza_mask, plaza_outer_radius

__all__ = [
    "build_central_plaza", "plaza_mask", "plaza_outer_radius",
    "build_cardinal_axes",
    "BlockRegion", "enumerate_blocks",
    "GUILD_BLOCK_DECOR_RECIPE", "decorate_blocks",
    "render_grid_streets",
]
