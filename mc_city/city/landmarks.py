"""[4.7] 大地标摆放：网格放不下的标志性建筑（公会大院/塔/桥/废墟）。

在 [4.5] 中心广场之后、[5'] 网格街区之前调用，先占住 footprint（写 placed_boxes/
locked_rects）→ 网格建筑、城墙、道路自动避让。每个 LANDMARK_SPECS 锚定角度+半径，
附近搜可建点。三种模式：
  normal         公会大院/塔/戏台/许愿树：terraform → 高台地基兜底 → 跳过
  requires_water 荷溢味/五亭桥：footprint 需有水，base_y=SEA_LEVEL，不填水
  outside_wall   破船废墟：墙外，坐自然地面（不 terraform，残骸贴坡也行）
不满足条件就跳过（不硬塞），结束打印放置/跳过清单。
"""
from __future__ import annotations

import math
import os
import random

import numpy as np

from ..config import (
    DARK_COLOSSUS_CARVE_ENABLED,
    DARK_COLOSSUS_CARVE_FRONT,
    DARK_COLOSSUS_CARVE_HEIGHT,
    DARK_COLOSSUS_CARVE_MARGIN,
    DARK_COLOSSUS_DEBRIS_COUNT,
    DARK_COLOSSUS_MAX_MEDIAN_SLOPE,
    DARK_COLOSSUS_MAX_RELIEF,
    EYE_KING_FLOAT_HEIGHT,
    EYE_KING_FLOATING_ENABLED,
    FOUNDATION_FALLBACK_ENABLED,
    FOUNDATION_MAX_CUT,
    FOUNDATION_MAX_FILL,
    FOUNDATION_STILT_ENABLED,
    FOUNDATION_STILT_LEG_BLOCK,
    FOUNDATION_STILT_LEG_SPACING,
    FOUNDATION_STRATEGY,
    ENV_BUILDING_STYLE_FALLBACKS,
    FORCE_PLATFORM_ENABLED,
    LANDMARK_CORE_KEEPOUT,
    LANDMARK_MAX_CUT,
    LANDMARK_MAX_FILL,
    LANDMARK_ROOT,
    LANDMARK_SPECS,
    LANDMARK_STYLE_OVERRIDES_ENABLED,
    LANDMARK_STYLE_SPECS,
    LANDMARK_WATER_ALWAYS_ENABLED,
    LANDMARK_WATER_MIN_FRAC,
    OUTER_RING_END_R,
    SEA_LEVEL,
    WATER_MONSTER_SINK_DEPTH,
    WALL_RADIUS,
)
from ..mc.codec import BlockCodec
from ..mc.placement import paste_volume, set_blocks_batch
from ..scan.coord_frame import ScanContext
from .block_placement import (
    _environment_style_for, _environment_terrain_for, _stiltify_fill,
)
from .reskin import make_eye_king_remap, make_remap
from .placement import (
    boxes_intersect,
    compute_facing_rotation,
    footprint_xz,
    make_box_from_center,
)
from .terraform import (
    apply_terraform,
    terraform_for_building,
    terraform_force_platform,
)
from .trees import clear_footprint_vegetation

# 候选搜索：锚点附近的角度/半径偏移（先试锚点本身，再向外扩）。
_ANGLE_OFFS = (0, -12, 12, -24, 24, -36, 36)
_RADIUS_OFFS = (0, -18, 18, -36, 36, -54, 54)
_GIANT_RUBBLE = (
    "minecraft:blackstone",
    "minecraft:basalt",
    "minecraft:cobbled_deepslate",
    "minecraft:cracked_stone_bricks",
    "minecraft:polished_blackstone",
)


def _anchor(center_x: int, center_z: int, angle_deg: float, radius: float):
    a = math.radians(angle_deg)
    return int(round(center_x + radius * math.cos(a))), \
           int(round(center_z + radius * math.sin(a)))


