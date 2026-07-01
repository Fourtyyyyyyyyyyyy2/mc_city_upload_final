"""清除树木：全范围 / 单建筑 footprint / 通用植被清理。"""
import numpy as np
from collections import Counter

from ..mc.blocks import AIR_BLOCK, get_block_id, is_tree_block_id
from ..mc.codec import BlockCodec
from ..mc.placement import set_blocks_batch
from ..scan.coord_frame import ScanContext


def clear_trees_in_scan(scan_volume: np.ndarray,
                        height_map: np.ndarray,
                        ctx: ScanContext,
                        max_height_above_ground: int = 80,
                        batch_size: int = 1024,
                        mutate_scan: bool = True,
                        codec: BlockCodec = None) -> int:
    """全范围矢量化伐树（uint16 快速路径 ~200x 加速）。

    地面以下的"树木方块"被认作误判，会被过滤掉。
    返回清除的方块数。
    """
    NY, NZ, NX = scan_volume.shape
    scan_y0 = ctx.min_y
    print(f"🪓 clear_trees_in_scan: scan_y0 = {scan_y0}")

    is_compact = (codec is not None and scan_volume.dtype == np.uint16)

    if is_compact:
        tree_names = [n for n in codec.name_to_code if is_tree_block_id(n)]
        tree_codes = [codec.name_to_code[n] for n in tree_names
                      if codec.name_to_code.get(n) is not None]

        if not tree_codes:
            print("✅ 编码表中没有树木方块，跳过")
            return 0

        tree_mask = np.isin(scan_volume, tree_codes)
        tree_positions = np.argwhere(tree_mask)

        # 不再做 world_y > ground_y 过滤：is_tree_block_id 已精确匹配
        # _log/_wood/_leaves 等，被埋的木头清掉也无妨；用 height_map 过滤
        # 反而在地图里有悬空建筑/高耸结构时会把真正的树误判为地下而漏掉。

        batch = []
        batch_positions = []  # parallel list: (yi, zs, xs) to mutate after HTTP ok
        cleared = 0
        failed = 0
        air_code = codec.AIR_CODE

        def _flush_compact():
            nonlocal batch, batch_positions, cleared, failed
            if not batch:
                return
            ok = set_blocks_batch(batch)
            if ok:
                cleared += len(batch)
                if mutate_scan:
                    for (yi2, zs2, xs2) in batch_positions:
                        scan_volume[yi2, zs2, xs2] = air_code
            else:
                failed += len(batch)
            batch = []
            batch_positions = []

        for yi, zs, xs in tree_positions:
            world_y = int(yi) + scan_y0
            xw, zw = ctx.s2w(int(xs), int(zs))
            batch.append({"x": int(xw), "y": int(world_y), "z": int(zw),
                          "id": AIR_BLOCK})
            batch_positions.append((int(yi), int(zs), int(xs)))
            if len(batch) >= batch_size:
                _flush_compact()

        _flush_compact()

        if failed:
            print(f"⚠️ 树木清除：成功 {cleared} / 失败 {failed} 个方块 "
                  f"(HTTP 失败的批次未同步到 scan_volume)")
        else:
            print(f"✅ 树木清除完成：{cleared} 个方块")
        return cleared

    # fallback：dict / str 数组
    batch = []
    batch_positions = []  # parallel list: (yy, zs, xs)
    cleared = 0
    failed = 0
    fallback_air = codec.AIR_CODE if codec else AIR_BLOCK

    def _flush_fallback():
        nonlocal batch, batch_positions, cleared, failed
        if not batch:
            return
        ok = set_blocks_batch(batch)
        if ok:
            cleared += len(batch)
            if mutate_scan:
                for (yy2, zs2, xs2) in batch_positions:
                    scan_volume[yy2, zs2, xs2] = fallback_air
        else:
            failed += len(batch)
        batch = []
        batch_positions = []

    for zs in range(NZ):
        for xs in range(NX):
            surface_idx = None
            for yy in range(NY - 1, -1, -1):
                bid = get_block_id(scan_volume[yy, zs, xs], codec)
                if bid != AIR_BLOCK and not is_tree_block_id(bid):
                    surface_idx = yy
                    break
            if surface_idx is None:
                continue
            y_end = min(NY - 1, surface_idx + max_height_above_ground)
            xw, zw = ctx.s2w(xs, zs)
            for yy in range(surface_idx + 1, y_end + 1):
                bid = get_block_id(scan_volume[yy, zs, xs], codec)
                if is_tree_block_id(bid):
                    world_y = yy + scan_y0
                    batch.append({"x": int(xw), "y": int(world_y), "z": int(zw),
                                  "id": AIR_BLOCK})
                    batch_positions.append((int(yy), int(zs), int(xs)))
                if len(batch) >= batch_size:
                    _flush_fallback()
    _flush_fallback()
    if failed:
        print(f"⚠️ 树木清除：成功 {cleared} / 失败 {failed} 个方块")
    else:
        print(f"✅ 树木清除完成：{cleared} 个方块")
    return cleared


