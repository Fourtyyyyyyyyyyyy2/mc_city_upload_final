"""Terrain/style-aware city planning profiles."""
from __future__ import annotations

from dataclasses import dataclass

from ..config import (
    BLOCK_BUILDING_PADDING,
    BLOCK_SIZE,
    CITY_PLANNING_PROFILES,
    CITY_PLANNING_PROFILES_ENABLED,
    GRID_LARGE_THRESHOLD,
    GRID_MAX_LARGE,
    NEXT_ROAD_WIDTH,
)


@dataclass(frozen=True)
class PlanningProfile:
    style: str
    block_size: int
    next_road_width: int
    building_padding: int
    large_threshold: int
    max_large: int


_DEFAULT = PlanningProfile(
    style="default",
    block_size=BLOCK_SIZE,
    next_road_width=NEXT_ROAD_WIDTH,
    building_padding=BLOCK_BUILDING_PADDING,
    large_threshold=GRID_LARGE_THRESHOLD,
    max_large=GRID_MAX_LARGE,
)


def planning_profile_for(style: str | None) -> PlanningProfile:
    """Return a complete planning profile for the current environment style."""
    if not CITY_PLANNING_PROFILES_ENABLED or not style:
        return _DEFAULT
    data = CITY_PLANNING_PROFILES.get(style) or CITY_PLANNING_PROFILES.get("chinese")
    if not data:
        return _DEFAULT
    return PlanningProfile(
        style=style,
        block_size=int(data.get("block_size", _DEFAULT.block_size)),
        next_road_width=int(data.get("next_road_width", _DEFAULT.next_road_width)),
        building_padding=int(data.get("building_padding", _DEFAULT.building_padding)),
        large_threshold=int(data.get("large_threshold", _DEFAULT.large_threshold)),
        max_large=int(data.get("max_large", _DEFAULT.max_large)),
    )


__all__ = ["PlanningProfile", "planning_profile_for"]