def _corners_ok(bx, bz, sx, sz, center_x, center_z, outside_wall: bool,
                core_keepout: int = None, wall_radius: int = None,
                outer_end_r: int = None) -> bool:
    """footprint 四角的城心距约束：避开广场核心；按模式卡墙内/墙外。

    keepout/wall/outer 传 None 时用 config 全局（大图旧行为）；调用方传
    ctx.city_dims 的值即接入自适应缩放（小图用小墙半径，地标才放得下）。
    """
    keepout = LANDMARK_CORE_KEEPOUT if core_keepout is None else core_keepout
    wallr = WALL_RADIUS if wall_radius is None else wall_radius
    outer = OUTER_RING_END_R if outer_end_r is None else outer_end_r
    half_x, half_z = sx // 2, sz // 2
    dists = [math.hypot(cx - center_x, cz - center_z)
             for cx in (bx - half_x, bx + half_x)
             for cz in (bz - half_z, bz + half_z)]
    if min(dists) < keepout:                         # 别压到广场/灵魂树
        return False
    if max(dists) > outer:                           # 别冲出城市外延
        return False
    if outside_wall:
        return min(dists) > wallr + 3                # 废墟整体在墙外
    return max(dists) < wallr - 2                    # 大院整体在墙内


def _floating_corners_ok(bx, bz, sx, sz, center_x, center_z,
                         core_keepout: int = None,
                         outer_end_r: int = None) -> bool:
    """Sky statues can hover over the outer ward without needing wall footprint."""
    keepout = LANDMARK_CORE_KEEPOUT if core_keepout is None else core_keepout
    outer = OUTER_RING_END_R if outer_end_r is None else outer_end_r
    half_x, half_z = sx // 2, sz // 2
    dists = [math.hypot(cx - center_x, cz - center_z)
             for cx in (bx - half_x, bx + half_x)
             for cz in (bz - half_z, bz + half_z)]
    return min(dists) >= keepout and max(dists) <= outer


def _footprint_has_water(features, sx0, sz0, sx1, sz1) -> bool:
    patch = features.is_water[sz0:sz1 + 1, sx0:sx1 + 1]
    if patch.size == 0:
        return False
    # 原为 .any()（1 格水即通过）→ 大地标搁浅。改为水占比 ≥ 阈值才算水域。
    return bool(patch.mean() >= LANDMARK_WATER_MIN_FRAC)


_BASE_OFFSET_CACHE: dict[str, int] = {}
_MODEL_HEIGHT_CACHE: dict[str, int] = {}


def _model_height(path: str) -> int:
    """Return model Y size for sky/sink placement decisions."""
    if path in _MODEL_HEIGHT_CACHE:
        return _MODEL_HEIGHT_CACHE[path]
    try:
        h = int(np.load(path, allow_pickle=True).shape[0])
    except Exception as exc:
        print(f"   [WARN] model height read failed {os.path.basename(path)}: {exc!r}")
        h = 0
    _MODEL_HEIGHT_CACHE[path] = h
    return h


def _base_layer_offset(path: str, min_frac: float = 0.08, max_off: int = 4) -> int:
    """跳过 npy 底部稀疏层（扫描噪声/零星地基）→ 让"第一个够密的层"贴地。

    paste_volume 按"第一个有任意实心块"对齐；若底层只有零星几块（如四不合院 y=0 仅
    35 个苔藓），真地板会被抬高、底下露空（用户"少了一格"）。返回应下沉的层数。
    """
    if path in _BASE_OFFSET_CACHE:
        return _BASE_OFFSET_CACHE[path]
    try:
        v = np.load(path, allow_pickle=True)
    except Exception as e:
        print(f"   ⚠️ _base_layer_offset 读 {os.path.basename(path)} 失败：{e!r}")
        _BASE_OFFSET_CACHE[path] = 0
        return 0
    NY, NZ, NX = v.shape
    thresh = max(1, int(min_frac * NZ * NX))
    off = 0
    for y in range(min(max_off + 1, NY)):
        solid = sum(1 for b in v[y].ravel()
                    if (b.get("id", "minecraft:air") if isinstance(b, dict)
                        else str(b)) != "minecraft:air")
        if solid >= thresh:
            off = y
            break
    _BASE_OFFSET_CACHE[path] = off
    return off


