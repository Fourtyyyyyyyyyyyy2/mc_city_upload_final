"""地形特征分析层（Priority 0 卡 1）。

从 height_map + scan_volume 派生一组 numpy mask/map：坡度、起伏、是否水、
是否平地、高度分带、山脊。下游卡 2~5 的"地形友好"决策全部读这层数据，
不再各自重算。

设计要点：
1. 全部矢量化（numpy + scipy.ndimage），10 万格 < 1 秒。
2. height_map sentinel = ctx.min_y 表示"无效列"（撞天花板或全空气）。
   所有派生数组在 valid_mask=False 处填合理默认值，不让 sentinel 的低值
   污染坡度 / 起伏度的局部统计——做法是计算前把 sentinel 替换成有效格
   的均值（一个稳定背景），算完再 mask 回去。
3. 不修改任何现有模块，纯新增。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
from scipy.ndimage import maximum_filter, median_filter, uniform_filter

from ..config import (
    ELEVATION_ZONE_PERCENTILES,
    FLAT_SLOPE_THRESHOLD,
    RIDGE_PROMINENCE,
    ROUGHNESS_WINDOW,
    WATER_BLOCK_IDS,
)


@dataclass
class TerrainFeatures:
    """地形特征数据集，形状均为 (NZ, NX)，与 height_map 一致。

    无效格（height_map <= ctx.min_y）的派生值：
        slope_map = 0, roughness_map = 0, is_water = False,
        is_flat = False, elevation_zone = 0, ridge_mask = False.
    判断"是否可用"请显式查 valid_mask，不要靠派生值反推。
    """
    height_map: np.ndarray         # int32, 复制自原 height_map
    valid_mask: np.ndarray         # bool, True = 该格有有效地表
    slope_map: np.ndarray          # float32, |∇h| 梯度模
    roughness_map: np.ndarray      # float32, ROUGHNESS_WINDOW 方窗高度标准差
    is_water: np.ndarray           # bool, 顶层方块是水/岩浆
    is_flat: np.ndarray            # bool, slope < FLAT_SLOPE_THRESHOLD 且 valid
    elevation_zone: np.ndarray     # int8, 0=low / 1=mid / 2=high（按 valid 区分位数）
    ridge_mask: np.ndarray         # bool, 局部极大且高出邻域中位数 RIDGE_PROMINENCE


def analyze_terrain(ctx,
                    height_map: np.ndarray,
                    scan_volume: np.ndarray,
                    codec=None) -> TerrainFeatures:
    """从 ctx + height_map + scan_volume 计算 TerrainFeatures。

    Args:
        ctx:         ScanContext。读 ctx.min_y 判 sentinel；若 ctx 上挂了
                     codec（getattr(ctx,'codec',None)），优先用它做水域检测。
        height_map:  (NZ, NX) int32，scan/height_map.generate_height_map 的产出。
        scan_volume: (NY, NZ, NX) 体素数据。uint16+codec 走快速路径；其它
                     dtype 时 is_water 退化为 SEA_LEVEL 启发式（精度下降）。
        codec:       可选 BlockCodec；优先级高于 ctx.codec。

    返回的所有数组都是新分配的，调用方可以放心修改。
    """
    if height_map.ndim != 2:
        raise ValueError(f"height_map 必须是 2D，实际 shape={height_map.shape}")

    NZ, NX = height_map.shape
    min_y = int(ctx.min_y)
    hm = height_map.astype(np.int32, copy=True)

    valid_mask = hm > min_y

    # ── 为了让坡度/起伏度在 sentinel 边缘不爆炸，先把无效格替换成
    #    有效格的均值，相当于"恒值背景"。算完再把无效格的派生值清零。
    h_float = hm.astype(np.float32)
    if valid_mask.any():
        fill_value = float(hm[valid_mask].mean())
    else:
        fill_value = float(min_y)
    h_filled = np.where(valid_mask, h_float, np.float32(fill_value))

    # 坡度：np.gradient 默认中心差分。模 = sqrt(gx^2 + gz^2)。
    grad_z, grad_x = np.gradient(h_filled)
    slope_map = np.sqrt(grad_x * grad_x + grad_z * grad_z).astype(np.float32)
    slope_map[~valid_mask] = 0.0

    # 起伏度：方窗 std = sqrt(E[h²] - E[h]²)。
    win = int(ROUGHNESS_WINDOW)
    if win % 2 == 0:
        raise ValueError(f"ROUGHNESS_WINDOW 必须是奇数，当前={win}")
    local_mean = uniform_filter(h_filled, size=win, mode="nearest")
    local_mean_sq = uniform_filter(h_filled * h_filled, size=win, mode="nearest")
    local_var = np.maximum(local_mean_sq - local_mean * local_mean, 0.0)
    roughness_map = np.sqrt(local_var).astype(np.float32)
    roughness_map[~valid_mask] = 0.0

    # 水域：查 height_map 对应高度的方块是不是水。
    is_water = _detect_water(hm, scan_volume, valid_mask, min_y,
                             codec=codec or getattr(ctx, "codec", None))

    # 平地：valid + 坡度小 + 不是水。
    is_flat = valid_mask & (slope_map < FLAT_SLOPE_THRESHOLD) & (~is_water)

    # 高度分带：按 valid 区的 33/66 分位数划分。
    elevation_zone = _compute_elevation_zone(hm, valid_mask)

    # 山脊：局部最大 + 比邻域中位数高出 RIDGE_PROMINENCE。
    # maximum_filter / median_filter 都是 scipy.ndimage 的矢量化原语。
    local_max = maximum_filter(h_filled, size=5, mode="nearest")
    local_median = median_filter(h_filled, size=5, mode="nearest")
    ridge_mask = (
        (h_filled >= local_max)
        & ((h_filled - local_median) >= RIDGE_PROMINENCE)
        & valid_mask
    )

    return TerrainFeatures(
        height_map=hm,
        valid_mask=valid_mask,
        slope_map=slope_map,
        roughness_map=roughness_map,
        is_water=is_water,
        is_flat=is_flat,
        elevation_zone=elevation_zone,
        ridge_mask=ridge_mask,
    )


def _detect_water(height_map: np.ndarray,
                  scan_volume: np.ndarray,
                  valid_mask: np.ndarray,
                  min_y: int,
                  codec) -> np.ndarray:
    """is_water 检测。

    优先级：
      1) scan_volume 是 uint16 且 codec 有效 → 矢量化查 top 方块 ∈ 水域 codes。
      2) 退化路径：height_map <= SEA_LEVEL 的有效格直接判为水（仅作兜底，
         demo / 极端环境下使用）。
    """
    NZ, NX = height_map.shape
    is_water = np.zeros((NZ, NX), dtype=bool)

    if not valid_mask.any():
        return is_water

    fast_path = (
        codec is not None
        and isinstance(scan_volume, np.ndarray)
        and scan_volume.dtype == np.uint16
        and scan_volume.ndim == 3
    )

    if fast_path:
        water_codes = []
        for name in WATER_BLOCK_IDS:
            code = codec.name_to_code.get(name)
            if code is not None:
                water_codes.append(int(code))
        if not water_codes:
            return is_water  # codec 还没见过水方块（合成数据时常见）

        NY = scan_volume.shape[0]
        # y_index：把世界 Y 换回 scan y 索引；无效格 clip 到 0 避免 IndexError。
        y_idx = np.clip(height_map - min_y, 0, NY - 1)
        zs = np.arange(NZ).reshape(NZ, 1)
        xs = np.arange(NX).reshape(1, NX)
        top_codes = scan_volume[y_idx, zs, xs]      # (NZ, NX) uint16
        is_water = np.isin(top_codes, np.array(water_codes, dtype=np.uint16))
        is_water &= valid_mask
        return is_water

    # 退化路径：没有 codec 或 scan_volume 不是 compact。
    from ..config import SEA_LEVEL
    is_water = valid_mask & (height_map <= SEA_LEVEL)
    return is_water


def _compute_elevation_zone(height_map: np.ndarray,
                            valid_mask: np.ndarray) -> np.ndarray:
    """按全图有效格高度的 (p_low, p_high) 分位数把每格划入 0/1/2。

    无效格 → 0。注意：0 同时是 "low" 和 "invalid"——下游若关心区别，
    必须显式查 valid_mask。
    """
    zone = np.zeros(height_map.shape, dtype=np.int8)
    if not valid_mask.any():
        return zone

    p_low, p_high = ELEVATION_ZONE_PERCENTILES
    valid_heights = height_map[valid_mask]
    thr_low = np.percentile(valid_heights, p_low)
    thr_high = np.percentile(valid_heights, p_high)

    # 先按高度划分，再把无效格压回 0。
    zone[height_map >= thr_low] = 1
    zone[height_map >= thr_high] = 2
    zone[~valid_mask] = 0
    return zone
