"""城市生成主编排器：build_city。

流程：
    [0] 全局伐树
    [1] 地形分析（terrain_map + suitability_map）
    [2] 骨架道路（环 + 放射）→ 道路邻近度图
    [3] 城市底板（水域 → 实地）
    [4] 城市核心（灵魂树）
    [5] 建筑放置：
        GRID_LAYOUT_ENABLED=True → [4.5] 中心广场+主道 + [5'] grid 街区驱动（占位色块）
        否则                     → [5] 三圈层散点 + [6] 模块化（住宅/商铺）
    [7] 再次刷一次城市底板（处理新增水域接触）
    [8] 城墙
    [9] 建筑接入道路（A*）
    [10] 叙事图层（NARRATIVE_ENABLED 控制；task 1.3+）
"""
import os

import numpy as np

from ..config import (
    BLOCK_DECOR_ENABLED,
    COMMERCIAL_STREET_ENABLED,
    DEFER_CARDINAL_ROADS,
    DARK_COLOSSUS_ANGLE,
    DARK_COLOSSUS_ANGLES,
    DARK_COLOSSUS_ENABLED,
    DARK_COLOSSUS_MIN_WALL_R,
    DARK_COLOSSUS_MAX_COUNT,
    DARK_COLOSSUS_OUTER_CLEARANCE,
    DARK_COLOSSUS_RADIUS_FRAC,
    DARK_COLOSSUS_WALL_CLEARANCE,
    GRID_LAYOUT_ENABLED,
    INVASION_RAID_ENABLED,
    NARRATIVE_TABLEAUX_ENABLED,
    GRID_STREETS_ENABLED,
    GRID_USE_NPY_BUILDINGS,
    INNER_MIN_SUITABILITY,
    INNER_RETRY_MIN_SUITABILITY,
    LANDMARK_ENABLED,
    LANDMARK_SPECS,
    NARRATIVE_ENABLED,
    RUIN_ENABLED,
    ROAD_ASTAR_CLEARANCE,
    ROAD_ASTAR_DOOR_OFFSET,
    ROAD_ASTAR_GATES,
    ROAD_ASTAR_MAX_STEP,
    ROAD_ASTAR_STAIRS,
    ROAD_ASTAR_STEP_PENALTY,
    ROAD_ASTAR_SURFACE,
    ROAD_ASTAR_WIDTH,
    GREENERY_ENABLED,
    ROAD_SYSTEM,
    SOUL_TREE_DOMINANT_TERRAIN,
    SMALL_CITY_CORE_WISH_TREE,
    SMALL_CITY_R_THRESHOLD,
    SMALL_CITY_WALL_WIDTH,
    SMALL_CITY_WALL_HEIGHT,
    SMALL_CITY_WALL_FLAT_TOP,
    SMALL_CITY_LANDMARK_SPECS,
    SMALL_CITY_LANDMARK_KEEPOUT,
    SUPER_LARGE_EYE_KINGS_ENABLED,
    SUPER_LARGE_EYE_KINGS_MIN_WALL_R,
    SUPER_LARGE_EYE_KING_ANGLES,
    SUPER_LARGE_EYE_KING_RADIUS_FRAC,
    WATER_REFRESH_AFTER_FLOOR,
    RADIUS_MAP,
    TERRAFORM_CORE_STRATEGY,
    TERRAFORM_MAX_CUT_CORE,
    TERRAFORM_MAX_FILL_CORE,
    WALL_RADIUS,
    WISH_TREE_PATH,
)
from ..layout import (
    build_cardinal_axes, build_central_plaza, decorate_blocks,
    enumerate_blocks, plaza_outer_radius, render_grid_streets,
)
from ..mc.codec import BlockCodec
from ..mc.placement import paste_volume, set_blocks_batch
from ..modular import build_modular_ring
from .block_placement import (
    _environment_style_for,
    place_block_buildings, placeholder_dims, placeholder_render,
)
from .commercial_street import build_commercial_streets
from ..narrative import assign_narrative_metadata
from ..narrative.books import place_story_books
from ..narrative.guild_decor import place_guild_decorations
from ..narrative.invasion import stage_invasion
from ..narrative.tableaux import stage_tableaux
from ..narrative.ruin import apply_ruin
from ..narrative.signs import place_door_signs
from ..narrative.streets import name_and_sign_streets
from ..roads import RoadPathfinder, SmartRoadSystem
from ..roads.astar_router import (
    build_ground_height, building_door_anchors,
    footprint_blocked_mask, network_to_blocks, plan_network,
)
from ..scan.coord_frame import ScanContext
from ..scan.height_map import generate_height_map
from .components import choose_core_component
from .greenery import scatter_greenery
from .reskin import make_tree_remap, make_wish_tree_remap
from .dimensions import compute_city_dims
from .foundation import prepare_city_floor
from .landmarks import place_landmarks
from .placement import footprint_xz, place_buildings_grid
from .planning import planning_profile_for
from .rings import grow_organic_rings
from .suitability import (
    compute_road_distance_map, compute_suitability_map,
    find_flat_slots_in_ring,
)
from .terraform import apply_terraform, terraform_for_building
from .terrain import TERRAIN_NAMES, build_terrain_map
from .trees import clear_trees_in_scan
from .wall import build_city_wall

TARGET_BUILDINGS = {"inner": 6,  "mid": 16, "outer": 28}
MIN_SPACING      = {"inner": 24, "mid": 18, "outer": 14}
GRID_STEP        = {"inner": 8,  "mid": 10, "outer": 12}

MODULAR_CONFIG = {"inner": 4, "mid": 12, "outer": 20}


