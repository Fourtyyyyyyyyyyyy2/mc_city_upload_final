"""城市选址：在 height_map 上找最适合建城的中心点。

包含两个入口：
    find_best_city_center(height_map, ctx, city_radius)
        legacy 入口，纯 height_map 启发式（局部高度方差 + 水比例）。
        被 find_dramatic_center 当兜底用。

    find_dramatic_center(ctx, features, fallback_radius)
        卡 2 引入。基于 TerrainFeatures 的多维加权评分：戏剧性、突出度、
        可建度、临水距、中心偏置。挑视觉权重最高且能建的点。
"""
from __future__ import annotations

import numpy as np
from scipy.ndimage import (
    distance_transform_edt,
    maximum_filter,
    minimum_filter,
    uniform_filter,
)

from ..config import (
    CENTER_DRAMA_RADIUS,
    CENTER_MAX_CORE_RELIEF,
    CENTER_CORE_RELIEF_WINDOW,
    CENTER_MIN_BUILDABLE_RATIO,
    CENTER_REGIONAL_BUILDABLE_ENABLED,
    CENTER_REGIONAL_BUILDABLE_WINDOW,
    CENTER_SAMPLE_STRIDE,
    SEA_CITY_ENABLED,
    SEA_LEVEL,
    WALL_RADIUS,
)
from ..scan.coord_frame import ScanContext


# ─────────────────────────────────────────────────────────────────
# Legacy 入口：保留作兜底
# ─────────────────────────────────────────────────────────────────

def find_best_city_center(height_map: np.ndarray, ctx: ScanContext,
                          city_radius: int = 190) -> tuple[int, int]:
    """评分 = 局部高度方差 + 水域比例惩罚（越低越好）。

    返回 (world_x, world_z)。
    """
    NZ, NX = height_map.shape
    h = height_map.astype(float)

    win = city_radius * 2 + 1
    local_mean = uniform_filter(h, size=win, mode='nearest')
    local_mean_sq = uniform_filter(h ** 2, size=win, mode='nearest')
    local_var = np.maximum(local_mean_sq - local_mean ** 2, 0.0)

    water_mask = (h <= SEA_LEVEL + 1).astype(float)
    local_water = uniform_filter(water_mask, size=win, mode='nearest')

    score = local_var + local_water * 500.0

    margin = city_radius + 10
    if 2 * margin >= NZ or 2 * margin >= NX:
        # 扫描区域太小，直接取中心
        best_zs, best_xs = NZ // 2, NX // 2
    else:
        score[:margin, :] = np.inf
        score[-margin:, :] = np.inf
        score[:, :margin] = np.inf
        score[:, -margin:] = np.inf
        idx = int(np.argmin(score))
        best_zs, best_xs = np.unravel_index(idx, score.shape)

    best_xw = ctx.origin_x + int(best_xs)
    best_zw = ctx.origin_z + int(best_zs)

    std_val = float(local_var[best_zs, best_xs]) ** 0.5
    water_pct = float(local_water[best_zs, best_xs]) * 100.0
    print(f"[INFO] 最优城市中心: scan({best_xs},{best_zs}) → world({best_xw},{best_zw})"
          f"  高度std={std_val:.1f}  水域={water_pct:.1f}%", flush=True)

    return best_xw, best_zw


# ─────────────────────────────────────────────────────────────────
# 卡 2：戏剧性选址
# ─────────────────────────────────────────────────────────────────

CENTER_SCORE_WEIGHTS = {
    "drama":           0.30,    # 周围高差越大越好（山脚 / 谷地中央）
    "prominence":      0.25,    # 自身在邻域里的突出度（半岛 / 山脊）
    "buildable":       0.25,    # 8x8 平地占比，硬约束之外再当软评分
    "water_proximity": 0.10,    # 距水域 10..30 格内加分
    "center_bias":     0.10,    # 距扫描中心近一点，防止偏角
}

# 区域可建率模式（CENTER_REGIONAL_BUILDABLE_ENABLED）：把"城半径内可建率"当主项，
# 压低 drama/prominence → 城落在能填满的连片平地，不再稀疏。和上面权重二选一。
CENTER_SCORE_WEIGHTS_REGIONAL = {
    "drama":              0.15,
    "prominence":         0.10,
    "buildable":          0.15,    # 局部 8x8（保城心本身够平）
    "regional_buildable": 0.40,    # 城半径内可建格占比（治本主项）
    "water_proximity":    0.10,
    "center_bias":        0.10,
}

