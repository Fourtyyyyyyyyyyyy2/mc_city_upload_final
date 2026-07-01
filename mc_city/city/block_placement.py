"""Priority 2 卡 9.3：街区驱动 placement（1 街区 = 1 栋建筑）。

现实化设计（用户决策）：现有素材都是大楼（outer 26×29 / mid 32×57+ /
inner 60×74），塞不进 spec 原想的"每坊多栋 8×8 小楼 + 中庭"。改成：
- **1 街区 1 栋**：~100 街区 → ~100 栋，规整 grid，密度来自街区多。
- **主殿**：每公会最靠 plaza 的 mid 街区放 inner_<terrain> 高楼（deterministic）。
- **普通街区**：ring+terrain 池取建筑，朝城心；terraform footprint(cut/fill≤30)
  失败则跳过整块。
- 大 mid 楼溢出街区靠碰撞框 boxes_intersect 自动稀疏化邻块。
- 不调旧 place_buildings_grid；不动 narrative/scan/mc/roads。

`_building_slots_in_block` 在 1 街区 1 栋模型下退化成"街区中心 1 个 slot"，不再单列。
"""
from __future__ import annotations

import os
import random

import numpy as np

from ..config import (
    BLOCK_BUILDING_PADDING,
    BLOCK_SIZE,
    BLOCK_TERRAFORM_MAX_CUT,
    BLOCK_TERRAFORM_MAX_FILL,
    COMMERCIAL_STREET_ENABLED,
    COMMERCIAL_STREET_PREFIX,
    ENV_BUILDING_PACKS_ENABLED,
    ENV_BUILDING_STYLE_BY_TERRAIN,
    ENV_STYLE_PER_BLOCK,
    FOUNDATION_FALLBACK_ENABLED,
    FOUNDATION_MAX_CUT,
    FOUNDATION_MAX_FILL,
    FOUNDATION_STILT_ENABLED,
    FOUNDATION_STILT_LEG_BLOCK,
    FOUNDATION_STILT_LEG_SPACING,
    FOUNDATION_STRATEGY,
    FORCE_PLATFORM_ENABLED,
    GRID_LARGE_THRESHOLD,
    GRID_MAX_LARGE,
    GRID_MERGE_LARGE_BLOCKS,
    GRID_PREFER_SMALL,
    GRID_UNIQUE_BUILDINGS,
    MAIN_HALL_FALLBACK_TO_MID,
    NEXT_ROAD_WIDTH,
    SEA_CITY_STILT_DECK_OFFSET,
    SEA_CITY_STILT_OVER_WATER,
    SEA_CITY_STILT_WATER_FRAC,
)
from ..mc.codec import BlockCodec
from ..mc.placement import paste_volume, set_blocks_batch
from ..scan.coord_frame import ScanContext
from .components import list_guild_files, list_style_chain_files, list_style_files
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
    terraform_water_stilt,
)
from .terrain import TERRAIN_NAMES
from .trees import clear_footprint_vegetation

_GUILDS = ("soul_scholars", "soul_engineers", "merchants", "adventurers")

# 占位"色块建筑"（卡 9.5：真实城用占位色块，不用过大的 npy）。
# 墙 + 屋顶按公会上色（俯视看到屋顶色 = 公会分区）；主殿金顶。
GUILD_BUILDING_COLOR: dict[str, str] = {
    "soul_scholars":  "minecraft:blue_concrete",
    "soul_engineers": "minecraft:orange_concrete",
    "merchants":      "minecraft:yellow_concrete",
    "adventurers":    "minecraft:red_concrete",
}


def placeholder_dims(region, is_main: bool) -> tuple[int, int, int]:
    """占位建筑尺寸 (sx, sz, height)，均 < 街区 30 留街道；按 ring/role 选。"""
    if is_main:
        return (28, 28, 16)
    if region.ring == "mid":
        return (24, 24, 11)
    return (22, 22, 7)


