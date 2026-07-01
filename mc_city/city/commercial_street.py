"""Priority 6 卡 12.1：商业街——沿 4 条 cardinal 主道两侧密铺 05_商业街_* 小店。

放置逻辑（与 block_placement 同层、复用其 terraform/paste 套路），放 city/ 子包，
不放 layout/——layout 是纯几何层，从 layout 反向 import city 会成循环依赖。

公开 API:
    build_commercial_streets(center_x, center_z, plaza_r, ctx, codec, height_map,
        terrain_map, scan_volume, placed_origins, placed_boxes, locked_rects,
        wall_radius=WALL_RADIUS, dry_run=False) -> dict

设计：
- 4 条主道几何 E(+x)/W(-x)/S(+z)/N(-z)，从 plaza_r(+START_OFFSET) 到 wall_radius。
- 每条主道两侧紧贴路缘逐段排店，店面朝街（朝路中线投影点）。
- 池 = components/ 下所有 05_商业街_*.npy（list_prefix_files），整城洗牌后循环复用。
- terraform 复用 block_placement 同款：terraform_for_building 不行 → force_platform 兜底。
- 放成功的店登记 placed_boxes/locked_rects，与地标/网格/城墙共享同一套碰撞框（自动避让）。
- 不动 builder 现有步骤、不自己写 HTTP（paste_volume / set_blocks_batch 间接）。
"""
from __future__ import annotations

import os
import random

import numpy as np

from ..config import (
    BLOCK_TERRAFORM_MAX_CUT,
    BLOCK_TERRAFORM_MAX_FILL,
    COMMERCIAL_STREET_GAP,
    COMMERCIAL_STREET_MAX_FOOTPRINT,
    COMMERCIAL_STREET_PREFIX,
    COMMERCIAL_STREET_SPACING,
    COMMERCIAL_STREET_START_OFFSET,
    CARDINAL_ROAD_WIDTH,
    FORCE_PLATFORM_ENABLED,
    WALL_RADIUS,
)
from ..mc.codec import BlockCodec
from ..mc.placement import paste_volume
from ..scan.coord_frame import ScanContext
from .block_placement import _environment_style_for, _environment_terrain_for
from .components import list_prefix_files, list_style_chain_files
from .reskin import make_remap
from .placement import (
    boxes_intersect,
    compute_facing_rotation,
    footprint_xz,
    is_location_occupied,
    make_box_from_center,
)
from .terraform import (
    apply_terraform,
    terraform_for_building,
    terraform_force_platform,
)
from .trees import clear_footprint_vegetation

# 4 个 cardinal 方向 (label, dx, dz)，与 layout.cardinal_road._CARDINALS 一致。
_CARDINALS = (("E", +1, 0), ("W", -1, 0), ("S", 0, +1), ("N", 0, -1))


def _shop_pool(style: str | None = None) -> list[str]:
    """商业街小店池：05_商业街_* 且 footprint ≤ 上限。按 basename 去重（同名店散在
    mid+outer 两文件夹，只取一份）+ 排序，保证 deterministic、不把同店当两家。"""
    seen: dict[str, str] = {}
    source = []
    if style:
        source = (list_style_chain_files("mid", style)
                  + list_style_chain_files("outer", style))
    if not source:
        source = list_prefix_files(COMMERCIAL_STREET_PREFIX)
    for p in source:
        b = os.path.basename(p)
        if b in seen:
            continue
        if max(footprint_xz(p, 0)) <= COMMERCIAL_STREET_MAX_FOOTPRINT:
            seen[b] = p
    return [seen[b] for b in sorted(seen)]


