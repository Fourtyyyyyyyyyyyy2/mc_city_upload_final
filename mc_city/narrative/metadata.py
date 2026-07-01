"""叙事元数据分配。

把 build_city 的 used 字典 + 模块化建筑列表压扁成 BuildingMeta 列表，
并按下列规则赋 role / guild / name / founder_year / ruin_severity：

  inner 圈 6 栋
      4 大公会主殿（按扇区中线就近：soul_scholars/soul_engineers/
      merchants/adventurers）+ 剩余 2 栋 → 近 = soul_core_exchange，
      远 = city_hall。inner 不足 6 栋时，超额位置降级为公会分院。
  mid 圈
      role 循环 guild_branch / guild_workshop / shop / inn；
      guild 取距 inner 公会主殿（4 个 anchor）最近者。
  outer 圈
      按角度排序循环 watchtower / warehouse / house（约 1:1:5）；
      guild 按角度扇区。
  modular
      role=house；guild 按角度扇区。

名字由 names.generate_building_name 生成（origin 决定 seed → 可重现）。
不调 HTTP，不依赖 height_map。
"""
import math
from typing import Any, Optional

from .names import generate_building_name
from .types import BuildingMeta

# 扇区中线（度，atan2(dz, dx)）→ 对应公会
# 0°-90°:soul_scholars / 90°-180°:soul_engineers /
# 180°-270°:merchants / 270°-360°:adventurers
_SECTOR_CENTERS: list[tuple[float, str]] = [
    (45.0,  "soul_scholars"),
    (135.0, "soul_engineers"),
    (225.0, "merchants"),
    (315.0, "adventurers"),
]

_GUILD_MAIN_ROLE: dict[str, str] = {
    "soul_scholars":  "soul_academy_main",
    "soul_engineers": "soul_engineers_main",
    "merchants":      "merchants_main",
    "adventurers":    "adventurers_main",
}

# mid 圈 role 轮转
_MID_ROLE_CYCLE = ("guild_branch", "guild_workshop", "shop", "inn")

# 圈层默认毁伤程度。叙事设定：被入侵后越靠外越惨。
# 1.1 docstring 留的范围 0~1，后续 ruin 图层可以基于此再加扰动。
_RUIN_BY_RING: dict[str, float] = {
    "inner":   0.2,
    "mid":     0.4,
    "outer":   0.6,
    "modular": 0.6,
}


# ── 公共入口 ──────────────────────────────────────────────────────────
def assign_narrative_metadata(used: dict,
                              modular_buildings: list,
                              center_x: int,
                              center_z: int) -> list[BuildingMeta]:
    """生成 BuildingMeta 列表。顺序：inner → mid → outer → modular。

    参数与 1.1 占位实现一致：
      used: {"inner":[info,...], "mid":[...], "outer":[...]}，info 至少含 origin。
      modular_buildings: list[dict|tuple]，目前 build_city 还未收集，调用方传 []。
      center_x, center_z: 城市中心世界坐标。
    """
    inner_list = used.get("inner") or []
    mid_list   = used.get("mid")   or []
    outer_list = used.get("outer") or []

    inner_metas = _assign_inner(inner_list, center_x, center_z)

    # 4 大公会主殿位置，给 mid 圈做"最近 anchor"
    guild_anchors: dict[str, tuple[int, int, int]] = {
        m.guild: m.origin
        for m in inner_metas
        if m.guild and m.role in _GUILD_MAIN_ROLE.values()
    }

    mid_metas     = _assign_mid(mid_list, guild_anchors, center_x, center_z)
    outer_metas   = _assign_outer(outer_list, center_x, center_z)
    modular_metas = _assign_modular(modular_buildings or [], center_x, center_z)

    metas = inner_metas + mid_metas + outer_metas + modular_metas
    _name_pass(metas)
    return metas


# ── 圈层各自的分配规则 ────────────────────────────────────────────────
def _assign_inner(inner_list: list,
                  center_x: int, center_z: int) -> list[BuildingMeta]:
    """inner 圈：扇区中线就近 → 4 主殿；剩余按距离 → exchange/city_hall。"""
    if not inner_list:
        return []

    entries = []
    for info in inner_list:
        origin = _coerce_origin(info)
        entries.append({
            "origin": origin,
            "ang":    _angle_deg(origin, center_x, center_z),
            "dist":   _dist_xz(origin, (center_x, 0, center_z)),
        })

    assignments: dict[int, tuple[str, Optional[str]]] = {}

    # 每个扇区中线（45/135/225/315）选角度差最小的 1 栋作公会主殿
    for sector_center, guild in _SECTOR_CENTERS:
        best_i, best_diff = -1, 1e9
        for i, e in enumerate(entries):
            if i in assignments:
                continue
            d = _angle_diff(e["ang"], sector_center)
            if d < best_diff:
                best_i, best_diff = i, d
        if best_i >= 0:
            assignments[best_i] = (_GUILD_MAIN_ROLE[guild], guild)

    # 剩下的按距离：近 → exchange，第二近 → city_hall，再多的降级 guild_branch
    remaining = sorted(
        (i for i in range(len(entries)) if i not in assignments),
        key=lambda i: entries[i]["dist"],
    )
    leftover_roles = ["soul_core_exchange", "city_hall"]
    for k, i in enumerate(remaining):
        if k < len(leftover_roles):
            assignments[i] = (leftover_roles[k], None)
        else:
            assignments[i] = ("guild_branch", _sector_guild(entries[i]["ang"]))

    out: list[BuildingMeta] = []
    for i, e in enumerate(entries):
        role, guild = assignments[i]
        out.append(BuildingMeta(
            origin=e["origin"],
            ring="inner",
            role=role,
            guild=guild,
            name="",
            founder_year=_inner_founder_year(role),
            ruin_severity=_RUIN_BY_RING["inner"],
        ))
    return out


