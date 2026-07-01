"""建筑构件库索引：在 components/ 下查找 .npy。

目录结构约定：
    components/core_<terrain>/    - 城市核心（灵魂树等）
    components/<ring>_<terrain>/  - 各圈层建筑（ring = inner/mid/outer）
    components/<region_type>_<terrain>/  - 兼容旧命名

`region_type` 可以是 inner_plains/mid_desert 等"ring_baseterrain"形式。
"""
from __future__ import annotations

import os
import random

from ..config import COMPONENT_ROOT, ENV_BUILDING_STYLE_FALLBACKS, GUILD_NAMES


def _ring_from_region(region_type: str) -> str:
    """把 inner_plains / mid_plains / outer_plains 归一成 inner / mid / outer。"""
    return region_type.split("_", 1)[0]


def _norm_guild(guild: str) -> str:
    """公会 token 归一到文件夹名：soul_scholars->scholars, soul_engineers->engineers。

    选址端 token（narrative._sector_guild）带 soul_ 前缀，扫描文件夹不带。
    """
    return guild[5:] if guild.startswith("soul_") else guild


def list_guild_files(ring: str, guild: str) -> list[str]:
    """按 components/<ring>_<guild>/ 找 npy（guild 去 soul_ 前缀）。空则返回 []。

    卡 9.6：grid 街区建筑按公会路由（不再按地形）。某 ring_guild 池没扫 →
    返回 []，上层据此"该街区留空不放"。
    """
    folder = os.path.join(COMPONENT_ROOT, f"{ring}_{_norm_guild(guild)}")
    if os.path.isdir(folder):
        files = [os.path.join(folder, f) for f in os.listdir(folder)
                 if f.endswith(".npy")]
        if files:
            return files
    return []


def list_style_files(ring: str, style: str) -> list[str]:
    """Return files from components/<ring>_<style>/ for environment skin pools."""
    if style == "chinese":
        files: list[str] = []
        for guild in GUILD_NAMES:
            files.extend(list_guild_files(ring, guild))
        if ring == "inner" and not files:
            files = _chinese_inner_files_from_mid()
        files.sort(key=os.path.basename)
        return files

    folder = os.path.join(COMPONENT_ROOT, f"{ring}_{style}")
    if os.path.isdir(folder):
        files = [os.path.join(folder, f) for f in os.listdir(folder)
                 if f.endswith(".npy")]
        files.sort(key=os.path.basename)
        if files:
            return files
    return []


def _chinese_inner_files_from_mid() -> list[str]:
    """Build a virtual inner Chinese pool from larger mid-ring guild buildings."""
    inner_keywords = (
        "豪宅", "交易行", "钱庄", "客栈", "饭店", "塔",
        "四水归堂", "望云", "水栖", "山舍", "独立别墅",
    )
    mid_files: list[str] = []
    for guild in GUILD_NAMES:
        mid_files.extend(list_guild_files("mid", guild))

    preferred = [
        path for path in mid_files
        if not os.path.basename(path).startswith("05_商业街_")
        and any(key in os.path.basename(path) for key in inner_keywords)
    ]
    if preferred:
        return preferred
    return [
        path for path in mid_files
        if not os.path.basename(path).startswith("05_商业街_")
    ]


def list_style_chain_files(ring: str, style: str) -> list[str]:
    """Return primary style files followed by configured fallback style files."""
    if not style:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for current in (style, *ENV_BUILDING_STYLE_FALLBACKS.get(style, ())):
        for path in list_style_files(ring, current):
            key = os.path.basename(path)
            if key in seen:
                continue
            seen.add(key)
            out.append(path)
    return out

def list_prefix_files(prefix: str) -> list[str]:
    """递归 components/ 找文件名以 prefix 开头的 .npy（跨所有 ring_guild 子目录）。

    卡 12.1：商业街小店（05_商业街_*）散在 mid/outer × 各公会池里，按前缀汇总成一池。
    按 basename 排序保证 deterministic。空则返回 []。
    """
    out: list[str] = []
    for root, _dirs, files in os.walk(COMPONENT_ROOT):
        for f in files:
            if f.endswith(".npy") and f.startswith(prefix):
                out.append(os.path.join(root, f))
    out.sort(key=os.path.basename)
    return out


def choose_core_component(terrain_type: str) -> str:
    """选一个核心 .npy。优先用 core_<terrain>/，找不到就 core_plains/。"""
    folder = os.path.join(COMPONENT_ROOT, f"core_{terrain_type}")
    if os.path.isdir(folder):
        files = [f for f in os.listdir(folder) if f.endswith(".npy")]
        if files:
            return os.path.join(folder, random.choice(files))

    folder2 = os.path.join(COMPONENT_ROOT, "core_plains")
    files2 = [f for f in os.listdir(folder2) if f.endswith(".npy")]
    if not files2:
        raise FileNotFoundError(
            "No core .npy found in components/core_<terrain> nor components/core_plains")
    return os.path.join(folder2, random.choice(files2))


def list_region_files_for_terrain(region_type: str, terrain_type: str) -> list[str]:
    """优先地形专属池，找不到返回空列表。"""
    ring = _ring_from_region(region_type)
    for folder in (
        os.path.join(COMPONENT_ROOT, f"{ring}_{terrain_type}"),
        os.path.join(COMPONENT_ROOT, f"{region_type}_{terrain_type}"),
    ):
        if os.path.isdir(folder):
            files = [os.path.join(folder, f) for f in os.listdir(folder) if f.endswith(".npy")]
            if files:
                return files
    return []


def list_region_files_fallback(region_type: str) -> list[str]:
    """地形池空时的兜底：优先 region_type 目录，其次 ring 目录，再次 ring_plains。"""
    ring = _ring_from_region(region_type)
    for folder in (
        os.path.join(COMPONENT_ROOT, region_type),
        os.path.join(COMPONENT_ROOT, ring),
        os.path.join(COMPONENT_ROOT, f"{ring}_plains"),
    ):
        if os.path.isdir(folder):
            files = [os.path.join(folder, f) for f in os.listdir(folder) if f.endswith(".npy")]
            if files:
                return files
    return []