def placeholder_render(region, origin, sx: int, sz: int,
                       height: int, is_main: bool) -> None:
    """空心色块盒子：4 面墙 + 屋顶（terraform 后的 base_y 当地板）。"""
    ox, oy, oz = origin
    wall = GUILD_BUILDING_COLOR.get(region.guild, "minecraft:white_concrete")
    roof = "minecraft:gold_block" if is_main else wall
    top = oy + int(height)
    payload: list[dict] = []
    for dx in range(sx):
        for dz in range(sz):
            x = ox + dx
            z = oz + dz
            if dx in (0, sx - 1) or dz in (0, sz - 1):
                for y in range(oy + 1, top + 1):
                    payload.append({"x": x, "y": y, "z": z, "id": wall})
            payload.append({"x": x, "y": top + 1, "z": z, "id": roof})
            if len(payload) >= 1000:
                set_blocks_batch(payload)
                payload = []
    if payload:
        set_blocks_batch(payload)


def _terrain_at(terrain_map: np.ndarray, sx: int, sz: int) -> str:
    code = int(terrain_map[sz, sx])
    return TERRAIN_NAMES[code] if code < len(TERRAIN_NAMES) else "plains"


_BADLANDS_SURFACE_HINTS = ("terracotta", "red_sand")


def _surface_block_id(scan_volume: np.ndarray, codec: BlockCodec,
                      sx: int, sz: int) -> str:
    """Read the top non-air block at a scan column for biome skin routing."""
    if scan_volume is None or codec is None:
        return ""
    if not (0 <= sz < scan_volume.shape[1] and 0 <= sx < scan_volume.shape[2]):
        return ""
    for sy in range(scan_volume.shape[0] - 1, -1, -1):
        code = int(scan_volume[sy, sz, sx])
        if codec.is_air(code):
            continue
        try:
            return codec.decode(code).split("[", 1)[0]
        except Exception as exc:
            print(f"  ⚠️ 环境建筑包表层方块解码失败 ({sx},{sz},{sy}): {exc!r}")
            return ""
    return ""


def _environment_terrain_for(terrain_map: np.ndarray,
                             scan_volume: np.ndarray,
                             codec: BlockCodec,
                             sx: int, sz: int) -> str:
    """Resolve the terrain at a column, treating terracotta surfaces as badlands."""
    terrain = _terrain_at(terrain_map, sx, sz)
    surface = _surface_block_id(scan_volume, codec, sx, sz)
    if any(hint in surface for hint in _BADLANDS_SURFACE_HINTS):
        terrain = "badlands"
    return terrain


def _environment_style_for(terrain_map: np.ndarray,
                           scan_volume: np.ndarray,
                           codec: BlockCodec,
                           sx: int, sz: int) -> str | None:
    """Choose a building style from terrain, with terracotta columns as badlands."""
    if not ENV_BUILDING_PACKS_ENABLED:
        return None
    terrain = _environment_terrain_for(terrain_map, scan_volume, codec, sx, sz)
    return ENV_BUILDING_STYLE_BY_TERRAIN.get(terrain)


def _stiltify_fill(fill_blocks: list, leg_block: str, leg_spacing: int) -> list:
    """高台地基 → 吊脚楼：每列顶层留实心甲板，下方只在 footprint 周边 + 内部网格
    留栅栏腿，其余镂空。只处理 fill_blocks（实心填土），cut_blocks 不动。

    fill_blocks = [(xw, yw, zw, bid), ...]（terraform 输出，世界坐标）。
    """
    from collections import defaultdict
    cols: dict = defaultdict(list)
    for (xw, yw, zw, bid) in fill_blocks:
        cols[(xw, zw)].append((yw, bid))
    if not cols:
        return fill_blocks

    xs = [x for (x, _z) in cols]
    zs = [z for (_x, z) in cols]
    xmin, xmax, zmin, zmax = min(xs), max(xs), min(zs), max(zs)

    out: list = []
    for (xw, zw), ys in cols.items():
        ys.sort()
        top_y, top_bid = ys[-1]
        out.append((xw, top_y, zw, top_bid))            # 甲板顶层（实心，全列保留）
        is_perim = xw in (xmin, xmax) or zw in (zmin, zmax)
        is_leg = is_perim or (xw % leg_spacing == 0 and zw % leg_spacing == 0)
        if is_leg:
            low_y = ys[0][0]
            for yy in range(low_y, top_y):              # 栅栏腿：最低 fill 到甲板下
                out.append((xw, yy, zw, leg_block))
        # 非腿列：甲板下镂空（不填）→ 吊脚楼架空感
    return out


