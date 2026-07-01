"""mc_city 主入口：连接 → 暂停世界 → 扫描/加载 → 选址 → 建城 → 恢复世界。

运行方式（两种都支持）：
    python -m mc_city.main              # 推荐
    python mc_city/main.py              # IDE 绿色三角直接跑
两种都可加 --rescan 强制重新扫描。

缓存自动失效：data/scan_meta.json 会记录扫描参数 (x1..z2, y1..y2)。
下次启动时如果代码里的参数和 meta 不一致 → 自动重新扫描。
"""
from __future__ import annotations

# IDE/直接跑脚本时（python mc_city/main.py）补救相对导入
if __name__ == "__main__" and __package__ in (None, ""):
    import os as _os
    import sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    __package__ = "mc_city"

import argparse
import builtins
import json
import os
import re
import sys

import numpy as np
from tqdm import tqdm

from gdpc.interface import getBuildArea, getPlayers, getVersion
from gdpc.exceptions import BuildAreaNotSetError

from .city import build_city, compute_city_dims, find_dramatic_center
from .config import (
    BLOCK_CODEC_JSON, DEFAULT_HOST, HEIGHT_MAP_NPY,
    LARGE_BUILDAREA_VISUALS_ENABLED, MAX_DETAILED_SCAN_SIZE,
    SCAN_BLOCKS_COMPACT_NPY, SCAN_BLOCKS_NPY, SCAN_META_JSON,
)
from .mc.codec import BlockCodec
from .mc.command import pause_world, resume_world
from .scan.coord_frame import ScanContext
from .scan.height_map import generate_height_map
from .scan.scanner import scan_minecraft
from .scan.terrain_analysis import analyze_terrain

_ORIGINAL_PRINT = builtins.print
_ICON_RE = re.compile(r"[\u200d\ufe0f\u2100-\u214f\u2190-\u2bff\U0001f300-\U0001faff]")


def _strip_icons(value):
    if isinstance(value, str):
        return _ICON_RE.sub("", value)
    return value


def _install_plain_print():
    """Strip emoji/icon symbols from logs before console encoding."""
    if getattr(builtins.print, "_mc_city_plain", False):
        return

    def plain_print(*args, **kwargs):
        return _ORIGINAL_PRINT(*(_strip_icons(a) for a in args), **kwargs)

    plain_print._mc_city_plain = True
    builtins.print = plain_print


# 与扫描结果绑定的缓存文件。参数变化或 --rescan 时全部失效
_SCAN_CACHE_FILES = (
    SCAN_BLOCKS_NPY,
    SCAN_BLOCKS_COMPACT_NPY,
    BLOCK_CODEC_JSON,
    HEIGHT_MAP_NPY,
    SCAN_META_JSON,
)


def _read_scan_meta() -> dict | None:
    if not os.path.exists(SCAN_META_JSON):
        return None
    try:
        with open(SCAN_META_JSON) as f:
            return json.load(f)
    except Exception as e:
        print(f"[WARN] scan_meta.json 读取失败: {e}，视为缓存失效", flush=True)
        return None


def _write_scan_meta(params: dict):
    with open(SCAN_META_JSON, "w") as f:
        json.dump(params, f, indent=2)


def _invalidate_scan_caches(reason: str):
    print(f"🗑️  缓存失效（{reason}），清理:", flush=True)
    for p in _SCAN_CACHE_FILES:
        if os.path.exists(p):
            try:
                os.remove(p)
                print(f"     removed {os.path.basename(p)}", flush=True)
            except OSError as e:
                print(f"     [WARN] 删除 {p} 失败: {e}", flush=True)


def _cache_matches(params: dict) -> bool:
    """compact + codec + meta 三件齐全，且 meta 等于当前参数。"""
    if not (os.path.exists(SCAN_BLOCKS_COMPACT_NPY)
            and os.path.exists(BLOCK_CODEC_JSON)):
        return False
    saved = _read_scan_meta()
    if saved is None:
        return False
    return saved == params