def build_commercial_streets(center_x: int, center_z: int,
                             plaza_r: int,
                             ctx: ScanContext,
                             codec: BlockCodec,
                             height_map: np.ndarray,
                             terrain_map: np.ndarray,
                             scan_volume: np.ndarray,
                             placed_origins: list,
                             placed_boxes: list,
                             locked_rects: list,
                             wall_radius: int = WALL_RADIUS,
                             dry_run: bool = False,
                             ) -> dict:
    """沿 4 主道两侧密铺商业街小店。返回 {"placed": [...], "rejects": {...}}。

    dry_run=True 跳过 HTTP（terraform/occupied/paste），terraform 视作成功
    (base_y=店心地表)，仅验证几何/碰撞（dry-run 用）。
    """
    NZ, NX = height_map.shape
    center_sx, center_sz = ctx.w2s(center_x, center_z)
    style = None
    if 0 <= center_sx < NX and 0 <= center_sz < NZ:
        style = _environment_style_for(
            terrain_map, scan_volume, codec, center_sx, center_sz)
    pool = _shop_pool(style)
    if not pool:
        print(f"  ⚠️ 商业街池为空（无 {COMMERCIAL_STREET_PREFIX}* 或全超 "
              f"{COMMERCIAL_STREET_MAX_FOOTPRINT} 尺寸）")
        return {"placed": [], "rejects": {"empty_pool": 1}}

    features = getattr(ctx, "terrain_features", None)
    rng = random.Random(f"comm_{center_x}_{center_z}")
    order = pool[:]
    rng.shuffle(order)
    cur = [0]                       # 池游标（list 包一层，闭包可改）

    def _next_shop() -> str:
        p = order[cur[0] % len(order)]
        cur[0] += 1
        return p

    result: dict = {"placed": [], "rejects": {}}

    def _rej(reason: str):
        result["rejects"][reason] = result["rejects"].get(reason, 0) + 1

    road_half = CARDINAL_ROAD_WIDTH // 2          # 路半宽（中线到路缘）
    near_face = road_half + 1 + COMMERCIAL_STREET_GAP  # 中线到店近面
    d_start = int(plaza_r) + COMMERCIAL_STREET_START_OFFSET  # 外推过近 plaza 地标带
    d_end = int(wall_radius - 2 - 1)              # 不顶城墙，留收口

    for label, ax, az in _CARDINALS:
        axis_is_x = az == 0
        nominal_rot = 0 if axis_is_x else 90      # 取尺寸用（同轴 0/180 或 90/270 等价）
        for side in (+1, -1):                     # 主道两侧
            d = d_start
            while d <= d_end:
                path = _next_shop()
                fp_sx, fp_sz = footprint_xz(path, nominal_rot)
                width = fp_sx if axis_is_x else fp_sz     # 沿街方向
                depth = fp_sz if axis_is_x else fp_sx     # 垂直街（进深）
                if d + width - 1 > d_end:         # 这家在城墙前放不下 → 收尾该侧
                    break

                a_dist = d + width // 2                    # 店心沿轴距中心
                p_dist = near_face + depth // 2            # 店心垂直距中线
                if axis_is_x:
                    bx = int(center_x + ax * a_dist)
                    bz = int(center_z + side * p_dist)
                    proj = (bx, center_z)                 # 路中线投影点
                else:
                    bz = int(center_z + az * a_dist)
                    bx = int(center_x + side * p_dist)
                    proj = (center_x, bz)
                rotation = compute_facing_rotation(proj[0], proj[1], bx, bz)

                ok = _place_one(path, bx, bz, fp_sx, fp_sz, rotation,
                                ctx, codec, height_map, terrain_map, scan_volume,
                                features, placed_origins, placed_boxes,
                                locked_rects, dry_run, _rej, NX, NZ)
                if ok:
                    result["placed"].append((label, side, os.path.basename(path),
                                             bx, bz))
                d += width + COMMERCIAL_STREET_SPACING        # 无论成败都前进，防死循环

    n = len(result["placed"])
    if result["rejects"]:
        rj = ", ".join(f"{k}={v}" for k, v in
                       sorted(result["rejects"].items(), key=lambda kv: -kv[1]))
        print(f"  🏪 商业街放置 {n} 家（池 {len(pool)}）｜拒因: {rj}")
    else:
        print(f"  🏪 商业街放置 {n} 家（池 {len(pool)}）")
    return result


