"""建筑放置：几何工具、占用检测、圈层放置主流程。"""
from __future__ import annotations

import math
import random

import numpy as np

from ..config import (
    SEA_CITY_ENABLED,
    TERRAFORM_MAX_CUT,
    TERRAFORM_MAX_CUT_INNER,
    TERRAFORM_MAX_FILL,
    TERRAFORM_MAX_FILL_INNER,
)
from ..mc.blocks import (
    AIR_BLOCK, PASSTHROUGH_BLOCKS, get_block_id, is_natural_terrain_block,
)
from ..mc.codec import BlockCodec
from ..mc.placement import paste_volume
from ..scan.coord_frame import ScanContext
from .components import list_region_files_fallback, list_region_files_for_terrain
from .foundation import build_stilt_foundation
from .suitability import compute_footprint_complexity
from .terraform import apply_terraform, terraform_for_building
from .terrain import TERRAIN_NAMES
from .trees import clear_footprint_vegetation

# 按圈层选 terraform 上限：三圈都激进 = (10, 10)。
# 默认 (3, 3) 在山地上拦掉 60%+ 的 mid/outer 候选（playtest 验证：
# mid too_steep_cut max 见到 41/35/38/37 等），(10, 10) 救回大部分。
# inner / mid / outer 当前用同一阈值；未来若想精调可分开。
_TERRAFORM_LIMITS_BY_RING = {
    "inner": (TERRAFORM_MAX_CUT_INNER, TERRAFORM_MAX_FILL_INNER),
    "mid":   (TERRAFORM_MAX_CUT_INNER, TERRAFORM_MAX_FILL_INNER),
    "outer": (TERRAFORM_MAX_CUT_INNER, TERRAFORM_MAX_FILL_INNER),
}

_DIMS_CACHE: dict[str, tuple[int, int]] = {}


def footprint_xz(npy_path: str, rotation_deg: int) -> tuple[int, int]:
    """从 .npy 读取建筑 XZ 占地尺寸（考虑 90/270 度旋转交换 X/Z）。带缓存。"""
    if npy_path in _DIMS_CACHE:
        nx, nz = _DIMS_CACHE[npy_path]
    else:
        vol = np.load(npy_path, allow_pickle=True)
        _ny, nz, nx = vol.shape  # (Y, Z, X)
        _DIMS_CACHE[npy_path] = (nx, nz)

    sx, sz = nx, nz
    if rotation_deg % 180 == 90:
        sx, sz = sz, sx
    return int(sx), int(sz)


def make_box_from_center(cx: int, cz: int, sx: int, sz: int,
                         padding: int = 0) -> tuple[int, int, int, int]:
    """中心 + 尺寸 → 包围盒 (min_x, max_x, min_z, max_z)，闭区间。"""
    half_x = sx // 2
    half_z = sz // 2
    return (cx - half_x - padding,
            cx + (sx - half_x - 1) + padding,
            cz - half_z - padding,
            cz + (sz - half_z - 1) + padding)


def boxes_intersect(a, b) -> bool:
    """两个 (min_x, max_x, min_z, max_z) 是否相交。"""
    return not (a[1] < b[0] or a[0] > b[1] or a[3] < b[2] or a[2] > b[3])


def compute_facing_rotation(center_x: int, center_z: int,
                            x: int, z: int) -> int:
    """根据建筑相对城市中心的方位算出 0/90/180/270 旋转。"""
    dx = center_x - x
    dz = center_z - z
    angle_deg = math.degrees(math.atan2(dx, dz)) % 360
    return int(round(angle_deg / 90) * 90 % 360)