def _load_or_scan(params: dict) -> tuple[np.ndarray, BlockCodec]:
    """返回 (scan_volume_compact_uint16, codec)。

    使用顺序：参数匹配的紧凑缓存 → 原始 npy（罕见，转换后用）→ 重新扫描。
    """
    x1, x2 = params["x1"], params["x2"]
    y1, y2 = params["y1"], params["y2"]
    z1, z2 = params["z1"], params["z2"]

    if _cache_matches(params):
        print(f"📦 缓存参数一致，加载 compact scan...", flush=True)
        scan_volume = np.load(SCAN_BLOCKS_COMPACT_NPY)
        codec = BlockCodec.load(BLOCK_CODEC_JSON)
        return scan_volume, codec

    raw = None
    if os.path.exists(SCAN_BLOCKS_NPY) and os.path.getsize(SCAN_BLOCKS_NPY) > 0:
        print(f"?? Found {SCAN_BLOCKS_NPY}, converting to compact...", flush=True)
        try:
            raw = np.load(SCAN_BLOCKS_NPY, allow_pickle=True)
        except Exception as exc:
            print(f"[WARN] raw scan cache broken, rescanning: {exc!r}", flush=True)
            try:
                os.remove(SCAN_BLOCKS_NPY)
            except OSError:
                pass
    elif os.path.exists(SCAN_BLOCKS_NPY):
        print("[WARN] raw scan cache is empty, rescanning...", flush=True)
        try:
            os.remove(SCAN_BLOCKS_NPY)
        except OSError:
            pass

    if raw is None:
        print("?? Scanning Minecraft region...", flush=True)
        scan_minecraft(x1, x2, y1, y2, z1, z2,
                       filename=os.path.splitext(SCAN_BLOCKS_NPY)[0])
        raw = np.load(SCAN_BLOCKS_NPY, allow_pickle=True)

    codec = BlockCodec()
    print("🔄 Converting to compact uint16 format...", flush=True)
    scan_volume = codec.convert_object_array(raw)
    del raw  # free ~7 GB immediately
    np.save(SCAN_BLOCKS_COMPACT_NPY, scan_volume)
    codec.save(BLOCK_CODEC_JSON)
    _write_scan_meta(params)
    print(f"✅ Compact scan saved. Memory: {scan_volume.nbytes // 1024 // 1024} MB",
          flush=True)
    return scan_volume, codec


def _load_or_compute_height_map(scan_volume: np.ndarray,
                                ctx: ScanContext,
                                codec: BlockCodec) -> np.ndarray:
    if os.path.exists(HEIGHT_MAP_NPY):
        print(f"📦 Found {HEIGHT_MAP_NPY}, loading...", flush=True)
        return np.load(HEIGHT_MAP_NPY, allow_pickle=True)

    height_map = generate_height_map(scan_volume, min_y=ctx.min_y, codec=codec)
    np.save(HEIGHT_MAP_NPY, height_map)
    print(f"✅ Height map saved: {HEIGHT_MAP_NPY}", flush=True)
    return height_map