def clear_trees_in_footprint(scan_volume: np.ndarray,
                             height_map: np.ndarray,
                             ctx: ScanContext,
                             sx0: int, sx1: int, sz0: int, sz1: int,
                             codec: BlockCodec = None,
                             batch_size: int = 2048,
                             mutate_scan: bool = True) -> int:
    """只清理指定 footprint 范围内的树（比 clear_trees_in_scan 保守）。"""
    NY, NZ, NX = scan_volume.shape
    scan_y0 = ctx.min_y
    H, W = height_map.shape
    sx0 = max(0, sx0); sx1 = min(W - 1, sx1)
    sz0 = max(0, sz0); sz1 = min(H - 1, sz1)

    is_compact = (codec is not None and scan_volume.dtype == np.uint16)
    batch = []
    batch_positions = []  # parallel list: (yi, zs, xs)
    cleared = 0
    failed = 0
    air_code = codec.AIR_CODE if codec else AIR_BLOCK

    def _flush():
        nonlocal batch, batch_positions, cleared, failed
        if not batch:
            return
        ok = set_blocks_batch(batch)
        if ok:
            cleared += len(batch)
            if mutate_scan:
                for (yi2, zs2, xs2) in batch_positions:
                    scan_volume[yi2, zs2, xs2] = air_code
        else:
            failed += len(batch)
        batch = []
        batch_positions = []

    if is_compact:
        tree_names = [n for n in codec.name_to_code if is_tree_block_id(n)]
        tree_codes = {codec.name_to_code[n] for n in tree_names
                      if codec.name_to_code.get(n) is not None}
        if not tree_codes:
            return 0

        for zs in range(sz0, sz1 + 1):
            for xs in range(sx0, sx1 + 1):
                ground_y = int(height_map[zs, xs])
                for yi in range(NY):
                    code = int(scan_volume[yi, zs, xs])
                    if code in tree_codes:
                        world_y = yi + scan_y0
                        if world_y <= ground_y:
                            continue
                        xw, zw = ctx.s2w(xs, zs)
                        batch.append({"x": int(xw), "y": int(world_y), "z": int(zw),
                                      "id": AIR_BLOCK})
                        batch_positions.append((int(yi), int(zs), int(xs)))
                        if len(batch) >= batch_size:
                            _flush()
    else:
        for zs in range(sz0, sz1 + 1):
            for xs in range(sx0, sx1 + 1):
                ground_y = int(height_map[zs, xs])
                for yi in range(NY):
                    bid = get_block_id(scan_volume[yi, zs, xs], codec)
                    if is_tree_block_id(bid):
                        world_y = yi + scan_y0
                        if world_y <= ground_y:
                            continue
                        xw, zw = ctx.s2w(xs, zs)
                        batch.append({"x": int(xw), "y": int(world_y), "z": int(zw),
                                      "id": AIR_BLOCK})
                        batch_positions.append((int(yi), int(zs), int(xs)))
                        if len(batch) >= batch_size:
                            _flush()

    _flush()
    if failed:
        print(f"⚠️ footprint 树木清除：成功 {cleared} / 失败 {failed} 个方块")
    return cleared


def clear_footprint_vegetation(sx0: int, sx1: int, sz0: int, sz1: int,
                               ground_y: int,
                               ctx: ScanContext,
                               clear_height: int = 12):
    """粘贴建筑前清除占地上方 clear_height 格内的所有方块（草、花、矮树苗等）。"""
    batch = []
    for zs in range(sz0, sz1 + 1):
        for xs in range(sx0, sx1 + 1):
            xw, zw = ctx.s2w(xs, zs)
            for dy in range(1, clear_height + 1):
                batch.append({"x": int(xw), "y": int(ground_y) + dy, "z": int(zw),
                              "id": AIR_BLOCK})
    for i in range(0, len(batch), 2048):
        set_blocks_batch(batch[i:i + 2048])