def _resolve_landmark_specs(center_x: int, center_z: int,
                            ctx: ScanContext,
                            terrain_map: np.ndarray,
                            scan_volume: np.ndarray,
                            codec: BlockCodec) -> list[dict]:
    """Pick style-aware landmark specs; fall back to the Chinese landmark set."""
    if not LANDMARK_STYLE_OVERRIDES_ENABLED:
        return LANDMARK_SPECS
    scx, scz = ctx.w2s(center_x, center_z)
    if not (0 <= scz < terrain_map.shape[0] and 0 <= scx < terrain_map.shape[1]):
        return LANDMARK_SPECS

    style = _environment_style_for(terrain_map, scan_volume, codec, scx, scz)
    if not style or style == "chinese":
        return LANDMARK_SPECS

    # 水域标志物（破船 / bloop 海怪）独立于建筑风格，前置补回，避免被风格 specs 整体
    # 替换掉（无水的 footprint 会在放置阶段自动跳过）。
    water_specs = ([s for s in LANDMARK_SPECS if s.get("requires_water")]
                   if LANDMARK_WATER_ALWAYS_ENABLED else [])

    for current in (style,):
        specs = LANDMARK_STYLE_SPECS.get(current)
        if specs:
            print(f"   地标风格: {style} -> {current}（+水地标 {len(water_specs)}）")
            return water_specs + specs
    for current in ENV_BUILDING_STYLE_FALLBACKS.get(style, ()):
        specs = LANDMARK_STYLE_SPECS.get(current)
        if specs:
            print(f"   地标风格: {style} -> {current}（+水地标 {len(water_specs)}）")
            return water_specs + specs
    return LANDMARK_SPECS


def _try_terraform(spec_fp, height_map, features, ctx, terrain_map):
    """普通模式 terraform：标准预算失败 → 高台地基兜底（吊脚楼）。返回 tr 或 None。"""
    tr = terraform_for_building(
        footprint_xz=spec_fp, height_map=height_map, features=features, ctx=ctx,
        terrain_map=terrain_map, max_cut=LANDMARK_MAX_CUT, max_fill=LANDMARK_MAX_FILL)
    if tr.success:
        return tr
    if not FOUNDATION_FALLBACK_ENABLED:
        return None
    tr_fb = terraform_for_building(
        footprint_xz=spec_fp, height_map=height_map, features=features, ctx=ctx,
        terrain_map=terrain_map, max_cut=FOUNDATION_MAX_CUT,
        max_fill=FOUNDATION_MAX_FILL, target_strategy=FOUNDATION_STRATEGY)
    if not tr_fb.success:
        return None
    if FOUNDATION_STILT_ENABLED:
        tr_fb.fill_blocks = _stiltify_fill(
            tr_fb.fill_blocks, FOUNDATION_STILT_LEG_BLOCK, FOUNDATION_STILT_LEG_SPACING)
    return tr_fb