def _footprint_water_frac(terrain_map, fp_sx0: int, fp_sx1: int,
                          fp_sz0: int, fp_sz1: int) -> float:
    """footprint 内 water(code 4) 格占比；terrain_map 缺失返回 0。"""
    if terrain_map is None:
        return 0.0
    NZ, NX = terrain_map.shape
    x0 = max(0, fp_sx0); x1 = min(NX - 1, fp_sx1)
    z0 = max(0, fp_sz0); z1 = min(NZ - 1, fp_sz1)
    if x0 > x1 or z0 > z1:
        return 0.0
    patch = terrain_map[z0:z1 + 1, x0:x1 + 1]
    return float((patch == 4).sum()) / float(patch.size)


def _try_claim_2x2(r, region_by_key: dict, used_ids: set, period: int):
    """找一个含 r 的 2×2 同圈层同公会、未占未阻挡街区簇。

    依次把 r 当 4 个角试。返回 [4 个 BlockRegion]（左上→右下）或 None。
    """
    px, pz = r.x0, r.z0
    for ox, oz in ((0, 0), (-period, 0), (0, -period), (-period, -period)):
        tlx, tlz = px + ox, pz + oz
        keys = [(tlx, tlz), (tlx + period, tlz),
                (tlx, tlz + period), (tlx + period, tlz + period)]
        cluster = [region_by_key.get(k) for k in keys]
        if all(c is not None and not c.blocked and id(c) not in used_ids
               and c.ring == r.ring and c.guild == r.guild for c in cluster):
            return cluster
    return None


def _pick_main_hall_blocks(regions: list) -> dict:
    """每公会取最靠 plaza（中心）的 mid 街区。返回 {guild: region}。

    距离相等时按 (x0, z0) 决胜，保证 deterministic。
    """
    out: dict = {}
    for g in _GUILDS:
        cands = [r for r in regions
                 if r.ring == "mid" and r.guild == g and not r.blocked]
        if not cands:
            continue
        out[g] = min(cands, key=lambda r: (
            (r.x0 + r.x1) ** 2 + (r.z0 + r.z1) ** 2, r.x0, r.z0))
    return out