def is_location_occupied(scan_volume: np.ndarray,
                         world_x: int, world_z: int,
                         ctx: ScanContext,
                         padding: int = 3,
                         y_above_ground_min: int = 1,
                         y_above_ground_max: int = 10,
                         codec: BlockCodec = None
                         ) -> tuple[bool, set[str]]:
    """检查 (world_x, world_z) 在地表上方是否有非空气、非可穿过、非自然地形方块。

    返回 (是否占用, 拦截的方块名集合)。空集表示越界 / 无地表 / 无拦截。
    blockers 用于上层做"漏哪些方块未列入 NATURAL_TERRAIN"的诊断。
    """
    NY, NZ, NX = scan_volume.shape
    sx, sz = ctx.w2s(int(world_x), int(world_z))

    if not (0 <= sx < NX and 0 <= sz < NZ):
        return True, set()  # 越界保守拒，不报具体方块

    # 找地表 y_index
    base_y_idx = None
    for yy in range(NY - 1, -1, -1):
        block = scan_volume[yy, sz, sx]
        if get_block_id(block, codec) != AIR_BLOCK:
            base_y_idx = yy
            break

    if base_y_idx is None:
        return False, set()

    # uint16 快速路径
    if codec is not None and scan_volume.dtype == np.uint16:
        y0 = base_y_idx + int(y_above_ground_min)
        y1 = min(base_y_idx + int(y_above_ground_max), NY - 1)
        z0 = max(0, sz - padding); z1 = min(NZ, sz + padding + 1)
        x0 = max(0, sx - padding); x1 = min(NX, sx + padding + 1)
        patch = scan_volume[y0:y1 + 1, z0:z1, x0:x1]
        non_air = patch[patch != codec.AIR_CODE]
        # Fix C：自然地形方块（相邻高列的山体伸入本列上方）不算"已占用"。
        blockers = {
            decoded for c in np.unique(non_air)
            if (decoded := codec.decode(int(c))) not in PASSTHROUGH_BLOCKS
            and not is_natural_terrain_block(decoded)
        }
        return (len(blockers) > 0, blockers)

    # fallback
    y0 = base_y_idx + int(y_above_ground_min)
    y1 = base_y_idx + int(y_above_ground_max)
    if y0 >= NY:
        return False, set()
    y1 = min(y1, NY - 1)

    blockers: set[str] = set()
    for y in range(y0, y1 + 1):
        for dz in range(-padding, padding + 1):
            for dx in range(-padding, padding + 1):
                xx = sx + dx; zz = sz + dz
                if not (0 <= xx < NX and 0 <= zz < NZ):
                    continue
                bid = get_block_id(scan_volume[y, zz, xx], codec)
                if (bid != AIR_BLOCK
                        and bid not in PASSTHROUGH_BLOCKS
                        and not is_natural_terrain_block(bid)):
                    blockers.add(bid)
    return (len(blockers) > 0, blockers)