def _assign_mid(mid_list: list,
                guild_anchors: dict[str, tuple[int, int, int]],
                center_x: int, center_z: int) -> list[BuildingMeta]:
    if not mid_list:
        return []

    # 按角度稳定排序，让 role 轮转看起来沿环带流动
    sorted_pairs = sorted(
        ((info, _coerce_origin(info)) for info in mid_list),
        key=lambda p: _angle_deg(p[1], center_x, center_z),
    )

    out: list[BuildingMeta] = []
    for idx, (_info, origin) in enumerate(sorted_pairs):
        if guild_anchors:
            guild = min(guild_anchors, key=lambda g: _dist_xz(origin, guild_anchors[g]))
        else:
            guild = _sector_guild(_angle_deg(origin, center_x, center_z))
        role = _MID_ROLE_CYCLE[idx % len(_MID_ROLE_CYCLE)]
        out.append(BuildingMeta(
            origin=origin,
            ring="mid",
            role=role,
            guild=guild,
            name="",
            founder_year=-100 + (idx % 40),
            ruin_severity=_RUIN_BY_RING["mid"],
        ))
    return out


def _assign_outer(outer_list: list,
                  center_x: int, center_z: int) -> list[BuildingMeta]:
    if not outer_list:
        return []

    sorted_pairs = sorted(
        ((info, _coerce_origin(info)) for info in outer_list),
        key=lambda p: _angle_deg(p[1], center_x, center_z),
    )

    out: list[BuildingMeta] = []
    # 每 7 个一个循环 → 28 outer ≈ 4 watchtower + 4 warehouse + 20 house
    for idx, (_info, origin) in enumerate(sorted_pairs):
        slot = idx % 7
        if slot == 0:
            role = "watchtower"
        elif slot == 3:
            role = "warehouse"
        else:
            role = "house"
        out.append(BuildingMeta(
            origin=origin,
            ring="outer",
            role=role,
            guild=_sector_guild(_angle_deg(origin, center_x, center_z)),
            name="",
            founder_year=-40 + (idx % 30),
            ruin_severity=_RUIN_BY_RING["outer"],
        ))
    return out


def _assign_modular(modular_list: list,
                    center_x: int, center_z: int) -> list[BuildingMeta]:
    if not modular_list:
        return []
    out: list[BuildingMeta] = []
    for idx, info in enumerate(modular_list):
        origin = _coerce_origin(info)
        out.append(BuildingMeta(
            origin=origin,
            ring="modular",
            role="house",
            guild=_sector_guild(_angle_deg(origin, center_x, center_z)),
            name="",
            founder_year=-20 + (idx % 25),
            ruin_severity=_RUIN_BY_RING["modular"],
        ))
    return out


# ── 起名（按 (role, guild) 内序号） ─────────────────────────────────
def _name_pass(metas: list[BuildingMeta]) -> None:
    counters: dict[tuple[str, Optional[str]], int] = {}
    for m in metas:
        key = (m.role, m.guild)
        idx = counters.get(key, 0)
        counters[key] = idx + 1
        m.name = generate_building_name(m, index=idx)


# ── 工具 ──────────────────────────────────────────────────────────────
def _coerce_origin(info: Any) -> tuple[int, int, int]:
    """info 可为 dict（含 'origin'）或直接 (x,y,z) 元组。缺字段抛 KeyError，不静默吞。"""
    if isinstance(info, dict):
        x, y, z = info["origin"]
    else:
        x, y, z = info
    return (int(x), int(y), int(z))


def _angle_deg(origin: tuple[int, int, int],
               center_x: int, center_z: int) -> float:
    """origin 相对城市中心的角度（度，[0, 360)），以 atan2(dz, dx) 计。"""
    dx = origin[0] - center_x
    dz = origin[2] - center_z
    return math.degrees(math.atan2(dz, dx)) % 360.0


def _dist_xz(o1: tuple[int, int, int], o2: tuple[int, int, int]) -> float:
    return math.hypot(o1[0] - o2[0], o1[2] - o2[2])


def _angle_diff(a: float, b: float) -> float:
    """两个角度的最小差（度，[0, 180]）。"""
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


def _sector_guild(angle_deg: float) -> str:
    """[0,90)→scholars / [90,180)→engineers / [180,270)→merchants / [270,360)→adventurers。"""
    if angle_deg < 90.0:
        return "soul_scholars"
    if angle_deg < 180.0:
        return "soul_engineers"
    if angle_deg < 270.0:
        return "merchants"
    return "adventurers"


def _inner_founder_year(role: str) -> int:
    return {
        "soul_core_exchange":   0,
        "city_hall":           -50,
        "soul_academy_main":  -180,
        "soul_engineers_main":-175,
        "merchants_main":     -170,
        "adventurers_main":   -160,
    }.get(role, -100)
