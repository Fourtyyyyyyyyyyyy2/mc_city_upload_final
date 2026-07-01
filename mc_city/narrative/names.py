"""叙事图层：建筑名称生成。

- ROLE_NAMES / GUILD_NAMES：role / guild 到中文显示名的映射，
  上层（告示牌、书本、街道）直接查表用。
- generate_building_name：根据 BuildingMeta 合成可重现的中文名。
  随机源 seed = 经典 3D 坐标哈希 (origin)，同一坐标永远得到同一名字。
"""
import random
from typing import Optional

from .types import BuildingMeta

# ── 显示名查表 ─────────────────────────────────────────────────────────
ROLE_NAMES: dict[str, str] = {
    # inner 圈定名建筑
    "soul_academy_main":   "Hall of Scholars",
    "soul_engineers_main": "Engineers' Foundry",
    "merchants_main":      "Merchants' Guildhall",
    "adventurers_main":    "Adventurers' Hall",
    "soul_core_exchange":  "Soul Core Exchange",
    "city_hall":           "City Hall",
    # mid 圈
    "guild_branch":        "Guild Branch",
    "guild_workshop":      "Workshop",
    "shop":                "Shop",
    "inn":                 "Inn",
    # outer 圈 & 模块化
    "house":               "House",
    "warehouse":           "Warehouse",
    "watchtower":          "Watchtower",
    # 兜底
    "placeholder":         "Unnamed",
}

GUILD_NAMES: dict[str, str] = {
    "soul_scholars":  "Scholars",
    "soul_engineers": "Engineers",
    "merchants":      "Merchants",
    "adventurers":    "Adventurers",
}

# 4 个公会主殿 role —— 这些名字固定，不走"前缀+后缀+序号"
_FIXED_NAME_ROLES = frozenset({
    "soul_academy_main", "soul_engineers_main",
    "merchants_main", "adventurers_main",
    "soul_core_exchange", "city_hall",
})

# 古风序号字符（壹..玖、拾）
_CHINESE_NUMS = "零壹贰叁肆伍陆柒捌玖"

# 民居/小建筑的别号，加点烟火气（英文版）
_HOUSE_EPITHETS = ("Quiet", "Calm", "Still", "Ward", "Hearth", "Bright",
                   "Home", "Rest", "Vale", "Dusk", "Dawn", "Glen")


def _to_chinese_num(n: int) -> str:
    """1..99 → 古风中文数字（壹/拾贰/贰拾叁/…）。超过 99 直接转回阿拉伯。"""
    if n <= 0:
        return _CHINESE_NUMS[0]
    if n < 10:
        return _CHINESE_NUMS[n]
    if n == 10:
        return "拾"
    if n < 20:
        return "拾" + _CHINESE_NUMS[n - 10]
    if n < 100:
        tens, ones = divmod(n, 10)
        head = _CHINESE_NUMS[tens] + "拾"
        return head if ones == 0 else head + _CHINESE_NUMS[ones]
    return str(n)


def _seed_from_origin(origin: tuple[int, int, int]) -> int:
    """3D 坐标 → 稳定 32-bit seed，不依赖 Python hash randomization。"""
    x, y, z = origin
    h = (x * 73856093) ^ (y * 19349663) ^ (z * 83492791)
    return h & 0x7FFFFFFF


def generate_building_name(meta: BuildingMeta, index: int = 0) -> str:
    """根据 BuildingMeta 生成中文显示名。

    - 6 个固定名 role 直接返回查表名（4 主殿/exchange/city_hall）。
    - 其它按"公会前缀·角色后缀·古风序号"合成。
    - house 额外抽一个字辈（清/安/宁/…）增加古风味。
    index 是同 (role, guild) 内的局部序号，由调用方维护。
    """
    if meta.role in _FIXED_NAME_ROLES:
        return ROLE_NAMES.get(meta.role, "Unnamed")

    rng = random.Random(_seed_from_origin(meta.origin))
    role_name = ROLE_NAMES.get(meta.role, "Unnamed")
    seq = index + 1

    # 不挂公会前缀（牌面太长会截断；公会已由门牌 [SCH] 徽号 + 标语体现）。
    if meta.role == "house":
        epithet = rng.choice(_HOUSE_EPITHETS)
        return f"{epithet} {role_name} No.{seq}"
    return f"{role_name} No.{seq}"


__all__ = ["ROLE_NAMES", "GUILD_NAMES", "generate_building_name"]
