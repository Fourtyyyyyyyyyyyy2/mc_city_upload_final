"""扫描坐标系 ↔ 世界坐标 的换算上下文。

ScanContext 同时充当 build pipeline 的"伴生状态包"——卡 2 之后下游会
往它上面挂 terrain_features、ring_masks 等只读快照。本来是 frozen=True，
卡 2 起改成普通 dataclass，因为：
  1) 全代码库没有把 ctx 当 dict key / set 元素的地方（已审核）。
  2) 任务卡 2/3/4/5 一致要求 ctx.xxx = yyy，多卡共享一份选址/圈层结果。

约定：origin_x / origin_z / min_y 三个核心字段在创建后不要再改；如果需要
不同的 scan 区，重新构造一个新的 ctx，不要原地修改这三项。挂上去的 feature
字段是单次构建的快照，下次重建前应该置 None 或新建 ctx。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    # 仅类型注解用，避免运行时循环 import。
    from .terrain_analysis import TerrainFeatures
    from ..city.dimensions import CityDims


@dataclass
class ScanContext:
    """记录扫描原点（scan[0,0,0] 对应的世界坐标）+ 可选的派生数据。

    scan_volume.shape = (NY, NZ, NX)，scan(x,z) → world(x + origin_x, z + origin_z)
    scan y_idx → world (y_idx + min_y)。
    """
    origin_x: int
    origin_z: int
    min_y: int

    # ── 派生数据，由后续 pass 填充 ────────────────────────────────
    # terrain_features 由 scan.terrain_analysis.analyze_terrain 产出（卡 1/2）。
    # ring_masks 由 city.rings.grow_organic_rings 产出（卡 3）。
    # 字段类型故意用 Optional[Any]，避免运行时强制 import 重型依赖；
    # 真实类型由 TYPE_CHECKING 块里的别名提示给 IDE。
    terrain_features: Optional[Any] = field(default=None, repr=False, compare=False)
    ring_masks: Optional[Any] = field(default=None, repr=False, compare=False)
    # city_dims 由 city.dimensions.compute_city_dims 产出（卡 10.2）：按 build area
    # 尺寸派生的全部圈层/墙/广场半径快照，供选址/圈层/grid 消费。
    city_dims: Optional[Any] = field(default=None, repr=False, compare=False)

    def w2s(self, xw: int, zw: int) -> tuple[int, int]:
        """world → scan (x, z)"""
        return xw - self.origin_x, zw - self.origin_z

    def s2w(self, xs: int, zs: int) -> tuple[int, int]:
        """scan → world (x, z)"""
        return xs + self.origin_x, zs + self.origin_z