def _place_one(path, bx, bz, fp_sx, fp_sz, rotation,
               ctx, codec, height_map, terrain_map, scan_volume, features,
               placed_origins, placed_boxes, locked_rects, dry_run, _rej,
               NX, NZ) -> bool:
    """放一家店：碰撞 → terraform（force_platform 兜底）→ paste。复用 block_placement 套路。"""
    scx, scz = ctx.w2s(bx, bz)
    fp_sx0 = scx - fp_sx // 2
    fp_sx1 = scx + (fp_sx - fp_sx // 2 - 1)
    fp_sz0 = scz - fp_sz // 2
    fp_sz1 = scz + (fp_sz - fp_sz // 2 - 1)
    if fp_sx0 < 0 or fp_sx1 >= NX or fp_sz0 < 0 or fp_sz1 >= NZ:
        _rej("footprint_oob")
        return False

    # padding=0：商业街要紧贴排，相邻店框不留 buffer（否则 SPACING=0 时自撞 box_intersect）。
    box = make_box_from_center(bx, bz, fp_sx, fp_sz, padding=0)
    if any(boxes_intersect(box, b) for b in placed_boxes):
        _rej("box_intersect")
        return False

    # terraform：正常 cut/fill → 失败则强制平台兜底（与 block_placement 同款）。
    terraformed = False
    tr = None
    if not dry_run and features is not None:
        tr = terraform_for_building(
            footprint_xz=(fp_sx0, fp_sz0, fp_sx1, fp_sz1),
            height_map=height_map, features=features, ctx=ctx,
            terrain_map=terrain_map,
            max_cut=BLOCK_TERRAFORM_MAX_CUT, max_fill=BLOCK_TERRAFORM_MAX_FILL)
        if not tr.success and FORCE_PLATFORM_ENABLED:
            tr = terraform_force_platform(
                footprint_xz=(fp_sx0, fp_sz0, fp_sx1, fp_sz1),
                height_map=height_map, features=features, ctx=ctx,
                terrain_map=terrain_map)
        if not tr.success:
            _rej("terraform:" + (tr.reason.split("(")[0] or "fail"))
            return False
        base_y = int(tr.base_y)
        terraformed = True
    else:
        base_y = int(height_map[scz, scx])
        if base_y <= int(ctx.min_y):
            _rej("sentinel")
            return False

    if not dry_run:
        occ, _ = is_location_occupied(scan_volume, bx, bz, ctx,
                                      padding=2, codec=codec)
        if occ:
            _rej("occupied")
            return False
        if terraformed:
            if not apply_terraform(tr, ctx, scan_volume=scan_volume,
                                   height_map=height_map, codec=codec):
                _rej("apply_terraform_http_fail")
                return False
        clear_footprint_vegetation(fp_sx0, fp_sx1, fp_sz0, fp_sz1, base_y, ctx)
        origin = (int(bx - fp_sx // 2), base_y, int(bz - fp_sz // 2))
        shop_terrain = _environment_terrain_for(terrain_map, scan_volume, codec, scx, scz)
        try:
            paste_volume(path, origin=origin, clear_target=False, rotation=rotation,
                         block_remap=make_remap(shop_terrain))
        except Exception as exc:
            _rej("paste_exception")
            print(f"  ⚠️ 商业街店渲染失败 ({bx},{bz}): {exc!r}")
            return False
    else:
        origin = (int(bx - fp_sx // 2), base_y, int(bz - fp_sz // 2))

    placed_origins.append((int(bx), base_y, int(bz)))
    placed_boxes.append(box)
    locked_rects.append((fp_sx0, fp_sx1, fp_sz0, fp_sz1))
    return True


__all__ = ["build_commercial_streets"]