def place_block_buildings(regions: list,
                          ctx: ScanContext,
                          codec: BlockCodec,
                          height_map: np.ndarray,
                          terrain_map: np.ndarray,
                          scan_volume: np.ndarray,
                          placed_origins: list,
                          placed_boxes: list,
                          center_x: int,
                          center_z: int,
                          locked_rects: list = None,
                          box_padding: int = BLOCK_BUILDING_PADDING,
                          block_size: int = BLOCK_SIZE,
                          next_road_width: int = NEXT_ROAD_WIDTH,
                          large_threshold: int = GRID_LARGE_THRESHOLD,
                          max_large: int = GRID_MAX_LARGE,
                          dry_run: bool = False,
                          dims_fn=None,
                          render_fn=None,
                          ) -> dict:
    """逐街区放 1 栋建筑。返回 {ring: [info, ...]}（兼容旧版结构）。

    info = {path, origin, rotation, guild, role, ring}。role: "main"|"house"。
    dry_run=True 时跳过 HTTP（terraform/paste/occupied），terraform 视作成功
    (base_y=街区中心地表)，只验证选址/碰撞/主殿逻辑（demo 用）。

    可注入钩子（默认 None = 用 npy 池 + paste_volume）：
      dims_fn(region, is_main) -> (sx, sz, height)：自定 footprint（占位色块用），
        给出时不查 npy 池、rotation=0；
      render_fn(region, origin, sx, sz, height, is_main)：自定渲染，替代 paste。
    """
    if locked_rects is None:
        locked_rects = []
    NZ, NX = height_map.shape
    features = getattr(ctx, "terrain_features", None)
    rng = random.Random(f"{center_x}_{center_z}")
    result: dict = {"mid": [], "outer": []}
    used_basenames: set = set()              # 卡：整城建筑去重（按 npy 文件名）
    large_count = 0                          # 卡：已放的大楼数（限 GRID_MAX_LARGE）

    # 拒因统计：每个跳过原因累计，结束打一行，定位"为什么某扇区没楼"。
    reject_stats: dict = {}
    foundation_used = 0

    def _rej(reason: str):
        reject_stats[reason] = reject_stats.get(reason, 0) + 1

    platform_used = 0
    water_stilt_used = 0
    main_blocks = _pick_main_hall_blocks(regions)
    main_ids = {id(r) for r in main_blocks.values()}

    others = [r for r in regions if not r.blocked and id(r) not in main_ids]
    others.sort(key=lambda r: (-r.terrain_score, r.x0, r.z0))
    order = list(main_blocks.values()) + others

    # 大楼 2×2 合并用：街区按 (x0,z0) 索引 + 已被合并占用的街区 id 集 + 周期。
    region_by_key = {(rr.x0, rr.z0): rr for rr in regions}
    used_region_ids: set = set()
    merge_period = int(block_size) + int(next_road_width)
    merged_count = 0
    center_sx, center_sz = ctx.w2s(center_x, center_z)
    # ENV_STYLE_PER_BLOCK=True：不固定整城风格，留 None → 每个街区按自己地形解析
    # （下方 env_style = city_env_style or _environment_style_for(...)）。
    # False：整城统一用城心地形的风格（旧行为）。
    city_env_style = None
    if not ENV_STYLE_PER_BLOCK and 0 <= center_sx < NX and 0 <= center_sz < NZ:
        city_env_style = _environment_style_for(
            terrain_map, scan_volume, codec, center_sx, center_sz)

    for r in order:
        if id(r) in used_region_ids:           # 已被某大楼并入 2×2 → 跳过
            continue
        is_main = id(r) in main_ids
        bx = (r.x0 + r.x1) // 2
        bz = (r.z0 + r.z1) // 2
        scx_b, scz_b = ctx.w2s(bx, bz)
        if not (0 <= scx_b < NX and 0 <= scz_b < NZ):
            _rej("center_oob")
            continue

        height = None
        if dims_fn is not None:                          # 占位色块模式
            sx_b, sz_b, height = dims_fn(r, is_main)
            sx_b, sz_b = int(sx_b), int(sz_b)
            rotation = 0
            path = None
        else:                                            # npy 模式
            ring_for_pool = "inner" if is_main else r.ring   # 主殿用 inner 高楼
            # Environment skin pools override guild pools only when matching assets exist.
            env_style = city_env_style or _environment_style_for(
                terrain_map, scan_volume, codec, scx_b, scz_b)
            pool = list_style_chain_files(ring_for_pool, env_style) if env_style else []
            # 风格优先：记下"主风格"(env_style 本体，不含 medieval 回退)的文件名。
            # 选择时先用尽主风格，回退池(medieval)只补主风格用完后的剩余街区，
            # 避免 medieval 凭数量优势把 western/steampunk 淹掉（badlands 城显示成中世纪）。
            primary_basenames: set[str] = set()
            if pool and env_style:
                primary_basenames = {os.path.basename(p)
                                     for p in list_style_files(ring_for_pool, env_style)}
            main_from_mid = False
            if not pool and env_style and is_main and MAIN_HALL_FALLBACK_TO_MID:
                pool = list_style_chain_files("mid", env_style)
                primary_basenames = {os.path.basename(p)
                                     for p in list_style_files("mid", env_style)}
                main_from_mid = True

            # 卡 9.6：按公会路由（components/<ring>_<guild>/）。池空 → 留空不放。
            if not pool:
                pool = list_guild_files(ring_for_pool, r.guild)
            # 卡 11.2：inner_<guild> 池空时主殿回退 mid 池（挑最大一栋当主殿），
            # 而不是 no_pool 跳过 → 补回 scholars/engineers/adventurers 等空 inner 的主殿。
            if not pool and is_main and MAIN_HALL_FALLBACK_TO_MID:
                pool = list_guild_files("mid", r.guild)
                main_from_mid = True
            # 卡 12.1 (A)：商业街开启时，05_商业街_* 小店专供 [4.8] 沿街铺，
            # 不进网格 → 商业街辨识度立住、网格腾给住宅/公会楼。主殿不受影响（大楼无此前缀）。
            if COMMERCIAL_STREET_ENABLED:
                pool = [p for p in pool
                        if not os.path.basename(p).startswith(COMMERCIAL_STREET_PREFIX)]
            if not pool:
                _rej(f"no_pool:{r.guild}")
                continue
            if is_main:
                # inner 主殿用字母序首个（原行为）；回退 mid 池时挑 footprint 最大一栋。
                path = (max(pool, key=lambda p: max(footprint_xz(p, 0)))
                        if main_from_mid else sorted(pool)[0])
            elif GRID_UNIQUE_BUILDINGS:
                # 优先没用过的；都用过了才在全池里抽（池小于街区数时退化）
                unused = [p for p in pool
                          if os.path.basename(p) not in used_basenames]
                # 风格优先：主风格还有没用过的 → 只在主风格里选，medieval 回退让位。
                # 主风格全用过后 unused_primary 为空，自然落到全池(含 medieval)补余。
                if primary_basenames:
                    unused_primary = [p for p in unused
                                      if os.path.basename(p) in primary_basenames]
                    if unused_primary:
                        unused = unused_primary
                cand = unused if unused else pool
                if GRID_PREFER_SMALL:
                    if large_count >= max_large:      # 大楼已达上限 → 只挑小楼
                        small = [p for p in cand
                                 if max(footprint_xz(p, 0)) <= large_threshold]
                        cand = small or cand
                    cand = sorted(cand, key=lambda p: max(footprint_xz(p, 0)))
                    path = rng.choice(cand[:max(3, len(cand) // 2)])  # 较小一半里随机
                else:
                    path = rng.choice(cand)
            else:
                path = rng.choice(pool)
            rotation = compute_facing_rotation(center_x, center_z, bx, bz)
            sx_b, sz_b = footprint_xz(path, rotation)

        # 大楼 2×2 合并：footprint 超标 → 占同圈层同公会 2×2 簇，中心移到簇心。
        merged_cluster = None
        if (GRID_MERGE_LARGE_BLOCKS and dims_fn is None
                and max(sx_b, sz_b) > large_threshold):
            merged_cluster = _try_claim_2x2(r, region_by_key,
                                            used_region_ids, merge_period)
            if merged_cluster is not None:
                cx0 = min(c.x0 for c in merged_cluster)
                cx1 = max(c.x1 for c in merged_cluster)
                cz0 = min(c.z0 for c in merged_cluster)
                cz1 = max(c.z1 for c in merged_cluster)
                bx, bz = (cx0 + cx1) // 2, (cz0 + cz1) // 2
                scx_b, scz_b = ctx.w2s(bx, bz)
                if not (0 <= scx_b < NX and 0 <= scz_b < NZ):
                    merged_cluster = None              # 簇心越界 → 放弃合并

        fp_sx0 = scx_b - sx_b // 2
        fp_sx1 = scx_b + (sx_b - sx_b // 2 - 1)
        fp_sz0 = scz_b - sz_b // 2
        fp_sz1 = scz_b + (sz_b - sz_b // 2 - 1)
        if fp_sx0 < 0 or fp_sx1 >= NX or fp_sz0 < 0 or fp_sz1 >= NZ:
            _rej("footprint_oob")
            continue

        if merged_cluster is not None:
            box = (cx0, cx1, cz0, cz1)                 # 预留整个 2×2 地块
        else:
            box = make_box_from_center(bx, bz, sx_b, sz_b, padding=box_padding)
        if any(boxes_intersect(box, b) for b in placed_boxes):
            _rej("box_intersect")
            continue

        # 选址安全（已建结构占用）；dry_run 跳过 HTTP/scan 查询
        if not dry_run:
            occ, _ = is_location_occupied(scan_volume, bx, bz, ctx,
                                          padding=2, codec=codec)
            if occ:
                _rej("occupied")
                continue

        # terraform 街区建筑 footprint（cut/fill≤30）。失败 → 地基兜底（高台），
        # 再失败才跳过整块。
        terraformed = False
        tr = None
        water_frac = _footprint_water_frac(terrain_map, fp_sx0, fp_sx1,
                                           fp_sz0, fp_sz1)
        use_water_stilt = (SEA_CITY_STILT_OVER_WATER
                           and water_frac >= SEA_CITY_STILT_WATER_FRAC)
        if not dry_run and features is not None and use_water_stilt:
            # 水上吊脚楼：甲板抬海平面上、柱子打到海床、露水面（不填平水）。
            tr = terraform_water_stilt(
                footprint_xz=(fp_sx0, fp_sz0, fp_sx1, fp_sz1),
                height_map=height_map, terrain_map=terrain_map,
                scan_volume=scan_volume, ctx=ctx, codec=codec,
                deck_offset=SEA_CITY_STILT_DECK_OFFSET)
            if tr.success:
                if FOUNDATION_STILT_ENABLED:
                    tr.fill_blocks = _stiltify_fill(
                        tr.fill_blocks, FOUNDATION_STILT_LEG_BLOCK,
                        FOUNDATION_STILT_LEG_SPACING)
                water_stilt_used += 1
            else:
                _rej(f"water_stilt:{tr.reason.split('(')[0]}")
                continue
            base_y = int(tr.base_y)
            terraformed = True
        elif not dry_run and features is not None:
            tr = terraform_for_building(
                footprint_xz=(fp_sx0, fp_sz0, fp_sx1, fp_sz1),
                height_map=height_map, features=features, ctx=ctx,
                terrain_map=terrain_map,
                max_cut=BLOCK_TERRAFORM_MAX_CUT,
                max_fill=BLOCK_TERRAFORM_MAX_FILL,
            )
            if not tr.success and FOUNDATION_FALLBACK_ENABLED:
                # 卡：地形太陡 → 高台地基（高分位 base_y + 放宽 fill）填土垫平。
                tr_fb = terraform_for_building(
                    footprint_xz=(fp_sx0, fp_sz0, fp_sx1, fp_sz1),
                    height_map=height_map, features=features, ctx=ctx,
                    terrain_map=terrain_map,
                    max_cut=FOUNDATION_MAX_CUT,
                    max_fill=FOUNDATION_MAX_FILL,
                    target_strategy=FOUNDATION_STRATEGY,
                )
                if tr_fb.success:
                    if FOUNDATION_STILT_ENABLED:
                        # 吊脚楼：实心填土 → 实心顶 + 栅栏腿 + 下镂空
                        tr_fb.fill_blocks = _stiltify_fill(
                            tr_fb.fill_blocks, FOUNDATION_STILT_LEG_BLOCK,
                            FOUNDATION_STILT_LEG_SPACING)
                    tr = tr_fb
                    foundation_used += 1
            if not tr.success and FORCE_PLATFORM_ENABLED:
                # 第三层：强制平台兜底（无上限 + sentinel carve），保证街区不空。
                tr_fp = terraform_force_platform(
                    footprint_xz=(fp_sx0, fp_sz0, fp_sx1, fp_sz1),
                    height_map=height_map, features=features, ctx=ctx,
                    terrain_map=terrain_map)
                if tr_fp.success:
                    if FOUNDATION_STILT_ENABLED:
                        tr_fp.fill_blocks = _stiltify_fill(
                            tr_fp.fill_blocks, FOUNDATION_STILT_LEG_BLOCK,
                            FOUNDATION_STILT_LEG_SPACING)
                    tr = tr_fp
                    platform_used += 1
            if not tr.success:
                rk = tr.reason.split("(")[0] or "terraform_fail"
                _rej(f"terraform:{rk}")
                continue
            base_y = int(tr.base_y)
            terraformed = True
        else:
            base_y = int(height_map[scz_b, scx_b])
            if base_y <= int(ctx.min_y):
                _rej("sentinel")
                continue                            # sentinel 列跳过

        origin = (int(bx - sx_b // 2), base_y, int(bz - sz_b // 2))

        # 提交（dry_run 不写世界）
        if not dry_run:
            if terraformed:
                ok = apply_terraform(tr, ctx, scan_volume=scan_volume,
                                     height_map=height_map, codec=codec)
                if not ok:
                    _rej("apply_terraform_http_fail")
                    continue
            clear_footprint_vegetation(fp_sx0, fp_sx1, fp_sz0, fp_sz1,
                                       base_y, ctx)
            try:
                if render_fn is not None:
                    render_fn(r, origin, sx_b, sz_b, height, is_main)
                else:
                    # reskin：按该街区脚下地形把房子材质换成地形主题（plains/water 无主题→None）
                    block_terrain = _environment_terrain_for(
                        terrain_map, scan_volume, codec, scx_b, scz_b)
                    paste_volume(path, origin=origin, clear_target=False,
                                 rotation=rotation,
                                 block_remap=make_remap(block_terrain))
            except Exception as exc:
                _rej("paste_exception")
                print(f"  ⚠️ 街区建筑渲染失败 ({bx},{bz}): {exc!r}")
                continue

        placed_origins.append((int(bx), base_y, int(bz)))
        placed_boxes.append(box)
        locked_rects.append((fp_sx0, fp_sx1, fp_sz0, fp_sz1))
        if path:
            used_basenames.add(os.path.basename(path))   # 标记已用 → 不再撞脸
            if max(sx_b, sz_b) > large_threshold:    # 大楼计数（限额用）
                large_count += 1
        result[r.ring].append({
            "path": path or "placeholder", "origin": origin,
            "rotation": rotation,
            "guild": r.guild, "role": "main" if is_main else "house",
            "ring": r.ring,
        })
        if merged_cluster is not None:               # 标记并入的 2×2 街区为已用
            for c in merged_cluster:
                used_region_ids.add(id(c))
            merged_count += 1

    if merged_count:
        print(f"  🧩 大楼 2×2 合并地块 {merged_count} 处")
    if foundation_used:
        print(f"  🏗️ 高台地基兜底救回 {foundation_used} 栋（陡街区）")
    if platform_used:
        print(f"  🏛️ 强制平台兜底救回 {platform_used} 栋（极陡/sentinel 街区）")
    if water_stilt_used:
        print(f"  🌊 水上吊脚楼 {water_stilt_used} 栋（甲板抬海平面+栅栏腿+露水面）")
    if reject_stats:
        sorted_rej = sorted(reject_stats.items(), key=lambda kv: -kv[1])
        print(f"  📊 街区拒因 [候选={len(order)}]: "
              + ", ".join(f"{k}={v}" for k, v in sorted_rej))
    return result


__all__ = [
    "place_block_buildings",
    "placeholder_dims", "placeholder_render", "GUILD_BUILDING_COLOR",
]
