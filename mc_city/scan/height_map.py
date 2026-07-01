"""从扫描得到的 3D 体素数据生成 2D 高度图。

关键：默认 skip_trees=True，跳过树干/树叶/竹子等"非地形"方块。
否则在森林区 height_map 会记录树冠顶部而不是真实地面，
后续清树过滤、道路渲染、建筑选址都会被带歪。
水柱仍然算作"表面找到"——水面高度被记录。
"""
from __future__ import annotations

import numpy as np

from ..mc.blocks import (
    AIR_BLOCK, WATER_IDS, is_surface_decor_block, is_tree_block_id,
)
from ..mc.codec import BlockCodec


def generate_height_map(scan_volume: np.ndarray,
                        min_y: int = -64,
                        codec: BlockCodec = None,
                        skip_trees: bool = True) -> np.ndarray:
    """对每个 (z, x) 列从上向下找第一个非空气（默认也跳过树木）方块。

    Args:
        scan_volume: (NY, NZ, NX)
        min_y:       scan y=0 对应的世界 Y
        codec:       传入则按 uint16 快速路径处理
        skip_trees:  True 表示忽略 _log/_wood/_leaves 等树木方块，
                     得到真正的地面高度（推荐）

    Returns:
        height_map: (NZ, NX) int32，世界坐标 Y。
                    水柱的表面 Y 是水面，全空气/全树木的列保持为 min_y。
    """
    NY, NZ, NX = scan_volume.shape
    height_map = np.full((NZ, NX), min_y, dtype=np.int32)

    is_compact = (codec is not None and scan_volume.dtype == np.uint16)

    if is_compact:
        return _generate_compact(scan_volume, min_y, codec, skip_trees)

    return _generate_fallback(scan_volume, height_map, min_y, codec, skip_trees)


def _generate_compact(scan_volume: np.ndarray,
                      min_y: int,
                      codec: BlockCodec,
                      skip_trees: bool) -> np.ndarray:
    """uint16 矢量化路径。

    思路：构造 is_surface mask（非空气、非树木）→ 沿 Y 轴反向 argmax
    找到从顶部往下第一个 True 的位置 → 反推回原始 Y 索引。

    撞顶检测：若顶层 (yi=NY-1) 本身是 surface，说明地形其实延伸到扫描区之外，
    高度被截断了。这种列会被标记为 min_y（与"无 surface"同样表示无效），
    避免下游把建筑放在被截天花板的山顶 → 悬空。
    """
    NY, NZ, NX = scan_volume.shape

    skip_codes: set[int] = {codec.AIR_CODE}
    if skip_trees:
        # 跳树 + 地表小植被（dead_bush/草/花…）→ 落到真实地面高度，不被植被抬 1 格。
        for name, code in codec.name_to_code.items():
            base = name.split("[", 1)[0]
            if is_tree_block_id(base) or is_surface_decor_block(base):
                skip_codes.add(int(code))

    is_surface = ~np.isin(scan_volume, list(skip_codes))  # (NY, NZ, NX) bool

    # 沿 Y 轴反转，使索引 0 = 最高层；argmax 取首个 True 即为顶部表面
    rev = is_surface[::-1, :, :]
    first_true_from_top = np.argmax(rev, axis=0)         # (NZ, NX)
    top_y_idx = (NY - 1) - first_true_from_top           # 还原到原始 Y 索引
    has_surface = rev.any(axis=0)                        # 列里有没有 surface

    height_map = np.full((NZ, NX), min_y, dtype=np.int32)
    height_map[has_surface] = top_y_idx[has_surface] + min_y

    # 顶层是实体 = 撞天花板，真实地形高度未知，按无效列处理
    ceiling_hit = is_surface[-1, :, :]
    height_map[ceiling_hit] = min_y

    surface_count = int(np.sum(has_surface & ~ceiling_hit))
    truncated_count = int(np.sum(ceiling_hit))
    total = NZ * NX
    # 求真实有效区的 min/max（排除 min_y sentinel）
    valid_mask = height_map > min_y
    if valid_mask.any():
        valid_min = int(height_map[valid_mask].min())
        valid_max = int(height_map[valid_mask].max())
        range_str = f"min/max={valid_min}/{valid_max}"
    else:
        range_str = "min/max=N/A（无有效列）"
    print(f"🌄 Height map: surface found in {surface_count}/{total} 列，"
          f"{range_str}  (skip_trees={skip_trees})")
    if truncated_count:
        print(f"   ⚠️  {truncated_count}/{total} 列地形超出扫描天花板 "
              f"(y={min_y + NY - 1})，已标记为无效。"
              f"如要把这些区域纳入选址，请抬高 main.py 的 y2。")
    return height_map


def _generate_fallback(scan_volume: np.ndarray,
                       height_map: np.ndarray,
                       min_y: int,
                       codec: BlockCodec,
                       skip_trees: bool) -> np.ndarray:
    """非 uint16（dict/str 对象数组）路径。慢但兼容旧数据。"""
    NY, NZ, NX = scan_volume.shape

    for z in range(NZ):
        for x in range(NX):
            for y in reversed(range(NY)):
                block = scan_volume[y, z, x]

                if isinstance(block, (int, np.integer)):
                    code = int(block)
                    if code == 0:
                        continue
                    if skip_trees and codec is not None:
                        name = codec.decode(code).split("[", 1)[0]
                        if is_tree_block_id(name) or is_surface_decor_block(name):
                            continue
                    height_map[z, x] = y + min_y
                    break

                if isinstance(block, dict):
                    bid = block.get("id", AIR_BLOCK)
                else:
                    bid = str(block)

                if bid == AIR_BLOCK or bid == "0":
                    continue
                bid_base = bid.split("[", 1)[0]
                if skip_trees and (is_tree_block_id(bid_base)
                                   or is_surface_decor_block(bid_base)):
                    continue
                height_map[z, x] = y + min_y
                break

    print(f"🌄 Height map min/max: {int(np.min(height_map))}/{int(np.max(height_map))}"
          f"  (skip_trees={skip_trees}, fallback path)")
    return height_map
