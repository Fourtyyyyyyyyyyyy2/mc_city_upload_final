"""派生城市半径：把 build area 尺寸映射到所有圈层/墙/广场半径（Priority 3 卡 10.1）。

ADAPTIVE_SIZE_ENABLED=True：R = min(build_w, build_h)//2，所有半径按 config.SIZE_RATIO
比例派生（小图自动缩，顺手修当前 outer 末=260 的越界）。
=False：逐字段返回 config 当前写死值，等价旧行为（baseline 兜底）。

纯计算：无 HTTP、不读可变全局状态（flag 在调用时从 config 现取，可单测两路）。
BLOCK_SIZE 是固定物理尺寸，永不按比例缩——小图靠"圈带变窄、街区变少"自适应。
本卡不接入主流程，只暴露 CityDims / compute_city_dims 供卡 10.2 起消费。
"""
from __future__ import annotations

from dataclasses import dataclass

from .. import config


@dataclass(frozen=True)
class CityDims:
    """一次城市生成用的全部派生半径（单位 block，相对城市中心）。"""
    wall_radius: int
    inner: tuple[int, int]
    mid: tuple[int, int]
    outer: tuple[int, int]
    mid_start_r: int
    outer_end_r: int
    forecourt_r: int
    edge_margin: int     # center 选址边距：保证整城外延不越出 build area
    block_size: int


def _fixed_dims() -> CityDims:
    """ADAPTIVE_SIZE_ENABLED=False：逐字段复刻 config 当前写死值（旧行为）。

    edge_margin 用 WALL_RADIUS——这是 center.py 现在的实际边距，保证回退后
    选址行为与改造前完全一致。
    """
    return CityDims(
        wall_radius=config.WALL_RADIUS,
        inner=tuple(config.RADIUS_MAP["inner"]),
        mid=tuple(config.RADIUS_MAP["mid"]),
        outer=tuple(config.RADIUS_MAP["outer"]),
        mid_start_r=config.MID_RING_START_R,
        outer_end_r=config.OUTER_RING_END_R,
        forecourt_r=config.FORECOURT_RADIUS,
        edge_margin=config.WALL_RADIUS,
        block_size=config.BLOCK_SIZE,
    )


def compute_city_dims(build_w: int, build_h: int) -> CityDims:
    """build area 宽高 → CityDims。

    flag=False 时忽略尺寸返回固定值；flag=True 时按 R=min(w,h)//2 的比例派生。
    """
    if not config.ADAPTIVE_SIZE_ENABLED:
        return _fixed_dims()

    R = min(int(build_w), int(build_h)) // 2
    ratio = config.SIZE_RATIO

    def scale(x: float) -> int:
        return int(round(R * x))

    def scale_pair(pair) -> tuple[int, int]:
        return (scale(pair[0]), scale(pair[1]))

    outer = scale_pair(ratio["outer"])
    outer_end_r = scale(ratio["max_extent"])

    return CityDims(
        wall_radius=scale(ratio["wall"]),
        inner=scale_pair(ratio["inner"]),
        mid=scale_pair(ratio["mid"]),
        outer=outer,
        mid_start_r=scale(ratio["mid_start"]),
        outer_end_r=outer_end_r,
        forecourt_r=scale(ratio["forecourt"]),
        edge_margin=outer_end_r + config.SIZE_EDGE_BUFFER,
        block_size=config.BLOCK_SIZE,   # 固定物理尺寸，不按比例缩
    )


__all__ = ["CityDims", "compute_city_dims"]