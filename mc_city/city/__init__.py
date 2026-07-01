"""城市生成层。

入口：build_city（city.builder.build_city）。流程由 city.builder 编排，
具体步骤分散在 center / terrain / suitability / foundation / trees /
placement / wall / components 里。
"""
from .builder import build_city  # noqa: F401
from .center import find_best_city_center, find_dramatic_center  # noqa: F401
from .dimensions import CityDims, compute_city_dims  # noqa: F401