def _cap_scan_region(x1: int, x2: int, z1: int, z2: int,
                     max_size: int) -> tuple[int, int, int, int, int, int]:
    """Return a centered detailed scan no larger than max_size in X/Z."""
    full_w = int(x2 - x1)
    full_h = int(z2 - z1)
    cap = max(1, int(max_size))
    scan_w = min(full_w, cap)
    scan_h = min(full_h, cap)
    cx = (int(x1) + int(x2)) // 2
    cz = (int(z1) + int(z2)) // 2
    sx1 = cx - scan_w // 2
    sz1 = cz - scan_h // 2
    return sx1, sx1 + scan_w, sz1, sz1 + scan_h, full_w, full_h


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="mc_city",
                                     description="Minecraft city generation")
    parser.add_argument("--rescan", action="store_true",
                        help="强制重新扫描，忽略并删除所有缓存")
    parser.add_argument("--at-player", action="store_true",
                        help="忽略 buildarea，以玩家当前位置为中心 ±256 扫描"
                             "（用 includeData 读 NBT 坐标）")
    parser.add_argument("--scan-radius", type=int, default=250,
                        help="--at-player 或无 buildarea 时的扫描半径；500 = 1000x1000")
    parser.add_argument("--max-scan-size", type=int,
                        default=MAX_DETAILED_SCAN_SIZE,
                        help="Detailed scan cap for large maps; visual scale still uses the full area.")
    parser.add_argument("--no-scan-cap", action="store_true",
                        help="Disable the large-map scan cap and scan the full requested area.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None):
    _install_plain_print()
    args = _parse_args(argv)
    host = DEFAULT_HOST

    steps = ["connect", "pause_world", "scan_world", "build_city", "resume_world"]
    bar = tqdm(total=len(steps), desc="World Generation",
               file=sys.stdout, mininterval=0.1, ascii=True)
    bar.refresh()

    # 0) 连接检查
    try:
        ver = getVersion(host=host)
        print(f"✅ Connected to GDMC HTTP Interface. Version: {ver}", flush=True)
    except Exception as e:
        print(f"❌ Cannot connect to GDMC HTTP Interface at {host}: {e}", flush=True)
        raise
    bar.update(1); bar.set_postfix_str("connected"); bar.refresh()

    # 扫描区域：优先用 /setbuildarea；不可用则 fallback 到第一个玩家位置 ±256
    SCAN_RADIUS = int(args.scan_radius)   # 250=500×500；500=1000×1000
    # SCAN_Y_MAX 必须高于最高山顶；不够高时 height_map 把那列标 sentinel，
    # 后续 organic ring BFS 会被切成多块，outer 圈可能扩不出去（见 2026-05 调试）。
    # 100 对应平原偏多的地图；山地/丘陵地图至少 150。
    SCAN_Y_MIN, SCAN_Y_MAX = -64, 150
    # --at-player 时直接跳过 buildarea；否则优先 buildarea，没设再退玩家中心。
    bx0 = None
    if not args.at_player:
        try:
            area = getBuildArea(host=host)
            bx0, raw_by0, bz0 = int(area.offset.x), int(area.offset.y), int(area.offset.z)
            bx1 = bx0 + int(area.size.x)
            bz1 = bz0 + int(area.size.z)
            by0 = max(raw_by0, SCAN_Y_MIN)
            by1 = min(raw_by0 + int(area.size.y), SCAN_Y_MAX)
            print(f"📐 buildarea: x=[{bx0},{bx1}) z=[{bz0},{bz1}) y=[{by0},{by1})",
                  flush=True)
        except BuildAreaNotSetError:
            bx0 = None                       # 落到下面的玩家中心

    if bx0 is None:
        if args.at_player:
            print("ℹ️  --at-player：忽略 buildarea，以玩家位置为中心 ±256 扫描...",
                  flush=True)
        else:
            print("ℹ️  未设置 buildarea，自动以玩家位置为中心扫描...", flush=True)
        try:
            # includeData=True 才带 NBT Pos；不带时 /players 只回 name+uuid，解析失败。
            players_raw = getPlayers(includeData=True, host=host)
        except Exception as e:
            print(f"❌ 获取玩家位置失败：{e}", flush=True)
            raise

        # /players 在不同版本可能返回 list 或 dict[uuid→...]，每条可能是
        # str(uuid)、dict（含 position/Pos）、或 dict 里 data 字段是 NBT 字符串。
        # 把所有可能的结构都试一遍。
        if not players_raw:
            print("❌ 服务器里没有玩家。请先进入 Minecraft 世界。", flush=True)
            raise RuntimeError("no players online")

        if isinstance(players_raw, dict):
            entries = list(players_raw.values())
        else:
            entries = list(players_raw)

        def _find_pos(entry):
            if not isinstance(entry, dict):
                return None
            for key in ("position", "Pos", "pos"):
                v = entry.get(key)
                if isinstance(v, (list, tuple)) and len(v) >= 3:
                    return v
            data = entry.get("data")
            if isinstance(data, dict):
                for key in ("Pos", "position", "pos"):
                    v = data.get(key)
                    if isinstance(v, (list, tuple)) and len(v) >= 3:
                        return v
            if isinstance(data, str):
                # NBT 字符串里找 Pos:[x.xd, y.yd, z.zd]
                import re
                m = re.search(r"Pos\s*:\s*\[\s*([-\d.]+)d?\s*,\s*([-\d.]+)d?"
                              r"\s*,\s*([-\d.]+)d?\s*\]", data)
                if m:
                    return [float(m.group(1)), float(m.group(2)), float(m.group(3))]
            return None

        pos = None
        for entry in entries:
            pos = _find_pos(entry)
            if pos is not None:
                break

        if pos is None:
            print(f"❌ 无法从 /players 响应解析玩家位置。原始返回：", flush=True)
            print(f"   {players_raw!r}", flush=True)
            raise RuntimeError("player position parse failed")
        px = int(float(pos[0]))
        pz = int(float(pos[2]))

        bx0, bx1 = px - SCAN_RADIUS, px + SCAN_RADIUS
        bz0, bz1 = pz - SCAN_RADIUS, pz + SCAN_RADIUS
        by0, by1 = SCAN_Y_MIN, SCAN_Y_MAX
        print(f"📐 玩家中心 ({px},{pz}) → scan x=[{bx0},{bx1}) z=[{bz0},{bz1}) "
              f"y=[{by0},{by1})", flush=True)

    full_w = int(bx1 - bx0)
    full_h = int(bz1 - bz0)
    requested_w, requested_h = full_w, full_h
    if not args.no_scan_cap:
        capped = _cap_scan_region(bx0, bx1, bz0, bz1, args.max_scan_size)
        nbx0, nbx1, nbz0, nbz1, requested_w, requested_h = capped
        if (nbx0, nbx1, nbz0, nbz1) != (bx0, bx1, bz0, bz1):
            print(
                f"Large build area detected ({full_w}x{full_h}); "
                f"detailed scan capped to {(nbx1 - nbx0)}x{(nbz1 - nbz0)} "
                f"around the same center. Use --no-scan-cap to force full scan.",
                flush=True,
            )
            bx0, bx1, bz0, bz1 = nbx0, nbx1, nbz0, nbz1

    scan_params = {
        "x1": bx0, "x2": bx1,
        "y1": by0, "y2": by1,
        "z1": bz0, "z2": bz1,
    }

    ctx = ScanContext(origin_x=scan_params["x1"],
                      origin_z=scan_params["z1"],
                      min_y=scan_params["y1"])
    ctx.requested_build_size = (int(requested_w), int(requested_h))
    ctx.detailed_scan_size = (int(bx1 - bx0), int(bz1 - bz0))

    # 缓存有效性判断
    if args.rescan:
        _invalidate_scan_caches("--rescan")
    else:
        saved = _read_scan_meta()
        if saved is None and os.path.exists(SCAN_BLOCKS_COMPACT_NPY) \
                and os.path.exists(BLOCK_CODEC_JSON):
            # 一次性迁移：旧缓存没有 meta 文件，先按当前参数写一份
            print("ℹ️  现有 compact 缓存缺少 scan_meta.json，按当前参数补写。"
                  "如果参数其实变过，请用 --rescan 重扫。", flush=True)
            _write_scan_meta(scan_params)
        elif saved is not None and saved != scan_params:
            _invalidate_scan_caches(
                f"扫描参数变化: saved={saved} vs current={scan_params}")

    try:
        # 1) 暂停世界（gamerules）
        print("⏸️  Applying gamerules...", flush=True)
        pause_world(host=host)
        bar.update(1); bar.set_postfix_str("paused"); bar.refresh()

        # 2) 扫描 / 加载缓存
        scan_volume, codec = _load_or_scan(scan_params)
        height_map = _load_or_compute_height_map(scan_volume, ctx, codec)
        bar.update(1); bar.set_postfix_str("scanned"); bar.refresh()

        # 3) 地形分析 + 戏剧性选址（卡 1 + 卡 2）
        # 卡 10.2：按 build area 尺寸派生城市半径，挂 ctx 供选址/圈层/grid 消费。
        # 必须在 find_dramatic_center 之前——选址的 edge_margin 要读 city_dims。
        NZ, NX = height_map.shape
        ctx.city_dims = compute_city_dims(NX, NZ)
        req_w, req_h = getattr(ctx, "requested_build_size", (NX, NZ))
        if LARGE_BUILDAREA_VISUALS_ENABLED and min(req_w, req_h) > min(NX, NZ):
            ctx.visual_city_dims = compute_city_dims(req_w, req_h)
            print(f"   visual scale from full area {req_w}x{req_h}: "
                  f"{ctx.visual_city_dims}", flush=True)
        else:
            ctx.visual_city_dims = ctx.city_dims
        print(f"   城市尺寸 (build {NX}x{NZ}): {ctx.city_dims}", flush=True)

        print("🗺️  Analyzing terrain features...", flush=True)
        features = analyze_terrain(ctx, height_map, scan_volume, codec=codec)
        ctx.terrain_features = features  # 下游卡 3/4/5 复用，避免重算

        print("🔍 Finding dramatic city center...", flush=True)
        center_x, center_y, center_z = find_dramatic_center(ctx, features)
        print(f"   选定中心 world=({center_x},{center_y},{center_z})", flush=True)

        # 4) 建城（build_city 内部还是用 (center_x, center_z)，cy 仅日志用）
        print("🌆 Building city...", flush=True)
        build_city(center_x, center_z, height_map, scan_volume, ctx, codec)
        bar.update(1); bar.set_postfix_str("city built"); bar.refresh()

    finally:
        # 5) 恢复 gamerules
        print("▶️  Restoring gamerules...", flush=True)
        try:
            resume_world(host=host)
        except Exception as e:
            print(f"[WARN] resume_world failed: {e}", flush=True)

        bar.update(1); bar.set_postfix_str("done"); bar.refresh()
        bar.close()

    print("✅ Done!", flush=True)


if __name__ == "__main__":
    main()
