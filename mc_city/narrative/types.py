"""叙事图层的数据契约。

本模块只定义数据结构，不做任何 IO / HTTP / 计算。
实际的元数据分配在 metadata.py。
"""
from dataclasses import dataclass
from typing import Optional


@dataclass
class BuildingMeta:
    """一栋建筑的叙事元数据。

    每个字段的取值范围（字段顺序与字段含义解释一致）：

    - origin: (x, y, z) 世界坐标，建筑左下角原点。来自 build_city 返回的
      info["origin"]（特征建筑）或 modular_buildings 列表的同名字段。

    - ring: 圈层标签，取值之一：
        "inner"   inner 圈特征建筑（6 栋公会主殿/灵核/市政等）
        "mid"     mid 圈特征建筑
        "outer"   outer 圈特征建筑
        "modular" 模块化建筑（住宅/商铺，不属于任何特征圈层）

    - role: 角色字符串。任务 1.1 的占位实现统一返回 "placeholder"。
      任务 1.2 起会扩展为具体角色，例如：
        "soul_academy_main"      学院主殿
        "soul_engineers_main"    工程院主殿
        "merchants_main"         商人协会主殿
        "adventurers_main"       冒险者协会主殿
        "soul_core_exchange"     灵核交易所
        "city_hall"              市政厅
        "house" / "warehouse" / "watchtower" / "guild_workshop" / ...

    - guild: 所属公会，可为 None（不属于任何公会，如 city_hall）。
      取值之一：
        "soul_scholars"  学者公会
        "soul_engineers" 工程院
        "merchants"      商人协会
        "adventurers"    冒险者协会
        None             无公会归属

    - name: 显示名（中文 OK），用于告示牌/书本。
      占位实现里给一个可识别的占位名，1.2 起由 names.py 生成。

    - founder_year: 灵历建立年份。负数表示更早（如 -120 = 灵历前 120 年）。
      灵核为 0 年；公会立约约 -200 ~ -150 年；inner 主殿 ~ -100 年；
      mid/outer 建筑 ~ -50 ~ 0 年；战后重建为正数。

    - ruin_severity: 毁伤程度，浮点数 0.0~1.0。
        0.0  完好
        0.3  轻度损毁（墙面破损、屋瓦缺失）
        0.6  严重损毁（局部坍塌）
        1.0  夷为平地
      实际渲染时由后续图层使用，不在 1.1 里消费。
    """
    origin: tuple[int, int, int]
    ring: str
    role: str
    guild: Optional[str]
    name: str
    founder_year: int
    ruin_severity: float