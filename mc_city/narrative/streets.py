"""叙事图层：街道命名 + 路牌（任务 1.5）。

公开 API:
    STREET_NAMES_RADIAL / STREET_NAMES_RING — 街道名表（公会扇区 / 圈层）
    name_and_sign_streets(center_x, center_z, height_map, ctx) -> int
        主入口，提交 HTTP，返回成功放置块数
    build_street_sign_payloads(center_x, center_z, height_map, ctx)
        → (payloads, skipped)；不调 HTTP，方便 dry-run

设计：
    - 4 块"城外路牌"：4 个公会扇区中线（45/135/225/315°）与城墙的交点，
      朝外（出城方向）。
    - 12 块"十字路牌"：3 个 ring × 4 个 radial = 12 个交点，朝心（玩家
      从城外往内走时看到正面）。
    - 用 oak_sign（standing sign）+ rotation NBT；不用 oak_wall_sign
      因为路牌位置往往没有相邻墙体支撑，wall_sign 会 drop 成 item。
      standing sign 立在地表上面 1 格，只要下方地表有支撑方块即可。
    - 复用 signs._line_to_snbt_json 的 SNBT 包装。
    - 单块失败不影响其它（按 spec 1.5 CONSTRAINTS）。
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np

from ..config import RADIUS_MAP, WALL_RADIUS
from ..mc.placement import set_blocks_batch
from ..scan.coord_frame import ScanContext
from .metadata import _sector_guild
from .signs import SIGN_LINE_LIMIT, _line_to_snbt_json

# 路牌单批上限 < 1024（与 signs 一致）
STREET_SIGN_BATCH_SIZE = 256

# 4 公会按扇区中线（与 metadata._sector_guild 的 0/90/180/270 边界匹配）
STREET_NAMES_RADIAL: dict[str, str] = {
    "soul_scholars":  "Scholars' Avenue",
    "soul_engineers": "Artisans' Avenue",
    "merchants":      "Merchants' Avenue",
    "adventurers":    "Heroes' Avenue",
}

# 3 圈环道按圈层叙事命名
STREET_NAMES_RING: dict[str, str] = {
    "inner": "Ritual Ring",
    "mid":   "Civic Ring",
    "outer": "Dwellers' Ring",
}

# 公会短称（路牌行内字数紧张时用）
_GUILD_SHORT: dict[str, str] = {
    "soul_scholars":  "Scholars",
    "soul_engineers": "Artisans",
    "merchants":      "Merchants",
    "adventurers":    "Heroes",
}

# 4 个 cardinal 角度（度数），每个扇区的中线
_RADIAL_ANGLES_DEG: tuple[float, ...] = (45.0, 135.0, 225.0, 315.0)


# ── 公共入口 ──────────────────────────────────────────────────────
def name_and_sign_streets(center_x: int, center_z: int,
                          height_map: np.ndarray,
                          ctx: ScanContext,
                          codec=None) -> int:
    """放城外 4 块 + 十字 12 块路牌，分批 HTTP 提交。

    codec 参数留兼容签名（路牌不入 scan_volume）。

    Returns:
        成功放置的块数（≤ 16）。
    """
    _ = codec
    payloads, skipped = build_street_sign_payloads(
        center_x, center_z, height_map, ctx)
    if skipped:
        print(f"  ⚠️ 跳过 {skipped} 块路牌（地形挡住或越界）")
    if not payloads:
        print("  没有可放置的路牌")
        return 0

    success = 0
    for i in range(0, len(payloads), STREET_SIGN_BATCH_SIZE):
        batch = payloads[i:i + STREET_SIGN_BATCH_SIZE]
        if set_blocks_batch(batch):
            success += len(batch)
        else:
            print(f"  ⚠️ 路牌批次写入失败（{len(batch)} 块），继续下一批")
    return success


def build_street_sign_payloads(center_x: int, center_z: int,
                               height_map: np.ndarray,
                               ctx: ScanContext) -> tuple[list[dict], int]:
    """计算 16 块路牌 payload。不调 HTTP，dry-run 友好。

    顺序：先 4 块城外路牌（顺时针扇区中线），再 12 块十字路牌
    （ring × radial，inner→mid→outer，每圈内顺时针）。
    """
    NZ, NX = height_map.shape
    payloads: list[dict] = []
    skipped = 0

    # 1) 城外路牌（4 块）：扇区中线 × 城墙
    for angle_deg in _RADIAL_ANGLES_DEG:
        guild = _sector_guild(angle_deg)
        radial_name = STREET_NAMES_RADIAL.get(guild, "Nameless Rd")
        wx, wz = _polar_to_world(center_x, center_z, WALL_RADIUS, angle_deg)
        sign = _make_payload(
            wx, wz, height_map, ctx, NZ, NX,
            rotation=_rotation_away_from_center(wx, wz, center_x, center_z),
            lines=(
                radial_name,
                _arrow_for_facing_out(angle_deg),
                f"To {_GUILD_SHORT.get(guild, '')}",
                _cardinal_label(angle_deg),
            ),
        )
        if sign is None:
            skipped += 1
        else:
            payloads.append(sign)

    # 2) 十字路牌（12 块）：3 ring × 4 radial 交点
    for ring_name in ("inner", "mid", "outer"):
        r_min, r_max = RADIUS_MAP[ring_name]
        ring_r = (r_min + r_max) // 2
        ring_street = STREET_NAMES_RING[ring_name]
        for angle_deg in _RADIAL_ANGLES_DEG:
            guild = _sector_guild(angle_deg)
            radial_name = STREET_NAMES_RADIAL.get(guild, "Nameless Rd")
            wx, wz = _polar_to_world(center_x, center_z, ring_r, angle_deg)
            sign = _make_payload(
                wx, wz, height_map, ctx, NZ, NX,
                rotation=_rotation_toward_center(wx, wz, center_x, center_z),
                lines=(
                    ring_street,
                    "×",
                    radial_name,
                    f"{_GUILD_SHORT.get(guild, '')} St",
                ),
            )
            if sign is None:
                skipped += 1
            else:
                payloads.append(sign)

    return payloads, skipped


# ── helpers ───────────────────────────────────────────────────────
def _make_payload(wx: int, wz: int,
                  height_map: np.ndarray,
                  ctx: ScanContext,
                  NZ: int, NX: int,
                  rotation: int,
                  lines: tuple[str, str, str, str]) -> Optional[dict]:
    """通用 payload 构造：边界 / sentinel 检查 + SNBT 拼接。

    standing sign 立在地表 +1，rotation 用 0-15 范围（16 个朝向）。
    返回 None 表示该位置不能放（越界 / sentinel），调用方递增 skipped。
    """
    xs, zs = ctx.w2s(wx, wz)
    if not (0 <= xs < NX and 0 <= zs < NZ):
        return None
    ground_y = int(height_map[zs, xs])
    if ground_y <= ctx.min_y:
        return None
    truncated = tuple(_truncate(l, SIGN_LINE_LIMIT) for l in lines)
    msgs = ",".join(_line_to_snbt_json(line) for line in truncated)
    block_id = (f"minecraft:oak_sign[rotation={int(rotation)}]"
                f"{{front_text:{{messages:[{msgs}]}}}}")
    return {"x": int(wx), "y": int(ground_y + 1), "z": int(wz), "id": block_id}


def _polar_to_world(cx: int, cz: int, r: float,
                    angle_deg: float) -> tuple[int, int]:
    """极坐标→世界坐标。angle_deg=0 对应 +X 轴，顺时针。"""
    theta = math.radians(angle_deg)
    wx = int(round(cx + r * math.cos(theta)))
    wz = int(round(cz + r * math.sin(theta)))
    return wx, wz


# standing sign rotation: 16 个方向，0=south, 4=west, 8=north, 12=east
# 我们只用 4 个 cardinal 方向。
_FACING_TO_ROTATION: dict[str, int] = {
    "south": 0,
    "west":  4,
    "north": 8,
    "east":  12,
}


def _rotation_toward_center(wx: int, wz: int, cx: int, cz: int) -> int:
    """4 正方向中朝中心最近的一个，返回 standing sign rotation。"""
    dx = cx - wx
    dz = cz - wz
    if abs(dx) >= abs(dz):
        facing = "east" if dx >= 0 else "west"
    else:
        facing = "south" if dz >= 0 else "north"
    return _FACING_TO_ROTATION[facing]


def _rotation_away_from_center(wx: int, wz: int, cx: int, cz: int) -> int:
    """与朝心相反——城外路牌出城方向。"""
    dx = wx - cx
    dz = wz - cz
    if abs(dx) >= abs(dz):
        facing = "east" if dx >= 0 else "west"
    else:
        facing = "south" if dz >= 0 else "north"
    return _FACING_TO_ROTATION[facing]


def _cardinal_label(angle_deg: float) -> str:
    """45/135/225/315 -> SE/SW/NW/NE。"""
    a = angle_deg % 360
    if a < 90:
        return "SE"
    if a < 180:
        return "SW"
    if a < 270:
        return "NW"
    return "NE"


def _arrow_for_facing_out(angle_deg: float) -> str:
    """城外路牌的"出城方向"箭头。MC 字体里 ASCII 箭头有限，用文字代替。"""
    a = angle_deg % 360
    if a < 90:
        return "-> SE"
    if a < 180:
        return "-> SW"
    if a < 270:
        return "-> NW"
    return "-> NE"


def _truncate(text: str, n: int) -> str:
    return text if len(text) <= n else text[:n]


__all__ = [
    "STREET_NAMES_RADIAL", "STREET_NAMES_RING",
    "STREET_SIGN_BATCH_SIZE",
    "name_and_sign_streets", "build_street_sign_payloads",
]