def _giant_statue_site_ok(spec: dict, height_map: np.ndarray, features,
                          ctx: ScanContext, scx: int, scz: int,
                          fp_sx0: int, fp_sz0: int,
                          fp_sx1: int, fp_sz1: int) -> bool:
    """Extra safety for huge statues.

    Reject ridge/high-mountain peaks and very rough footprints. Nearby
    obstructing terrain is handled later by carving a reveal pocket.
    """
    if not spec.get("giant_statue"):
        return True
    patch = height_map[fp_sz0:fp_sz1 + 1, fp_sx0:fp_sx1 + 1]
    if patch.size == 0 or np.any(patch <= int(ctx.min_y)):
        return False
    relief = int(patch.max()) - int(patch.min())
    if relief > int(DARK_COLOSSUS_MAX_RELIEF):
        return False

    slope_patch = features.slope_map[fp_sz0:fp_sz1 + 1, fp_sx0:fp_sx1 + 1]
    if float(np.median(slope_patch)) > float(DARK_COLOSSUS_MAX_MEDIAN_SLOPE):
        return False

    ground_y = int(height_map[scz, scx])
    valid_heights = features.height_map[features.valid_mask]
    if valid_heights.size:
        median_h = float(np.percentile(valid_heights, 50))
        high85 = float(np.percentile(valid_heights, 85))
        high75 = float(np.percentile(valid_heights, 75))
        if high85 - median_h > 12 and ground_y >= high85:
            return False
    else:
        high75 = float("inf")

    cz0, cz1 = max(0, scz - 8), min(height_map.shape[0] - 1, scz + 8)
    cx0, cx1 = max(0, scx - 8), min(height_map.shape[1] - 1, scx + 8)
    if ground_y >= high75 and bool(features.ridge_mask[cz0:cz1 + 1, cx0:cx1 + 1].any()):
        return False

    return True


def _carve_giant_statue_reveal(spec: dict, center_x: int, center_z: int,
                               bx: int, bz: int, base_y: int,
                               fp_sx0: int, fp_sz0: int,
                               fp_sx1: int, fp_sz1: int,
                               height_map: np.ndarray,
                               ctx: ScanContext) -> int:
    """Cut nearby terrain so a giant statue emerges from a mountain/valley wall.

    The footprint stays handled by terraform. This pass clears extra rock around
    the legs and along the side facing the soul tree, preventing the model from
    being swallowed by terrain without allowing true mountain-top placement.
    """
    if not (spec.get("giant_statue") and DARK_COLOSSUS_CARVE_ENABLED):
        return 0
    margin = int(DARK_COLOSSUS_CARVE_MARGIN)
    front = int(DARK_COLOSSUS_CARVE_FRONT)
    x0, x1 = fp_sx0 - margin, fp_sx1 + margin
    z0, z1 = fp_sz0 - margin, fp_sz1 + margin

    # Extend the reveal pocket toward the city center, i.e. the side the statue faces.
    dir_x = 0 if center_x == bx else (1 if center_x > bx else -1)
    dir_z = 0 if center_z == bz else (1 if center_z > bz else -1)
    if abs(center_x - bx) >= abs(center_z - bz):
        if dir_x > 0:
            x1 += front
        elif dir_x < 0:
            x0 -= front
    else:
        if dir_z > 0:
            z1 += front
        elif dir_z < 0:
            z0 -= front

    NZ, NX = height_map.shape
    x0, x1 = max(0, x0), min(NX - 1, x1)
    z0, z1 = max(0, z0), min(NZ - 1, z1)
    cap_y = int(base_y) + int(DARK_COLOSSUS_CARVE_HEIGHT)
    blocks = []
    changed_cols = 0
    for sz in range(z0, z1 + 1):
        for sx in range(x0, x1 + 1):
            h = int(height_map[sz, sx])
            if h <= int(ctx.min_y) or h <= int(base_y) + 2:
                continue
            wx, wz = ctx.s2w(sx, sz)
            top = min(h, cap_y)
            for y in range(int(base_y) + 1, top + 1):
                blocks.append({"x": wx, "y": y, "z": wz, "id": "minecraft:air"})
            changed_cols += 1
            # Keep downstream reads conservative: this generated open cut is now at base_y.
            height_map[sz, sx] = min(h, int(base_y))
    ok_blocks = 0
    for i in range(0, len(blocks), 4096):
        batch = blocks[i:i + 4096]
        if set_blocks_batch(batch):
            ok_blocks += len(batch)
    if ok_blocks:
        print(f"   巨像显露削山：{changed_cols} 列 / {ok_blocks} 方块")
    return ok_blocks


def _surface_y(wx: int, wz: int, height_map: np.ndarray, ctx: ScanContext):
    xs, zs = ctx.w2s(wx, wz)
    NZ, NX = height_map.shape
    if not (0 <= xs < NX and 0 <= zs < NZ):
        return None
    y = int(height_map[zs, xs])
    return y if y > int(ctx.min_y) else None