# 归一化用的"饱和值"：raw 指标 >= 这里的值就算满分 1.0。
# 这些是经验值，调参时改这里而不是改下游使用方。
_DRAMA_RANGE_SAT = 30.0          # 邻域高差 >= 30 块 → drama 满分
_PROMINENCE_SAT = 10.0           # 突出度 >= 10 块 → prominence 满分
_WATER_PEAK_DIST = 20.0          # 距水 20 格时 water_proximity 满分
_WATER_TOLERANCE = 10.0          # ±10 格内才有分；10/30 处为 0


def find_dramatic_center(ctx: ScanContext,
                         features,
                         fallback_radius: int = 32) -> tuple[int, int, int]:
    """寻找"有戏剧性"的中心点。

    Args:
        ctx:             ScanContext。返回的中心点用世界坐标。
        features:        scan.terrain_analysis.TerrainFeatures。
        fallback_radius: 全军覆没时调 find_best_city_center 的 city_radius。

    Returns:
        (world_cx, world_cy, world_cz)。cy 是该位置在 height_map 上读到的地表 Y。
        若 features 全无效或硬过滤把候选清空，回退用 find_best_city_center +
        height_map[cz, cx] 读 cy。
    """
    h_map = features.height_map
    NZ, NX = h_map.shape
    valid = features.valid_mask
    is_water = features.is_water
    is_flat = features.is_flat

    # ── 5 张评分图，全部 (NZ, NX) float32，范围 [0, 1] ───────────
    drama_score, prominence_score = _drama_and_prominence(h_map, valid)
    buildable_score = _buildable(is_flat)
    water_score = _water_proximity(is_water)
    center_bias_score = _center_bias(NZ, NX)

    # footprint 尺度的高差：用于（渐进放宽地）排除树 terraform 必失败的陡坡中心。
    core_relief = _core_relief(h_map, valid)

    # ── 加权总分 ─────────────────────────────────────────────────
    if CENTER_REGIONAL_BUILDABLE_ENABLED:
        regional_score = _regional_buildable(
            is_flat, valid, is_water, CENTER_REGIONAL_BUILDABLE_WINDOW)
        W = CENTER_SCORE_WEIGHTS_REGIONAL
        total = (
            W["drama"]              * drama_score
            + W["prominence"]       * prominence_score
            + W["buildable"]        * buildable_score
            + W["regional_buildable"] * regional_score
            + W["water_proximity"]  * water_score
            + W["center_bias"]      * center_bias_score
        ).astype(np.float32)
    else:
        regional_score = None
        W = CENTER_SCORE_WEIGHTS
        total = (
            W["drama"]           * drama_score
            + W["prominence"]    * prominence_score
            + W["buildable"]     * buildable_score
            + W["water_proximity"] * water_score
            + W["center_bias"]   * center_bias_score
        ).astype(np.float32)

    # ── 硬约束 mask ──────────────────────────────────────────────
    # 1) valid; 2) 自己不是水; 3) 8x8 邻域平地比例够; 4) 距 scan 边缘有 margin
    #
    # 边距 margin 三级降级：
    #   优先 city_dims.edge_margin（=城最大外延+buffer，保证整城而非仅城墙在界内）；
    #     无 ctx.city_dims（直接调用/未挂）回退 WALL_RADIUS 旧值。flag=False 时
    #     city_dims.edge_margin == WALL_RADIUS，行为与改前逐字段一致。
    #   scan 太小放不下 → 退到 max(DRAMA_RADIUS+4, fallback_radius//4)，至少保灵魂树
    #   还放不下 → 最后兜底 min(NZ,NX)//4
    # 历史上 v1 只有 ~20 格 margin，center 偶尔选在 scan 边角导致
    # _place_core 拿 out_of_bounds、mid/outer 圈大量 footprint 超界（见 §10 日志）。
    # 海城模式：不把水排除出选址（城心可落在/靠近水，城区延伸到海面）。
    water_excl = np.ones_like(is_water) if SEA_CITY_ENABLED else (~is_water)
    base_mask = valid & water_excl & (buildable_score >= CENTER_MIN_BUILDABLE_RATIO)
    city_dims = getattr(ctx, "city_dims", None)
    primary_margin = city_dims.edge_margin if city_dims is not None else WALL_RADIUS
    for cand in (primary_margin,
                 max(CENTER_DRAMA_RADIUS + 4, fallback_radius // 4),
                 min(NZ, NX) // 4):
        if 2 * cand < min(NZ, NX):
            edge_margin = int(cand)
            break
    else:
        edge_margin = min(NZ, NX) // 4
    base_mask[:edge_margin, :] = False
    base_mask[-edge_margin:, :] = False
    base_mask[:, :edge_margin] = False
    base_mask[:, -edge_margin:] = False

    # ── 候选采样：stride 步长 ────────────────────────────────────
    stride = max(1, int(CENTER_SAMPLE_STRIDE))
    cand_zs = np.arange(edge_margin, NZ - edge_margin, stride)
    cand_xs = np.arange(edge_margin, NX - edge_margin, stride)
    if cand_zs.size == 0 or cand_xs.size == 0:
        return _fallback(ctx, h_map, fallback_radius, "扫描区太小，没采样到候选")

    zsc, xsc = np.meshgrid(cand_zs, cand_xs, indexing="ij")
    base_at_cand = base_mask[zsc, xsc]
    relief_at_cand = core_relief[zsc, xsc]

    # footprint 起伏：渐进放宽的硬阈值。先要够平（terraform 必成、树不半埋、广场不残），
    # 该阈值下无候选就逐级放宽，但**始终留在主路径**（稳健 WALL_RADIUS margin），
    # 绝不掉进 margin 很弱的 legacy fallback（那会选到 scan 角落 → out_of_bounds）。
    if CENTER_MAX_CORE_RELIEF > 0:
        relief_caps = [float(CENTER_MAX_CORE_RELIEF), 60.0, 90.0, float("inf")]
    else:
        relief_caps = [float("inf")]
    feasible_at_cand = base_at_cand
    used_cap = float("inf")
    for cap in relief_caps:
        trial = base_at_cand & (relief_at_cand <= cap)
        if trial.any():
            feasible_at_cand, used_cap = trial, cap
            break
    n_candidates = int(feasible_at_cand.sum())

    if n_candidates == 0:
        return _fallback(ctx, h_map, fallback_radius, "无可建点（buildable+margin 内全空）")
    if CENTER_MAX_CORE_RELIEF > 0 and used_cap > CENTER_MAX_CORE_RELIEF:
        cap_txt = "∞" if used_cap == float("inf") else str(int(used_cap))
        print(f"   ⚠️ relief≤{int(CENTER_MAX_CORE_RELIEF)} 无候选，放宽到 ≤{cap_txt}"
              f"（地形崎岖，核心可能仍需大 terraform 或回退单点）", flush=True)

    # 取可行候选里 total 最高的
    total_at_cand = np.where(feasible_at_cand, total[zsc, xsc], -np.inf)
    flat_idx = int(np.argmax(total_at_cand))
    grid_z, grid_x = np.unravel_index(flat_idx, total_at_cand.shape)
    best_zs = int(cand_zs[grid_z])
    best_xs = int(cand_xs[grid_x])

    cy = int(h_map[best_zs, best_xs])
    cx_world, cz_world = ctx.s2w(best_xs, best_zs)

    # 日志：总分 + 各项分 + 候选数。
    regional_txt = (f"regional={float(regional_score[best_zs, best_xs]):.2f} "
                    if regional_score is not None else "")
    print(
        f"[CENTER] dramatic@world({cx_world},{cz_world},y={cy}) "
        f"total={float(total[best_zs, best_xs]):.3f} "
        f"drama={float(drama_score[best_zs, best_xs]):.2f} "
        f"prom={float(prominence_score[best_zs, best_xs]):.2f} "
        f"buildable={float(buildable_score[best_zs, best_xs]):.2f} "
        f"{regional_txt}"
        f"relief={float(core_relief[best_zs, best_xs]):.0f} "
        f"water={float(water_score[best_zs, best_xs]):.2f} "
        f"cbias={float(center_bias_score[best_zs, best_xs]):.2f} "
        f"candidates={n_candidates}",
        flush=True,
    )

    return int(cx_world), int(cy), int(cz_world)


# ─────────────────────────────────────────────────────────────────
# 各项评分（全部矢量化）
# ─────────────────────────────────────────────────────────────────

def _drama_and_prominence(h_map: np.ndarray,
                          valid_mask: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """drama = 邻域高差（被山/谷环绕）。prominence = 自己 vs 邻域均值。"""
    h = h_map.astype(np.float32)
    if valid_mask.any():
        h_fill = np.where(valid_mask, h, np.float32(h[valid_mask].mean()))
    else:
        h_fill = h.copy()

    drama_window = 2 * int(CENTER_DRAMA_RADIUS) + 1
    drama_max = maximum_filter(h_fill, size=drama_window, mode="nearest")
    drama_min = minimum_filter(h_fill, size=drama_window, mode="nearest")
    drama_range = drama_max - drama_min
    drama_score = np.clip(drama_range / _DRAMA_RANGE_SAT, 0.0, 1.0).astype(np.float32)
    drama_score[~valid_mask] = 0.0

    # prominence：3x3 mean = 自己 + 8 邻居的平均。差值 > 0 表示突出。
    local_mean8 = uniform_filter(h_fill, size=3, mode="nearest")
    prominence_raw = np.maximum(h_fill - local_mean8, 0.0)
    prominence_score = np.clip(prominence_raw / _PROMINENCE_SAT, 0.0, 1.0).astype(np.float32)
    prominence_score[~valid_mask] = 0.0

    return drama_score, prominence_score


def _buildable(is_flat: np.ndarray) -> np.ndarray:
    """8x8 邻域里 is_flat 的比例 = uniform_filter on float。"""
    return uniform_filter(is_flat.astype(np.float32), size=8, mode="nearest")


def _regional_buildable(is_flat: np.ndarray, valid_mask: np.ndarray,
                        is_water: np.ndarray, window: int) -> np.ndarray:
    """城半径尺度的可建率：window×window 窗里 (flat & valid & ~water) 的占比。

    治本城稀疏——城心 8×8 平不代表整城能填满；这里看大窗，让城落在连片可建地。
    """
    # 海城模式：水也算可建（海面立楼），不从可建率里扣掉。
    water_term = np.ones_like(is_water) if SEA_CITY_ENABLED else (~is_water)
    buildable = (is_flat & valid_mask & water_term).astype(np.float32)
    return uniform_filter(buildable, size=max(8, int(window)), mode="nearest")


def _core_relief(h_map: np.ndarray, valid_mask: np.ndarray) -> np.ndarray:
    """footprint 尺度（CENTER_CORE_RELIEF_WINDOW）的高差 = max-min。

    无效列用 valid 区均值填充再求 max/min，避免 sentinel 把高差拉爆；无效列本身
    置 +inf，确保被硬过滤排除。返回 (NZ, NX) float32。
    """
    h = h_map.astype(np.float32)
    if valid_mask.any():
        h_fill = np.where(valid_mask, h, np.float32(h[valid_mask].mean()))
    else:
        h_fill = h.copy()
    win = max(1, int(CENTER_CORE_RELIEF_WINDOW))
    hi = maximum_filter(h_fill, size=win, mode="nearest")
    lo = minimum_filter(h_fill, size=win, mode="nearest")
    relief = (hi - lo).astype(np.float32)
    relief[~valid_mask] = np.float32(np.inf)
    return relief


def _water_proximity(is_water: np.ndarray) -> np.ndarray:
    """距水 10..30 格内有分，20 格处满分；其余 0。

    distance_transform_edt：对 ~mask 求到最近 True 的欧氏距离。当没有水时
    所有距离都是 inf，整图返回 0。
    """
    if not is_water.any():
        return np.zeros(is_water.shape, dtype=np.float32)
    water_dist = distance_transform_edt(~is_water).astype(np.float32)
    score = 1.0 - np.abs(water_dist - _WATER_PEAK_DIST) / _WATER_TOLERANCE
    score = np.clip(score, 0.0, 1.0).astype(np.float32)
    return score


def _center_bias(NZ: int, NX: int) -> np.ndarray:
    """距 scan 几何中心越近越好。归一化到 [0, 1]。"""
    zs_grid, xs_grid = np.indices((NZ, NX), dtype=np.float32)
    cx = (NX - 1) / 2.0
    cz = (NZ - 1) / 2.0
    dist = np.sqrt((xs_grid - cx) ** 2 + (zs_grid - cz) ** 2)
    max_dist = float(np.hypot(cx, cz))
    if max_dist <= 0:
        return np.ones((NZ, NX), dtype=np.float32)
    return (1.0 - dist / max_dist).astype(np.float32)


# ─────────────────────────────────────────────────────────────────
# 兜底
# ─────────────────────────────────────────────────────────────────

def _fallback(ctx: ScanContext,
              h_map: np.ndarray,
              fallback_radius: int,
              reason: str) -> tuple[int, int, int]:
    """所有候选都不达标时调用旧的 find_best_city_center 拿一个 2D 中心，
    再从 h_map 读 cy 拼成 3D。打印为什么走兜底。
    """
    print(f"[CENTER][FALLBACK] find_dramatic_center 回退原因: {reason}", flush=True)
    cx_world, cz_world = find_best_city_center(h_map, ctx, city_radius=fallback_radius)
    scx, scz = ctx.w2s(cx_world, cz_world)
    NZ, NX = h_map.shape
    scx = int(np.clip(scx, 0, NX - 1))
    scz = int(np.clip(scz, 0, NZ - 1))
    cy = int(h_map[scz, scx])
    return int(cx_world), int(cy), int(cz_world)
