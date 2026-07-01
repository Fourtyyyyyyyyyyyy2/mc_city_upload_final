"""块名 → 类别 ID 的映射（用于体素可视化）。

按需加载 JSON 表，找不到表时退化到一个空映射 + UNKNOWN_CLASS_ID。
真正的"表面地形分类"函数 classify_surface 在 mc/blocks.py 里。
"""
import json
import os

from ..config import BLOCK_CLASS_MAP_PATH

UNKNOWN_CLASS_ID = 1

if os.path.exists(BLOCK_CLASS_MAP_PATH):
    with open(BLOCK_CLASS_MAP_PATH, "r") as f:
        block_class_map = json.load(f)
else:
    print(f"⚠ 未找到 {BLOCK_CLASS_MAP_PATH}，使用空映射")
    block_class_map = {}


def classify(name: str) -> int:
    """根据 block 名称返回对应 class_id；未知方块返回 UNKNOWN_CLASS_ID。"""
    return block_class_map.get(name, UNKNOWN_CLASS_ID)