def place_buildings_grid(candidates: list,
                        target_count: int,
                        ring_name: str,
                        height_map: np.ndarray,
                        scan_volume: np.ndarray,
                        terrain_map: np.ndarray,
                        placed_origins: list,
                        placed_boxes: list,
                        locked_rects: list,
                        ctx: ScanContext,
                        center_x: int, center_z: int,
                        codec: BlockCodec,
                        min_spacing: int = 16,
                        height_map_original: np.ndarray = None,
                        max_std: float = 2.5,
                        max_range: int = 5) -> list:
    """从候选点中选 target_count 个位置放建筑。

    过滤条件：地形不能是水、地形复杂度（std/range）必须低、
    包围盒不能与已放建筑相交、不能离已放建筑过近、
    角度扇区每个最多 1 栋（防扎堆）。

    返回 newly_placed = [{"path": ..., "origin": ..., "rotation": ...}, ...]
    """
    if height_map_original is None:
        height_map_original = height_map

    NZ, NX = height_map.shape
    newly_placed = []
    placed_count = 0

    # 角度扇区：防止建筑都挤在一片平坦区域
    num_angle_sectors = max(target_count, 8)
    angle_sector_count: dict[int, int] = {}
    MAX_PER_ANGLE_SECTOR = 1

    # 诊断 counter：每个 reject 原因累计，圈结束后总结一行，避免刷屏看不清谁是大头。
    reject_stats: dict[str, int] = {}
    # location_occupied 拒因下，记录是哪些非自然方块拦的（按方块名计数），
    # 圈结束顺带打出 top-5，方便决定 NATURAL_TERRAIN_BLOCKS 还该补哪些。
    occ_blockers: dict[str, int] = {}

    def _rej(reason: str):
        reject_stats[reason] = reject_stats.get(reason, 0) + 1

    def get_angle_sector(wx: int, wz: int) -> int:
        angle = math.atan2(wz - center_z, wx - center_x)
        angle_deg = math.degrees(angle) % 360
        return int(angle_deg * num_angle_sectors / 360) % num_angle_sectors

    for (world_x, world_z, score) in candidates:
        if placed_count >= target_count:
            break

        angle_sec = get_angle_sector(world_x, world_z)
        if angle_sector_count.get(angle_sec, 0) >= MAX_PER_ANGLE_SECTOR:
            _rej("angle_sector_full")
            continue

        sx, sz = ctx.w2s(world_x, world_z)
        if not (0 <= sx < NX and 0 <= sz < NZ):
            _rej("out_of_bounds")
            continue

        t_code = int(terrain_map[sz, sx])
        terrain_type = TERRAIN_NAMES[t_code] if t_code < len(TERRAIN_NAMES) else "plains"
        if terrain_type == "water" and not SEA_CITY_ENABLED:
            _rej("water_terrain")
            continue

        pool = list_region_files_for_terrain(ring_name, terrain_type)
        if not pool:
            pool = list_region_files_fallback(ring_name)
        if not pool:
            _rej("no_component_for_terrain")
            continue

        structure_path = random.choice(pool)
        rotation_deg = compute_facing_rotation(center_x, center_z, world_x, world_z)
        sx_building, sz_building = footprint_xz(structure_path, rotation_deg)

        fp_sx0 = sx - sx_building // 2
        fp_sx1 = sx + (sx_building - sx_building // 2 - 1)
        fp_sz0 = sz - sz_building // 2
        fp_sz1 = sz + (sz_building - sz_building // 2 - 1)

        if fp_sx0 < 0 or fp_sx1 >= NX or fp_sz0 < 0 or fp_sz1 >= NZ:
            _rej("footprint_out_of_bounds")
            continue

        candidate_box = make_box_from_center(world_x, world_z,
                                              sx_building, sz_building, padding=3)
        if any(boxes_intersect(candidate_box, b) for b in placed_boxes):
            _rej("box_intersect")
            continue

        if any(math.hypot(px - world_x, pz - world_z) < min_spacing
               for (px, _, pz) in placed_origins):
            _rej("too_close")
            continue

        dynamic_padding = int(max(3, min(8, 0.12 * (sx_building + sz_building))))
        occ, blockers = is_location_occupied(
            scan_volume, world_x, world_z, ctx,
            padding=dynamic_padding, codec=codec,
        )
        if occ:
            _rej("location_occupied")
            for b in blockers:
                occ_blockers[b] = occ_blockers.get(b, 0) + 1
            continue

        complexity = compute_footprint_complexity(
            height_map_original, fp_sx0, fp_sx1, fp_sz0, fp_sz1)

        # footprint 落在被截天花板的列上，max_y 不可信
        if complexity["min_y"] <= ctx.min_y:
            _rej("footprint_sentinel")
            print(f"  ⏭️ 跳过：footprint 含无效列 (撞扫描天花板)")
            continue

        # ── 卡 5：单栋 terraforming 取代 std/range 一票否决 ──
        # 有 terrain_features 时走 terraform 路径；没有则退回旧的复杂度过滤
        # （保留路径让 demo / 老调用方仍能运行）。
        terraformed = False
        terraform_result = None
        if getattr(ctx, "terrain_features", None) is not None:
            max_cut, max_fill = _TERRAFORM_LIMITS_BY_RING.get(
                ring_name, (TERRAFORM_MAX_CUT, TERRAFORM_MAX_FILL),
            )
            terraform_result = terraform_for_building(
                footprint_xz=(fp_sx0, fp_sz0, fp_sx1, fp_sz1),
                height_map=height_map,
                features=ctx.terrain_features,
                ctx=ctx,
                terrain_map=terrain_map,
                max_cut=max_cut,
                max_fill=max_fill,
            )
            if not terraform_result.success:
                # 把 too_steep_cut(max=N) 这种 reason 归一成 too_steep 便于聚合统计
                rk = terraform_result.reason.split("(")[0] or "terraform_unknown"
                _rej(f"terraform:{rk}")
                print(f"  ⏭️ 跳过：terraform 失败 ({terraform_result.reason})")
                continue
            ground_y = int(terraform_result.base_y)
            terraformed = True
        else:
            if complexity["std"] > max_std or complexity["range"] > max_range:
                _rej("complexity_too_high")
                print(f"  ⏭️ 跳过：地形太复杂 std={complexity['std']:.1f}, "
                      f"range={complexity['range']}")
                continue
            ground_y = complexity["max_y"]

        origin = (int(world_x - sx_building // 2),
                  int(ground_y),
                  int(world_z - sz_building // 2))

        placed_origins.append((int(world_x), int(ground_y), int(world_z)))
        placed_boxes.append(candidate_box)
        locked_rects.append((fp_sx0, fp_sx1, fp_sz0, fp_sz1))

        if terraformed:
            print(f"  ✅ {ring_name.upper()} #{placed_count + 1}: "
                  f"world=({world_x},{ground_y},{world_z}), "
                  f"terrain={terrain_type}, rot={rotation_deg}°, "
                  f"suit={score:.2f}, terraform cost={terraform_result.cost}")
        else:
            print(f"  ✅ {ring_name.upper()} #{placed_count + 1}: "
                  f"world=({world_x},{ground_y},{world_z}), "
                  f"terrain={terrain_type}, rot={rotation_deg}°, "
                  f"suit={score:.2f}, std={complexity['std']:.1f}, "
                  f"range={complexity['range']}")

        try:
            if terraformed:
                # 提交 terraform 改动到世界 + 同步内存。失败回退候选。
                ok = apply_terraform(terraform_result, ctx,
                                     scan_volume=scan_volume,
                                     height_map=height_map, codec=codec)
                if not ok:
                    _rej("apply_terraform_http_fail")
                    print(f"  ⚠️ apply_terraform HTTP 失败，回退候选")
                    placed_origins.pop(); placed_boxes.pop(); locked_rects.pop()
                    continue
            else:
                # 旧路径：柱基填充。terraform 路径已经包含填土，不再需要。
                build_stilt_foundation(
                    height_map=height_map_original,
                    sx0=fp_sx0, sx1=fp_sx1,
                    sz0=fp_sz0, sz1=fp_sz1,
                    base_y=ground_y,
                    ctx=ctx,
                    terrain_map=terrain_map,
                )
            clear_footprint_vegetation(fp_sx0, fp_sx1, fp_sz0, fp_sz1,
                                       ground_y, ctx)
            paste_volume(structure_path, origin=origin,
                         clear_target=False, rotation=rotation_deg)

            newly_placed.append({
                "path": structure_path,
                "origin": origin,
                "rotation": rotation_deg,
            })
            placed_count += 1
            angle_sector_count[angle_sec] = angle_sector_count.get(angle_sec, 0) + 1

        except Exception as e:
            _rej("paste_exception")
            print(f"  ⚠️ 粘贴失败: {e}")
            placed_origins.pop()
            placed_boxes.pop()
            locked_rects.pop()
            continue

    print(f"  {ring_name.upper()} 圈层完成：目标{target_count}栋，实际{placed_count}栋")
    if reject_stats:
        # 按数量降序排，便于定位主因
        sorted_rejects = sorted(reject_stats.items(), key=lambda kv: -kv[1])
        rej_str = ", ".join(f"{k}={v}" for k, v in sorted_rejects)
        print(f"  📊 {ring_name.upper()} 拒因统计 [候选={len(candidates)} 通过={placed_count}]: {rej_str}")
    if occ_blockers:
        # location_occupied 拦截的具体方块 top-5，决定下次 NATURAL_TERRAIN 补哪些
        top = sorted(occ_blockers.items(), key=lambda kv: -kv[1])[:5]
        blockers_str = ", ".join(f"{k}={v}" for k, v in top)
        print(f"  📊 {ring_name.upper()} location_occupied top blockers: {blockers_str}")
    return newly_placed