def _refresh_height_map(scan_volume: np.ndarray, ctx: ScanContext,
                        codec: BlockCodec, into: np.ndarray):
    """清树后重算 height_map，原地写入 into。"""
    refreshed = generate_height_map(scan_volume, min_y=ctx.min_y, codec=codec)
    into[:, :] = refreshed


def _place_core(center_x: int, center_z: int,
                core_terrain: str,
                height_map: np.ndarray,
                suitability_map: np.ndarray,
                ctx: ScanContext,
                locked_rects: list,
                scan_volume: np.ndarray = None,
                codec: BlockCodec = None,
                terrain_map: np.ndarray = None,
                tree_terrain: str = None,
                use_wish_tree: bool = False) -> dict:
    """放置城市核心（灵魂树等）。

    新行为（用户决策：激进 terraform）：
        在 paste 前对灵魂树 footprint 调 terraform_for_building，
        max_cut/fill 用 TERRAFORM_MAX_CUT_CORE / TERRAFORM_MAX_FILL_CORE（15/15），
        target_strategy=TERRAFORM_CORE_STRATEGY（median，使 cut/fill 量大致对称）。
        成功 → base_y = terraform_result.base_y，paste 在这个高度。
        失败 → 用单点 height_map[scz, scx] 作 fallback（保留原行为，避免崩流程）。
    """
    # 小图用许愿树当核心（74×89 高39，小城放得下）；否则常规 soul_TREE。
    soul_path = WISH_TREE_PATH if use_wish_tree else choose_core_component(core_terrain)
    scx, scz = ctx.w2s(center_x, center_z)
    sx_tree, sz_tree = footprint_xz(soul_path, rotation_deg=0)

    sx0 = scx - sx_tree // 2
    sx1 = scx + (sx_tree - sx_tree // 2 - 1)
    sz0 = scz - sz_tree // 2
    sz1 = scz + (sz_tree - sz_tree // 2 - 1)
    locked_rects.append((sx0, sx1, sz0, sz1))

    H, W = suitability_map.shape
    cx0 = max(0, sx0 - 5); cx1 = min(W - 1, sx1 + 5)
    cz0 = max(0, sz0 - 5); cz1 = min(H - 1, sz1 + 5)
    suitability_map[cz0:cz1 + 1, cx0:cx1 + 1] = 0.0

    # ── 激进 terraform：把灵魂树 footprint 凿/垫成平台 ──
    base_y = int(height_map[scz, scx])  # fallback
    features = getattr(ctx, "terrain_features", None)
    can_terraform = (features is not None and scan_volume is not None
                     and codec is not None)
    if can_terraform:
        tr = terraform_for_building(
            footprint_xz=(sx0, sz0, sx1, sz1),
            height_map=height_map,
            features=features, ctx=ctx, terrain_map=terrain_map,
            max_cut=TERRAFORM_MAX_CUT_CORE,
            max_fill=TERRAFORM_MAX_FILL_CORE,
            target_strategy=TERRAFORM_CORE_STRATEGY,
        )
        if tr.success:
            ok = apply_terraform(tr, ctx, scan_volume=scan_volume,
                                 height_map=height_map, codec=codec)
            if ok:
                base_y = int(tr.base_y)
                print(f"   核心 terraform: base_y={base_y} cost={tr.cost} "
                      f"(strategy={TERRAFORM_CORE_STRATEGY})")
            else:
                print(f"   ⚠️ 核心 apply_terraform HTTP 失败，回退单点 base_y")
        else:
            print(f"   ⚠️ 核心 terraform 失败 ({tr.reason})，回退单点 base_y")
    else:
        print(f"   ⏭️ 核心跳过 terraform（features/scan_volume/codec 缺失），"
              f"用单点 base_y")

    origin = (int(center_x - sx_tree // 2), base_y, int(center_z - sz_tree // 2))
    print(f"   核心 origin={origin}, npy={soul_path}")
    # 灵魂树按地形换材质（雪→云杉冻树 / badlands→枯树 / water→深橡木青冠…）；
    # tree_terrain 给定时（主导地形）优先用它，否则回退中心地形；plains 返回 None 保留粉樱花。
    reskin_terrain = tree_terrain or core_terrain
    kind = "许愿树" if use_wish_tree else "灵魂树"
    print(f"   {kind}材质地形: {reskin_terrain}")
    remap = (make_wish_tree_remap(reskin_terrain) if use_wish_tree
             else make_tree_remap(reskin_terrain))
    paste_volume(soul_path, origin=origin, clear_target=False, rotation=0,
                 block_remap=remap)
    return {"path": soul_path, "origin": origin, "rotation": 0}


def _landmark_specs_for_size(small_city: bool, dims,
                             scan_radius_limit: int = None):
    """Return landmark specs for the current map size.

    Small maps get exactly one Eye King via SMALL_CITY_LANDMARK_SPECS.
    Very large GDMC maps add a second outer Eye King ring so the invasion
    ward reads at 1000x1000 scale.
    """
    if small_city:
        return SMALL_CITY_LANDMARK_SPECS

    def clamp_radius(radius: int, clearance: int) -> int:
        if scan_radius_limit is None:
            return int(radius)
        return int(min(radius, max(0, int(scan_radius_limit) - int(clearance))))

    if (not SUPER_LARGE_EYE_KINGS_ENABLED
            or dims.wall_radius < SUPER_LARGE_EYE_KINGS_MIN_WALL_R):
        specs = list(LANDMARK_SPECS)
    else:
        outer_limit = int(min(dims.wall_radius - 70, dims.outer_end_r - 80))
        specs = [s for s in LANDMARK_SPECS
                 if s.get("file") != "eye_king.npy"]
        for idx, angle in enumerate(SUPER_LARGE_EYE_KING_ANGLES):
            frac = SUPER_LARGE_EYE_KING_RADIUS_FRAC - (0.10 if idx % 2 else 0.0)
            radius = clamp_radius(int(min(outer_limit, dims.wall_radius * frac)),
                                  clearance=46)
            if radius > 0:
                specs.append({"file": "eye_king.npy", "angle": angle,
                              "radius": radius,
                              "monster_statue": True, "floating": True})
    if DARK_COLOSSUS_ENABLED and dims.wall_radius >= DARK_COLOSSUS_MIN_WALL_R:
        colossus_r = int(min(
            dims.wall_radius - DARK_COLOSSUS_WALL_CLEARANCE,
            dims.outer_end_r - DARK_COLOSSUS_OUTER_CLEARANCE,
            dims.wall_radius * DARK_COLOSSUS_RADIUS_FRAC,
        ))
        colossus_r = clamp_radius(colossus_r, clearance=82)
        if colossus_r > 0:
            insert_at = sum(1 for s in specs if s.get("file") != "eye_king.npy")
            angles = tuple(DARK_COLOSSUS_ANGLES) or (DARK_COLOSSUS_ANGLE,)
            for angle in angles[:max(1, int(DARK_COLOSSUS_MAX_COUNT))]:
                specs.insert(insert_at, {
                    "file": "dark_colossus.npy",
                    "angle": angle,
                    "radius": colossus_r,
                    "monster_statue": True,
                    "giant_statue": True,
                    "leg_debris": True,
                })
                insert_at += 1
    if specs == list(LANDMARK_SPECS):
        return None
    return specs


def _place_rings(center_x: int, center_z: int,
                 height_map: np.ndarray,
                 height_map_original: np.ndarray,
                 suitability_map: np.ndarray,
                 road_score_map: np.ndarray,
                 terrain_map: np.ndarray,
                 scan_volume_for_buildings: np.ndarray,
                 placed_origins: list,
                 placed_boxes: list,
                 locked_rects: list,
                 ctx: ScanContext,
                 codec: BlockCodec,
                 ring_masks=None) -> dict:
    """三圈层建筑放置。返回 {ring: [info, ...]}。

    卡 3：传入 ring_masks（RingMasks）时按有机 mask 选址；否则 RADIUS_MAP 兜底。
    """
    used = {"inner": [], "mid": [], "outer": []}
    H, W = suitability_map.shape

    for ring_name in ("inner", "mid", "outer"):
        r_min, r_max = RADIUS_MAP[ring_name]
        target = TARGET_BUILDINGS[ring_name]
        ring_mask = getattr(ring_masks, ring_name, None) if ring_masks else None
        mode = "organic" if ring_mask is not None else "circular"
        print(f"\n  --- {ring_name.upper()} 圈 (r={r_min}~{r_max}, 目标{target}栋, "
              f"{mode}) ---")

        # inner 圈用低阈值（激进 terraform 后大部分坡都能站房子）
        if ring_name == "inner":
            first_min_suit = INNER_MIN_SUITABILITY
            retry_min_suit = INNER_RETRY_MIN_SUITABILITY
        else:
            first_min_suit = 0.45
            retry_min_suit = 0.2

        # 诊断：mask 与 suitability 的交集分布，定位"候选少"的根因
        if ring_mask is not None:
            mask_cells = int(ring_mask.sum())
            suit_in_mask = suitability_map[ring_mask]
            buildable_first = int((suit_in_mask >= first_min_suit).sum())
            buildable_retry = int((suit_in_mask >= retry_min_suit).sum())
            print(f"  📊 mask={mask_cells} 格 / suit≥{first_min_suit}={buildable_first} "
                  f"/ suit≥{retry_min_suit}={buildable_retry}")

        candidates = find_flat_slots_in_ring(
            suitability_map=suitability_map,
            ctx=ctx, center_x=center_x, center_z=center_z,
            r_min=r_min, r_max=r_max,
            grid_step=GRID_STEP[ring_name],
            min_suitability=first_min_suit, top_k=300,
            road_score_map=road_score_map, road_weight=2.0,
            ring_mask=ring_mask,
        )
        print(f"  候选点数量: {len(candidates)}")

        if not candidates:
            print(f"  ⚠️ {ring_name} 圈没找到候选点，降低阈值重试...")
            candidates = find_flat_slots_in_ring(
                suitability_map=suitability_map,
                ctx=ctx, center_x=center_x, center_z=center_z,
                r_min=r_min, r_max=r_max,
                grid_step=GRID_STEP[ring_name],
                min_suitability=retry_min_suit, top_k=300,
                road_score_map=road_score_map, road_weight=1.0,
                ring_mask=ring_mask,
            )
            print(f"  降低阈值后候选点数量: {len(candidates)}")

        newly_placed = place_buildings_grid(
            candidates=candidates,
            target_count=target,
            ring_name=ring_name,
            height_map=height_map,
            scan_volume=scan_volume_for_buildings,
            terrain_map=terrain_map,
            placed_origins=placed_origins,
            placed_boxes=placed_boxes,
            locked_rects=locked_rects,
            ctx=ctx, center_x=center_x, center_z=center_z,
            codec=codec,
            min_spacing=MIN_SPACING[ring_name],
            height_map_original=height_map_original,
        )
        used[ring_name] = newly_placed

        # 已放建筑区域在 suitability_map 上清零
        for info in newly_placed:
            ox, _, oz = info["origin"]
            s_ox, s_oz = ctx.w2s(ox, oz)
            sp_x, sp_z = footprint_xz(info["path"], info["rotation"])
            bx0 = max(0, s_ox - 2)
            bx1 = min(W - 1, s_ox + sp_x + 2)
            bz0 = max(0, s_oz - 2)
            bz1 = min(H - 1, s_oz + sp_z + 2)
            suitability_map[bz0:bz1 + 1, bx0:bx1 + 1] = 0.0

    print("\n🎉 三圈层建筑放置完成!")
    for k, v in used.items():
        print(f"   {k}: {len(v)} 栋")
    return used


def build_city(center_x: int, center_z: int,
               height_map: np.ndarray,
               scan_volume: np.ndarray,
               ctx: ScanContext,
               codec: BlockCodec = None) -> dict:
    """主入口。返回 {ring: [placed_info, ...]} 已放置建筑摘要。"""
    if codec is None:
        codec = BlockCodec()

    # 卡 10.2：派生半径快照。main.py 选址前通常已挂 ctx.city_dims；
    # 直接调 build_city（如隔离测试）时这里兜底补算，保证下游不拿到 None。
    if getattr(ctx, "city_dims", None) is None:
        NZ, NX = height_map.shape
        ctx.city_dims = compute_city_dims(NX, NZ)

    # 卡 10.3：把派生半径接入建造环节。ADAPTIVE_SIZE_ENABLED=False 时
    # dims.wall_radius == config.WALL_RADIUS（_fixed_dims），行为与改前一致。
    dims = ctx.city_dims
    visual_dims = getattr(ctx, "visual_city_dims", dims)
    wall_radius = dims.wall_radius

    locked_rects: list = []
    placed_origins: list = []
    placed_boxes: list = []

    # ── [0] 全局伐树 ────────────────────────────────────────────────
    print("🪓 [0/10] 清除树木（全范围）...")
    scan_volume_for_buildings = scan_volume.copy()
    scan_volume_world = scan_volume

    cleared = clear_trees_in_scan(
        scan_volume_for_buildings, height_map, ctx,
        max_height_above_ground=70, codec=codec, mutate_scan=True,
    )
    clear_trees_in_scan(
        scan_volume_world, height_map, ctx,
        max_height_above_ground=70, codec=codec, mutate_scan=True,
    )
    print(f"   ✅ 树木清除完成，共清除 {cleared} 个方块")

    # ── [1] 地形分析 ────────────────────────────────────────────────
    print("🗺️  [1/10] 分析地形...")
    terrain_map = build_terrain_map(scan_volume_for_buildings, codec=codec)

    print("   清树后重算 height_map...")
    _refresh_height_map(scan_volume_for_buildings, ctx, codec, into=height_map)
    print("   ✅ height_map 已刷新")

    # 双份 height_map：original 永远是原始地面，working 接收 prepare_foundation 改动
    height_map_original = height_map.copy()
    height_map_working = height_map  # 与 height_map 共享内存

    print("   计算 suitability_map...")
    max_radius = max(r_max for _, r_max in RADIUS_MAP.values())
    suitability_map = compute_suitability_map(
        height_map=height_map, terrain_map=terrain_map, ctx=ctx,
        center_x=center_x, center_z=center_z,
        max_radius=max_radius, max_slope=0.35,
    )
    suitable_ratio = float(np.mean(suitability_map > 0.45))
    print(f"   ✅ suitability_map 完成，适宜建造比例={suitable_ratio:.1%}")

    # 卡 3：有机圈层。需要 ctx.terrain_features（main.py 在 build_city 前注入）。
    # 失败或缺失时 ring_masks=None，下游 placement/modular 自动回退到 RADIUS_MAP 圆形判定。
    ring_masks = None
    features = getattr(ctx, "terrain_features", None)
    if features is None:
        print("   ⚠️ ctx.terrain_features 未设置，跳过 grow_organic_rings，"
              "圈层回退圆形")
    else:
        try:
            ring_masks = grow_organic_rings(ctx, features, (center_x, center_z))
            ctx.ring_masks = ring_masks
        except Exception as exc:
            print(f"   ⚠️ grow_organic_rings 失败：{exc!r}，圈层回退圆形")
            ring_masks = None

    scx, scz = ctx.w2s(center_x, center_z)
    core_code = int(terrain_map[scz, scx])
    core_terrain = TERRAIN_NAMES[core_code] if core_code < len(TERRAIN_NAMES) else "plains"
    print(f"   城市中心地形: {core_terrain}")

    # 灵魂树材质用「全图主导地形」（水多地少图上中心常落平原岛 → 否则树永不变材质）。
    tree_terrain = core_terrain
    if SOUL_TREE_DOMINANT_TERRAIN:
        counts = np.bincount(terrain_map.ravel(), minlength=len(TERRAIN_NAMES))
        dom_code = int(counts.argmax())
        tree_terrain = (TERRAIN_NAMES[dom_code]
                        if dom_code < len(TERRAIN_NAMES) else core_terrain)
        print(f"   全图主导地形(灵魂树材质): {tree_terrain} "
              f"(counts={counts.tolist()})")
    city_env_style = _environment_style_for(
        terrain_map, scan_volume_for_buildings, codec, scx, scz)
    planning_profile = planning_profile_for(city_env_style)
    print("   城市规划 profile: "
          f"{planning_profile.style} "
          f"block={planning_profile.block_size} "
          f"road={planning_profile.next_road_width} "
          f"padding={planning_profile.building_padding} "
          f"large>{planning_profile.large_threshold} "
          f"max_large={planning_profile.max_large}")

    # ── [2] 骨架道路 ────────────────────────────────────────────────
    print("🛣️  [2/10] 生成骨架道路...")
    road_system = SmartRoadSystem(
        max_slope=0.5,
        road_block="minecraft:cobblestone",
        road_width=3,
        use_astar=True,
        building_buffer=2,
        avoid_buildings=True,
    )
    backbone_nodes = road_system.generate_structural_roads(
        center_x=center_x, center_z=center_z,
        radius_map=RADIUS_MAP,
        height_map=height_map_working,
        origin_x=ctx.origin_x, origin_z=ctx.origin_z,
        radial_count=8,
        height_map_original=height_map_original,
        features=features,
        ring_masks=ring_masks,
        ctx=ctx,
        render_radials=not GRID_STREETS_ENABLED,   # 卡 9.6：网格街道取代放射道
        render_rings=not GRID_STREETS_ENABLED,     # 环城路延后到 [9]（建筑后，遇楼断）
    )

    print("   计算道路距离评分图...")
    road_score_map = compute_road_distance_map(
        backbone_nodes=backbone_nodes,
        height_map=height_map, ctx=ctx, max_dist=25.0,
    )

    # ── [3] 城市底板（水域填充） ────────────────────────────────────
    print("🏗️  [3/10] 城市底板：填补水域和空洞...")
    filled_cells = prepare_city_floor(
        center_x=center_x, center_z=center_z,
        wall_radius=wall_radius,
        height_map=height_map, terrain_map=terrain_map,
        scan_volume=scan_volume_world,
        ctx=ctx, codec=codec,
    )

    # 填水后把这些格子标记为可建陆地（is_water=False）。否则 enumerate_blocks 仍按
    # 旧 is_water 判 blocked → 填好的海岸/沼泽地全空（GDMC 自适应核心）。
    # height_map/valid 在水柱处本就指向水面（=填后地表），无需改，只需翻 is_water。
    if WATER_REFRESH_AFTER_FLOOR and features is not None and filled_cells:
        for xs, zs in filled_cells:
            features.is_water[zs, xs] = False
        print(f"   ✅ 填水后标记 {len(filled_cells)} 格为可建陆地")

    # ── [4] 城市核心 ────────────────────────────────────────────────
    print("🏛️  [4/10] 放置城市核心...")
    # 小图（R=min(build)//2 ≤ 阈值）触发小图适配：核心用许愿树 + 窄矮城墙。
    city_R = min(height_map.shape) // 2
    small_city = city_R <= SMALL_CITY_R_THRESHOLD
    use_wish_tree = SMALL_CITY_CORE_WISH_TREE and small_city
    if small_city:
        print(f"   小图 R={city_R} ≤ {SMALL_CITY_R_THRESHOLD} → 小图适配"
              f"（许愿树核心 + 窄矮城墙）")
    core_info = _place_core(center_x, center_z, core_terrain,
                            height_map, suitability_map, ctx, locked_rects,
                            scan_volume=scan_volume_world, codec=codec,
                            terrain_map=terrain_map, tree_terrain=tree_terrain,
                            use_wish_tree=use_wish_tree)

    # ── [5] 建筑放置：grid 流程（Priority 2 卡 9.5）或旧散点+模块化 ──
    features_for_grid = getattr(ctx, "terrain_features", None)
    use_grid = GRID_LAYOUT_ENABLED and features_for_grid is not None
    if GRID_LAYOUT_ENABLED and features_for_grid is None:
        print("   ⚠️ GRID_LAYOUT_ENABLED 但缺 terrain_features，回退旧 placement")
    # 主道几何/参数 hoist 到外层：DEFER_CARDINAL_ROADS 时 [4.5] 算、[9] 建筑后才铺。
    plaza_r = None
    base_y_core = None

    if use_grid:
        # [4.5] 中心广场环 + 主道几何（半径按真实树 footprint 推）。
        # 主道铺块按 DEFER_CARDINAL_ROADS 决定：开→延后 [9]，关→这里铺。
        _card_when = "几何，铺块延后 [9]" if DEFER_CARDINAL_ROADS else "并铺块"
        print(f"🟦 [4.5] 中心广场环 + 4 cardinal 主道（{_card_when}）...")
        base_y_core = int(core_info["origin"][1])
        tx, tz = footprint_xz(core_info["path"], core_info["rotation"])
        half_x, half_z = tx // 2, tz // 2
        plaza_r = plaza_outer_radius(half_x, half_z)
        build_central_plaza(center_x, center_z, base_y_core, height_map, ctx,
                            codec, tree_half_x=half_x, tree_half_z=half_z)
        # DEFER_CARDINAL_ROADS：主道铺块延后到 [9]（建筑后、遇楼断开）。这里只算几何。
        if not DEFER_CARDINAL_ROADS:
            build_cardinal_axes(center_x, center_z, base_y_core, height_map, ctx,
                                codec, plaza_outer=plaza_r)

        # [4.7] 大地标（网格放不下的标志性建筑）——先占 footprint，
        # 网格街区/城墙/道路据 placed_boxes 自动避让。
        if LANDMARK_ENABLED:
            print("🏯 [4.7] 大地标摆放（公会大院/塔/桥/废墟）...")
            # 许愿树当核心时，从地标池排除它，避免重复放置。
            _lm_exclude = {os.path.basename(WISH_TREE_PATH)} if use_wish_tree else None
            scan_radius_limit = max(0, min(height_map.shape) // 2 - 8)
            landmark_specs = _landmark_specs_for_size(
                small_city, visual_dims, scan_radius_limit=scan_radius_limit)
            landmark_wall_radius = max(dims.wall_radius, scan_radius_limit)
            # 小图用专用小地标集 + 小 keepout；wall/outer 一律传 city_dims（接入自适应）。
            place_landmarks(
                center_x, center_z, ctx, codec, height_map, terrain_map,
                scan_volume_world, placed_origins, placed_boxes, locked_rects,
                exclude_files=_lm_exclude,
                specs_override=landmark_specs,
                core_keepout=SMALL_CITY_LANDMARK_KEEPOUT if small_city else None,
                wall_radius=landmark_wall_radius,
                outer_end_r=max(dims.outer_end_r, scan_radius_limit))

        # [4.8] 商业街——沿 4 cardinal 主道两侧密铺小店（卡 12.1，新增一步，不改原顺序）。
        # 先占 footprint，[5'] 网格 / [8] 城墙 / [9] 街道据 placed_boxes 自动避让。
        if COMMERCIAL_STREET_ENABLED:
            print("🏪 [4.8] 商业街沿主道密铺...")
            build_commercial_streets(
                center_x, center_z, plaza_r, ctx, codec, height_map,
                terrain_map, scan_volume_world, placed_origins, placed_boxes,
                locked_rects, wall_radius=dims.wall_radius)

        # [5'] grid 街区驱动建筑——替换散点[5]+模块化[6]
        # 卡 9.6：GRID_USE_NPY_BUILDINGS=True 走真实 npy（按公会路由），
        # 否则占位色块盒子。
        npy_mode = GRID_USE_NPY_BUILDINGS
        label = "真实 npy，按公会路由" if npy_mode else "占位色块，4 公会均衡"
        print(f"🏘️  [5'/10] grid 街区建筑（{label}）...")
        # 卡 10.3（第2步）：grid 建筑/街道范围接 city_dims，避免建筑跨墙（墙断）。
        # 小图 outer_end_r=墙半径 → 无墙外郊区、街区只在墙内，墙体完整 + 房量更少。
        grid_mid_start = dims.mid_start_r
        grid_wall = dims.wall_radius
        grid_outer = dims.wall_radius if small_city else dims.outer_end_r
        regions = enumerate_blocks(
            center_x, center_z, ctx, None, features_for_grid,
            wall_radius=grid_wall,
            mid_start_r=grid_mid_start,
            outer_end_r=grid_outer,
            block_size=planning_profile.block_size,
            next_road_width=planning_profile.next_road_width,
        )
        used = place_block_buildings(
            regions, ctx, codec, height_map, terrain_map, scan_volume_world,
            placed_origins, placed_boxes, center_x, center_z,
            locked_rects=locked_rects,
            box_padding=planning_profile.building_padding,
            block_size=planning_profile.block_size,
            next_road_width=planning_profile.next_road_width,
            large_threshold=planning_profile.large_threshold,
            # 小图：max_large=0 → 配合 GRID_PREFER_SMALL 只挑小楼（大楼放不下小城）。
            max_large=0 if small_city else planning_profile.max_large,
            dims_fn=None if npy_mode else placeholder_dims,
            render_fn=None if npy_mode else placeholder_render,
        )
        n_grid = sum(len(v) for v in used.values())
        print(f"   grid 放置 {n_grid} 栋")
        if BLOCK_DECOR_ENABLED:
            print("🎴 [5'] 主殿前广场装饰...")
            decorate_blocks(used, ctx, height_map, center_x, center_z)
    else:
        # ── [5] 三圈层建筑（旧散点）────────────────────────────────
        print("🏘️  [5/10] 三圈层建筑放置...")
        used = _place_rings(
            center_x=center_x, center_z=center_z,
            height_map=height_map,
            height_map_original=height_map_original,
            suitability_map=suitability_map,
            road_score_map=road_score_map,
            terrain_map=terrain_map,
            scan_volume_for_buildings=scan_volume_for_buildings,
            placed_origins=placed_origins,
            placed_boxes=placed_boxes,
            locked_rects=locked_rects,
            ctx=ctx, codec=codec,
            ring_masks=ring_masks,
        )

        # ── [6] 模块化建筑 ────────────────────────────────────────
        print("\n🏘️  [6/10] 模块化建筑（住宅/商铺）...")
        for ring_name, max_b in MODULAR_CONFIG.items():
            r_min, r_max = RADIUS_MAP[ring_name]
            ring_mask = getattr(ring_masks, ring_name, None) if ring_masks else None
            build_modular_ring(
                ring_name=ring_name, r_min=r_min, r_max=r_max,
                suitability_map=suitability_map,
                height_map=height_map, terrain_map=terrain_map,
                ctx=ctx, center_x=center_x, center_z=center_z,
                max_buildings=max_b,
                scan_volume=scan_volume_world, codec=codec,  # 卡 5：terraform 同步内存用
                ring_mask=ring_mask,  # 卡 3：有机圈层
            )

    # ── [7] 再次城市底板（清理新增水域） ────────────────────────────
    print("🏗️  [7/10] 城市底板：处理新增水域...")
    prepare_city_floor(
        center_x=center_x, center_z=center_z,
        wall_radius=wall_radius,
        height_map=height_map, terrain_map=terrain_map,
        scan_volume=scan_volume_world,
        ctx=ctx, codec=codec,
    )

    # ── [8] 城墙 ────────────────────────────────────────────────────
    print("🏰 [8/10] 生成城墙...")
    build_city_wall(
        center_x, center_z, wall_radius,
        height_map_original, ctx,
        placed_boxes=placed_boxes,
        terrain_map=terrain_map,
        scan_volume=scan_volume_world,
        codec=codec,
        wall_height=SMALL_CITY_WALL_HEIGHT if small_city else 5,
        gate_interval=90, gate_width=7,
        tower_interval=45,
        width=SMALL_CITY_WALL_WIDTH if small_city else None,
        flat_top=SMALL_CITY_WALL_FLAT_TOP if small_city else None,
    )

    # ── [9] 道路接入 ────────────────────────────────────────────────
    road_cells = None                                  # 绿化避让用：道路 scan 格集合
    if ROAD_SYSTEM == "astar":
        # 卡 15.1：A* 路网取代笔直主道+削填架桥（用户图 101/213 炸路根治）。
        # 高度图加权 A*：陡边不可通行 → 路绕缓坡、走不通就不修，drape 贴地铺。
        print("🛣️  [9/10] A* 路网（建筑门/城门/广场环，贴地无桥）...")
        gh = build_ground_height(scan_volume_world, codec, ctx.min_y)
        ccx, ccz = ctx.w2s(center_x, center_z)
        NZ_, NX_ = gh.shape
        # 灵魂树/广场圈设为不可通行 + 城心锚点改成广场环 8 点 → 路不穿树底座（图 334）。
        core_r = int(plaza_r) if plaza_r else 40
        blocked = footprint_blocked_mask(placed_boxes, ctx, gh.shape)
        _zz, _xx = np.ogrid[:NZ_, :NX_]
        core_disk = (_zz - ccz) ** 2 + (_xx - ccx) ** 2 <= core_r ** 2
        passable = (gh > ctx.min_y) & (~blocked) & (~core_disk)
        anchors = []
        _ring8 = ((1, 0), (0, 1), (-1, 0), (0, -1),
                  (0.7071, 0.7071), (0.7071, -0.7071),
                  (-0.7071, 0.7071), (-0.7071, -0.7071))
        for ux, uz in _ring8:                              # 广场外缘环锚点
            ax, az = center_x + (core_r + 3) * ux, center_z + (core_r + 3) * uz
            sx, sz = ctx.w2s(int(ax), int(az))
            if 0 <= sz < NZ_ and 0 <= sx < NX_ and passable[sz, sx]:
                anchors.append((sz, sx))
        anchors += building_door_anchors(
            placed_boxes, center_x, center_z, ctx, gh, ctx.min_y,
            door_offset=ROAD_ASTAR_DOOR_OFFSET)
        if ROAD_ASTAR_GATES:                               # 4 城门（城心±墙半径）
            for gx, gz in ((center_x + wall_radius, center_z),
                           (center_x - wall_radius, center_z),
                           (center_x, center_z + wall_radius),
                           (center_x, center_z - wall_radius)):
                sx, sz = ctx.w2s(int(gx), int(gz))
                if 0 <= sz < NZ_ and 0 <= sx < NX_:
                    anchors.append((sz, sx))
        bound = wall_radius + 8
        paths, unreach = plan_network(
            gh, anchors, passable, ctx.min_y,
            hard_step=ROAD_ASTAR_MAX_STEP, step_penalty=ROAD_ASTAR_STEP_PENALTY,
            bounds=(ccz - bound, ccz + bound, ccx - bound, ccx + bound))
        road_blocks = network_to_blocks(
            paths, gh, ctx, min_y=ctx.min_y, road_width=ROAD_ASTAR_WIDTH,
            surface=ROAD_ASTAR_SURFACE, stairs=ROAD_ASTAR_STAIRS,
            clearance=ROAD_ASTAR_CLEARANCE)
        for bi in range(0, len(road_blocks), 4096):
            set_blocks_batch(road_blocks[bi:bi + 4096])
        road_cells = {(int(b["z"]) - ctx.origin_z, int(b["x"]) - ctx.origin_x)
                      for b in road_blocks}            # 绿化避让用
        print(f"   A* 路网：{len(paths)} 条路 / 走不通 {len(unreach)} 锚点 / "
              f"{len(road_blocks)} 块（锚点 {len(anchors)}）")
    elif GRID_STREETS_ENABLED:
        # 卡 9.6：建筑之后渲染道路 → 不被建筑覆盖；blocked_boxes 让路遇楼断开。
        # 环城路（[2] 已延后）+ 网格街道（中式棋盘）一起在这渲染。
        print("🛣️  [9/10] 环城路 + 网格街道（建筑后渲染，遇楼断开）...")
        # DEFER_CARDINAL_ROADS：4 条主道在此（建筑后）铺，遇楼断开 → 不被覆盖。
        if DEFER_CARDINAL_ROADS and use_grid and plaza_r is not None:
            n_card = build_cardinal_axes(
                center_x, center_z, base_y_core, height_map_original, ctx,
                codec, plaza_outer=plaza_r, blocked_boxes=placed_boxes,
                scan_volume=scan_volume_world)
            print(f"   4 cardinal 主道（建筑后渲染）{n_card}/4 条")
        road_system.render_ring_roads(
            center_x, center_z, RADIUS_MAP, height_map_original,
            origin_x=ctx.origin_x, origin_z=ctx.origin_z, min_y=ctx.min_y,
            blocked_boxes=placed_boxes)
        n_streets = render_grid_streets(
            center_x, center_z, ctx, features, height_map_original,
            block_size=planning_profile.block_size,
            next_road_width=planning_profile.next_road_width,
            mid_start_r=dims.mid_start_r,
            outer_end_r=dims.wall_radius if small_city else dims.outer_end_r,
            wall_radius=dims.wall_radius,
            building_boxes=placed_boxes,
            scan_volume=scan_volume_world, codec=codec)
        print(f"   网格街道渲染 {n_streets} 段")
    else:
        print("🛣️  [9/10] 建筑接入道路...")
        road_system.pathfinder = RoadPathfinder(
            height_map_original, scan_volume_world,
            max_slope=0.5,
            origin_x=ctx.origin_x, origin_z=ctx.origin_z, min_y=ctx.min_y,
        )
        road_system.connect_buildings_to_nearest_road(
            building_positions=placed_origins,
            height_map=height_map_working,
            origin_x=ctx.origin_x, origin_z=ctx.origin_z,
            center_x=center_x, center_z=center_z,
            radius_map=RADIUS_MAP,
            height_map_original=height_map_original,
        )

    # ── [9.7] 城市绿化（卡 16.1，新增一步，不改原顺序） ─────────────
    tree_boxes: list = []                        # 绿化种的树 box，供废墟阶段烧毁
    if GREENERY_ENABLED:
        print("🌳 [9.7] 城市绿化：空地填空 + 路边散种树木...")
        n_before_trees = len(placed_boxes)
        try:
            scatter_greenery(
                center_x, center_z, ctx, codec,
                height_map, terrain_map, features,
                placed_boxes, road_cells=road_cells,
                wall_radius=wall_radius,
                plaza_r=int(plaza_r) if plaza_r else 40,
            )
        except Exception as exc:
            print(f"   ⚠️ scatter_greenery 异常：{exc!r}")
        tree_boxes = placed_boxes[n_before_trees:]     # 新增的都是树 box

    # ── [10] 叙事图层（任务 1.5 完整接入） ─────────────────────────
    # 顺序（spec 1.5 OUTPUT）：
    #   1) assign_narrative_metadata
    #   2) place_door_signs       (任务 1.3)
    #   3) place_story_books      (任务 1.4)
    #   4) name_and_sign_streets  (任务 1.5)
    # 每步独立失败不影响其它（spec 1.5 CONSTRAINTS：单个路牌失败不影响其他）。
    # 模块化建筑暂未由 build_modular_ring 返回，metadata 仍只覆盖三圈层 used。
    if NARRATIVE_ENABLED:
        print("📜 [10/10] 叙事图层：建筑告示牌 + 故事书 + 路牌...")
        metas = assign_narrative_metadata(used, [], center_x, center_z)
        # 用 height_map (working) 而不是 height_map_original：terraform 已经把
        # 建筑 footprint 内的 height_map 抬到 base_y。挂告示牌/讲台/装饰用 original
        # 会读到 terraform 之前的低地面 → 漂浮在新建筑地面上方。
        # height_map (working) 包含 terraform 改动，对 footprint 内的格给出正确高度。
        narrative_hm = height_map
        try:
            n_door = place_door_signs(
                metas, narrative_hm, ctx,
                center_x=center_x, center_z=center_z, codec=codec,
            )
            print(f"   ✅ 门口告示牌 {n_door}/{len(metas)} 块")
        except Exception as exc:
            print(f"   ⚠️ place_door_signs 异常：{exc!r}")
        try:
            n_books = place_story_books(
                core_info["origin"], narrative_hm, ctx,
                center_x=center_x, center_z=center_z,
            )
            print(f"   ✅ 灵魂树故事书 {n_books}/6 个讲台")
        except Exception as exc:
            print(f"   ⚠️ place_story_books 异常：{exc!r}")
        try:
            n_streets = name_and_sign_streets(
                center_x, center_z, narrative_hm, ctx, codec=codec,
            )
            print(f"   ✅ 街道路牌 {n_streets}/16 块")
        except Exception as exc:
            print(f"   ⚠️ name_and_sign_streets 异常：{exc!r}")
        try:
            # 新卡 8 D：公会装饰图层。每栋有 guild 的建筑朝外偏 1 格放公会标志。
            n_decor = place_guild_decorations(
                metas, narrative_hm, ctx,
                center_x=center_x, center_z=center_z, codec=codec,
            )
            print(f"   ✅ 公会装饰 {n_decor}/{len(metas)} 块")
        except Exception as exc:
            print(f"   ⚠️ place_guild_decorations 异常：{exc!r}")
    else:
        print("⏭️  [10/10] 叙事图层已禁用 (NARRATIVE_ENABLED=False)")

    # ── [10.6] 环境叙事实景（剧情演绎，末尾 append，非 reorder；静态可提交）──────
    if NARRATIVE_TABLEAUX_ENABLED:
        print("📜 [10.6] 环境叙事实景：3 幕冻结场景（立约/围城/断根）...")
        try:
            stage_tableaux(center_x, center_z, height_map, ctx, core_info)
        except Exception as exc:
            print(f"   ⚠️ stage_tableaux 异常：{exc!r}")

    # ── [10.7] 环境废墟叙事：把城市打成被入侵毁灭的废墟（纯环境，一眼可见）──────
    if RUIN_ENABLED:
        print("🔥 [10.7] 环境废墟：灵魂树断根燃烧 + 城墙豁口 + 焦土余烬...")
        try:
            apply_ruin(center_x, center_z, height_map, ctx, core_info,
                       wall_radius, int(plaza_r) if plaza_r else 40,
                       placed_boxes=placed_boxes, tree_boxes=tree_boxes)
        except Exception as exc:
            print(f"   ⚠️ apply_ruin 异常：{exc!r}")

    # ── [11] 活入侵彩蛋（末尾 append，非 reorder；默认关，不进 GDMC 提交）──────
    # 埋命令方块+计分板机关：解冻后玩家接近城心 → 聊天弹菜单 → 点击触发突袭。
    if INVASION_RAID_ENABLED:
        print("⚔️ [11] 埋活入侵彩蛋机关（解冻后接近城心触发菜单）...")
        try:
            stage_invasion(center_x, center_z, height_map, ctx,
                           placed_origins, core_info)
        except Exception as exc:
            print(f"   ⚠️ stage_invasion 异常：{exc!r}")

    print("\n✅ 城市生成完成!")
    return used