def _scatter_leg_debris(path: str, origin: tuple[int, int, int],
                        height_map: np.ndarray, ctx: ScanContext,
                        count: int = DARK_COLOSSUS_DEBRIS_COUNT) -> int:
    """Scatter broken blocks near the lower solid columns of a giant statue."""
    try:
        vol = np.load(path, allow_pickle=True)
    except Exception as exc:
        print(f"   ⚠️ 巨像碎石跳过：读取模型失败 {exc!r}")
        return 0
    NY, NZ, NX = vol.shape
    leg_layers = min(max(12, NY // 10), 28)
    leg_cells = []
    for z in range(NZ):
        for x in range(NX):
            for y in range(leg_layers):
                b = vol[y, z, x]
                bid = b.get("id", "minecraft:air") if isinstance(b, dict) else str(b)
                if bid != "minecraft:air":
                    leg_cells.append((x, z))
                    break
    if not leg_cells:
        return 0

    rng = random.Random(f"giant_debris_{origin}")
    batch = []
    ox, _oy, oz = origin
    for _ in range(int(count)):
        lx, lz = rng.choice(leg_cells)
        wx = int(ox + lx + rng.randint(-5, 5))
        wz = int(oz + lz + rng.randint(-5, 5))
        gy = _surface_y(wx, wz, height_map, ctx)
        if gy is None:
            continue
        block = rng.choice(_GIANT_RUBBLE)
        batch.append({"x": wx, "y": gy, "z": wz, "id": block})
        if rng.random() < 0.22:
            batch.append({"x": wx, "y": gy + 1, "z": wz, "id": block})
        if rng.random() < 0.10:
            batch.append({"x": wx, "y": gy + 1, "z": wz, "id": "minecraft:cobweb"})
    if batch and set_blocks_batch(batch):
        return len(batch)
    return 0


def place_landmarks(center_x: int, center_z: int,
                    ctx: ScanContext,
                    codec: BlockCodec,
                    height_map: np.ndarray,
                    terrain_map: np.ndarray,
                    scan_volume: np.ndarray,
                    placed_origins: list,
                    placed_boxes: list,
                    locked_rects: list,
                    dry_run: bool = False,
                    exclude_files: set = None,
                    specs_override: list = None,
                    core_keepout: int = None,
                    wall_radius: int = None,
                    outer_end_r: int = None) -> list:
    """按 LANDMARK_SPECS 摆放大地标。返回已放置 info 列表，并就地扩 placed_* / locked_rects。

    exclude_files：按文件名(basename)排除的地标——小图用许愿树当核心时传
    {"许愿树.npy"}，避免许愿树同时作核心又作地标重复放置。
    specs_override：给定则用它替代 _resolve_landmark_specs（小图传专用小地标集）。
    core_keepout/wall_radius/outer_end_r：None 时用 config 全局；传 ctx.city_dims
    的值即接入自适应缩放（小图墙半径小，地标才落得下）。
    """
    features = getattr(ctx, "terrain_features", None)
    if features is None:
        print("   ⚠️ 地标跳过：ctx.terrain_features 缺失")
        return []
    NZ, NX = height_map.shape
    exclude_files = exclude_files or set()
    placed: list = []
    skipped: list = []

    landmark_specs = (specs_override if specs_override is not None
                      else _resolve_landmark_specs(
                          center_x, center_z, ctx, terrain_map, scan_volume, codec))

    for spec in landmark_specs:
        path = spec.get("path")
        name = spec.get("file") or os.path.basename(path or "")
        if name in exclude_files:
            skipped.append((name, "excluded_as_core"))
            continue
        if not path:
            path = os.path.join(LANDMARK_ROOT, name)
        if not os.path.isfile(path):
            skipped.append((name, "missing_npy"))
            continue
        requires_water = bool(spec.get("requires_water"))
        outside_wall = bool(spec.get("outside_wall"))
        info = _place_one(spec, path, requires_water, outside_wall,
                          center_x, center_z, ctx, codec, height_map,
                          terrain_map, scan_volume, features, NX, NZ,
                          placed_boxes, dry_run,
                          core_keepout=core_keepout, wall_radius=wall_radius,
                          outer_end_r=outer_end_r)
        # 正常搜 49 候选全失败 → 对锚点强制平台兜底（water/墙外地标不强平）。
        if info is None and FORCE_PLATFORM_ENABLED \
                and not requires_water and not outside_wall \
                and not spec.get("monster_statue"):
            info = _place_one(spec, path, requires_water, outside_wall,
                              center_x, center_z, ctx, codec, height_map,
                              terrain_map, scan_volume, features, NX, NZ,
                              placed_boxes, dry_run, force=True,
                              core_keepout=core_keepout, wall_radius=wall_radius,
                              outer_end_r=outer_end_r)
        if info is None:
            skipped.append((name, info_reason(spec)))
            continue
        placed_origins.append((info["origin"][0], info["origin"][1], info["origin"][2]))
        placed_boxes.append(info["box"])
        locked_rects.append(info["rect"])
        placed.append(info)

    if placed:
        print(f"   🏯 地标放置 {len(placed)}：" + "、".join(p["file"] for p in placed))
    if skipped:
        print("   ⏭️ 地标跳过 {}：".format(len(skipped))
              + "，".join(f"{n}({r})" for n, r in skipped))
    return placed


def info_reason(spec) -> str:
    """跳过原因占位（细分原因在 _place_one 里 print）。"""
    if spec.get("requires_water"):
        return "no_site_or_water"
    if spec.get("outside_wall"):
        return "no_site_outside"
    return "no_buildable_site"


def _place_one(spec, path, requires_water, outside_wall,
               center_x, center_z, ctx, codec, height_map, terrain_map,
               scan_volume, features, NX, NZ, placed_boxes, dry_run,
               force: bool = False,
               core_keepout: int = None, wall_radius: int = None,
               outer_end_r: int = None):
    """对单个地标做候选搜索 + 放置。成功返回 info dict，失败返回 None。

    force=True：普通模式 terraform 改用强制平台兜底（无上限 + sentinel carve），
    保证落地（仅在正常搜索全失败后由 place_landmarks 重试时传）。
    """
    for da in _ANGLE_OFFS:
        for dr in _RADIUS_OFFS:
            bx, bz = _anchor(center_x, center_z, spec["angle"] + da,
                             spec["radius"] + dr)
            rotation = compute_facing_rotation(center_x, center_z, bx, bz)
            sx, sz = footprint_xz(path, rotation)
            if spec.get("floating"):
                if not _floating_corners_ok(
                        bx, bz, sx, sz, center_x, center_z,
                        core_keepout=core_keepout, outer_end_r=outer_end_r):
                    continue
            else:
                if not _corners_ok(
                        bx, bz, sx, sz, center_x, center_z, outside_wall,
                        core_keepout=core_keepout, wall_radius=wall_radius,
                        outer_end_r=outer_end_r):
                    continue
            scx, scz = ctx.w2s(bx, bz)
            fp_sx0 = scx - sx // 2
            fp_sx1 = scx + (sx - sx // 2 - 1)
            fp_sz0 = scz - sz // 2
            fp_sz1 = scz + (sz - sz // 2 - 1)
            if fp_sx0 < 0 or fp_sx1 >= NX or fp_sz0 < 0 or fp_sz1 >= NZ:
                continue
            if not _giant_statue_site_ok(
                    spec, height_map, features, ctx, scx, scz,
                    fp_sx0, fp_sz0, fp_sx1, fp_sz1):
                continue
            box = make_box_from_center(bx, bz, sx, sz, padding=2)
            if any(boxes_intersect(box, b) for b in placed_boxes):
                continue
            if requires_water and not _footprint_has_water(
                    features, fp_sx0, fp_sz0, fp_sx1, fp_sz1):
                continue

            # ── 定 base_y ──
            spec_fp = (fp_sx0, fp_sz0, fp_sx1, fp_sz1)
            tr = None
            if spec.get("floating") and EYE_KING_FLOATING_ENABLED:
                center_ground = int(height_map[scz, scx])
                if center_ground <= int(ctx.min_y):
                    continue
                model_h = _model_height(path)
                sky_y = center_ground + int(EYE_KING_FLOAT_HEIGHT)
                if model_h > 0:
                    sky_y = min(sky_y, 319 - model_h)
                base_y = max(center_ground + 32, int(sky_y))
            elif spec.get("water_submerge"):
                base_y = int(SEA_LEVEL) - int(WATER_MONSTER_SINK_DEPTH)
            elif spec.get("giant_statue"):
                base_y = int(height_map[scz, scx])
                if base_y <= int(ctx.min_y):
                    continue
            elif requires_water:
                base_y = int(SEA_LEVEL)              # 桥/水榭坐水面，不填水
            elif outside_wall:
                base_y = int(height_map[scz, scx])   # 废墟坐自然地面
                if base_y <= int(ctx.min_y):
                    continue                         # sentinel 列
            elif force:
                tr = terraform_force_platform(spec_fp, height_map, features,
                                              ctx, terrain_map)
                if not tr.success:
                    continue
                if FOUNDATION_STILT_ENABLED:
                    tr.fill_blocks = _stiltify_fill(
                        tr.fill_blocks, FOUNDATION_STILT_LEG_BLOCK,
                        FOUNDATION_STILT_LEG_SPACING)
                base_y = int(tr.base_y)
            else:
                tr = _try_terraform(spec_fp, height_map, features, ctx, terrain_map)
                if tr is None:
                    continue
                base_y = int(tr.base_y)

            # 下沉稀疏底层 → 真地板贴地（修四不合院"少了一格"）。base_y 本身不改，
            # 只压低 origin_y，让 paste 把第一个够密的层对到 base_y。
            origin = (int(bx - sx // 2),
                      base_y - _base_layer_offset(path),
                      int(bz - sz // 2))
            if not dry_run:
                if tr is not None:
                    if not apply_terraform(tr, ctx, scan_volume=scan_volume,
                                           height_map=height_map, codec=codec):
                        continue                     # HTTP 失败 → 换下一候选
                if spec.get("giant_statue"):
                    _carve_giant_statue_reveal(
                        spec, center_x, center_z, bx, bz, base_y,
                        fp_sx0, fp_sz0, fp_sx1, fp_sz1,
                        height_map, ctx)
                if not (requires_water or outside_wall or spec.get("floating")):
                    clear_footprint_vegetation(fp_sx0, fp_sx1, fp_sz0, fp_sz1,
                                               base_y, ctx)
                lm_terrain = _environment_terrain_for(
                    terrain_map, scan_volume, codec, scx, scz)
                remap = make_remap(lm_terrain)
                if os.path.basename(path) == "eye_king.npy":
                    remap = make_eye_king_remap(remap)
                try:
                    paste_volume(path, origin=origin, clear_target=False,
                                 rotation=rotation,
                                 block_remap=remap)
                    if spec.get("leg_debris"):
                        debris_n = _scatter_leg_debris(path, origin, height_map, ctx)
                        if debris_n:
                            print(f"   巨像腿部碎石 {debris_n} 块")
                except Exception as exc:
                    spec_name = spec.get("file") or os.path.basename(path)
                    print(f"   ⚠️ 地标 {spec_name} paste 失败：{exc!r}")
                    continue
            return {
                "file": spec.get("file") or os.path.basename(path),
                "path": path, "origin": origin,
                "rotation": rotation, "box": box,
                "rect": (fp_sx0, fp_sx1, fp_sz0, fp_sz1),
                "role": "landmark",
            }
    return None


__all__ = ["place_landmarks"]
