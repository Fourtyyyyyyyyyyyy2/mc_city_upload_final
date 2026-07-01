"""模块化建筑：动态生成的住宅 / 商铺。

入口：build_modular_ring(ring_name, r_min, r_max, suitability_map, ...)
内部依赖 parts.gen_* 生成各类部件方块。
"""
from .builder import build_modular_ring  # noqa: F401
