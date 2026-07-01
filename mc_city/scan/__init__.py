"""世界扫描层：坐标系、扫描、高度图、方块分类、地形分析。"""

from .terrain_analysis import TerrainFeatures, analyze_terrain

__all__ = ["TerrainFeatures", "analyze_terrain"]
