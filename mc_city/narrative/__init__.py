"""叙事图层子包。

公开 API：
    BuildingMeta             —— 单栋建筑的叙事元数据契约（types.py）
    assign_narrative_metadata —— 把 build_city 结果转换为元数据列表（metadata.py）

任务 1.3 起会陆续加入 signs / books / streets 等模块。
"""
from .metadata import assign_narrative_metadata
from .types import BuildingMeta

__all__ = ["BuildingMeta", "assign_narrative_metadata"]
