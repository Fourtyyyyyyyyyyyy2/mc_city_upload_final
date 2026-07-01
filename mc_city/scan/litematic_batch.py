"""批量把一个文件夹里的 .litematic 投影转成 component npy。

对每个 .litematic 调 litematic_to_volume → trim/strip → save_component，
存进 components/<ring>_<guild>/（或 <ring>_<terrain>/）。超尺寸的自动跳过并列出，
不污染街区池。文件名自动清掉「【建筑机器wW】」前缀和首尾空白当 component 名。

用法（PowerShell 反引号续行）：
  python -m mc_city.scan.litematic_batch --dir "建筑合集" --ring mid --guild merchants
  python -m mc_city.scan.litematic_batch --dir "建筑合集" --ring outer --guild merchants `
      --strip-ground --max-size 28
  python -m mc_city.scan.litematic_batch --dir "建筑合集" --guild merchants --dry-run  # 只看清单不存盘

  --max-size N   footprint(X 或 Z) > N 的跳过（默认 28，对应 30×30 街区）
  --dry-run      只解析+打印尺寸/会跳过谁，不写 npy
  其余 --ring/--guild/--terrain/--strip-ground/--no-trim 与单文件导入一致
"""
from __future__ import annotations

import argparse
import os
import re

from ..config import BLOCK_SIZE, GUILD_NAMES, NEXT_ROAD_WIDTH, RING_NAMES
from .building_scan import save_component, strip_ground, trim_to_solid
from .litematic_import import litematic_to_volume

# 默认尺寸上限对齐 block_placement._try_claim_2x2 的 2×2 合并能力：
# 一栋大楼可占同公会 2×2 街区簇，有效地块 ≈ 2*BLOCK_SIZE + NEXT_ROAD_WIDTH。
# 超此值的（破船/木塔/五亭桥等地标）才真放不进网格，应单独当地标摆。
_MERGE_CEILING = 2 * BLOCK_SIZE + NEXT_ROAD_WIDTH

# 文件名清洗：去前缀、去扩展名、压空白；保留括号（区分 有/无内饰 版本，避免重名覆盖）
_PREFIX_RE = re.compile(r"^【[^】]*】\s*")
_BAD_CHARS_RE = re.compile(r'[\\/:*?"<>|]')


def _clean_name(filename: str) -> str:
    name = os.path.splitext(filename)[0]
    name = _PREFIX_RE.sub("", name)
    name = _BAD_CHARS_RE.sub("_", name)
    return name.strip().replace(" ", "_") or "building"


def batch_import(folder: str, ring: str, group: str,
                 trim: bool = True, do_strip: bool = False,
                 max_size: int = _MERGE_CEILING, dry_run: bool = False) -> dict:
    """转 folder 下所有 .litematic。返回 {"ok":[...], "skipped":[...], "failed":[...]}。"""
    files = sorted(f for f in os.listdir(folder) if f.lower().endswith(".litematic"))
    result = {"ok": [], "skipped": [], "failed": []}
    print(f"📂 {folder} 下找到 {len(files)} 个 .litematic → {ring}_{group}/"
          f"（max_size={max_size}{'，dry-run' if dry_run else ''}）")

    for fn in files:
        path = os.path.join(folder, fn)
        name = _clean_name(fn)
        try:
            volume = litematic_to_volume(path)
            if trim:
                volume = trim_to_solid(volume)
            if do_strip:
                volume = strip_ground(volume)
                if trim:
                    volume = trim_to_solid(volume)
        except Exception as e:                 # 单个文件坏不该中断整批
            print(f"  ❌ {fn}：{e}")
            result["failed"].append((fn, str(e)))
            continue

        NY, NZ, NX = volume.shape
        if NX > max_size or NZ > max_size:
            print(f"  ⏭️  跳过 {fn}：footprint {NX}×{NZ} > {max_size}")
            result["skipped"].append((fn, (NX, NZ)))
            continue

        if dry_run:
            print(f"  ✓ {name}  {NX}×{NZ}×{NY}")
            result["ok"].append((name, (NX, NZ, NY)))
            continue

        save_component(volume, ring, group, name)
        result["ok"].append((name, (NX, NZ, NY)))

    print(f"\n📊 完成：转 {len(result['ok'])}，跳过 {len(result['skipped'])}，"
          f"失败 {len(result['failed'])}")
    if result["skipped"]:
        print("   超尺寸（需单独处理）：")
        for fn, (nx, nz) in result["skipped"]:
            print(f"     {nx}×{nz}  {fn}")
    return result


def _parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="mc_city.scan.litematic_batch",
        description="批量 Litematica 投影 → component npy")
    p.add_argument("--dir", required=True, help="装 .litematic 的文件夹")
    p.add_argument("--ring", default="mid", choices=RING_NAMES)
    p.add_argument("--terrain", default="plains",
                   help="无 --guild 时用 plains/desert/snow/mountain/water")
    p.add_argument("--guild", default=None, choices=GUILD_NAMES,
                   help="给定则存到 <ring>_<guild>/")
    p.add_argument("--max-size", type=int, default=_MERGE_CEILING,
                   help=f"footprint > 此值则跳过（默认 {_MERGE_CEILING}=2×2合并上限）")
    p.add_argument("--no-trim", dest="trim", action="store_false",
                   help="不裁剪外壳空气")
    p.add_argument("--strip-ground", dest="strip_ground", action="store_true",
                   help="剥掉楼外能够到的草/土/水")
    p.add_argument("--dry-run", action="store_true",
                   help="只解析打印尺寸，不写 npy")
    return p.parse_args(argv)


def main(argv=None) -> None:
    args = _parse_args(argv)
    group = args.guild if args.guild else args.terrain
    batch_import(args.dir, args.ring, group,
                 trim=args.trim, do_strip=args.strip_ground,
                 max_size=args.max_size, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
