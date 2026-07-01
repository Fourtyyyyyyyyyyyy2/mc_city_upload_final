"""把 Litematica 投影文件 (.litematic) 转成 component npy（与手扫的同构）。

投影 mod 存的是 gzip 压缩的 NBT（palette + 打包索引），管线只认 np.load 出来的
(NY, NZ, NX) object 数组，每格 {"id": "minecraft:xxx[state]"}。本模块只做格式转换，
转完直接复用 building_scan.save_component / trim_to_solid / strip_ground，
codec / paste_volume / door-front sidecar / --rescan 全不动。

用法（PowerShell 反引号续行）：
  python -m mc_city.scan.litematic_import --file path\to\house.litematic `
      --ring inner --guild scholars --name main_hall_01 `
      --front N --door 12 0 0
  python -m mc_city.scan.litematic_import --file x.litematic --name h1 --info   # 只看尺寸不存

  --ring / --guild / --terrain / --name / --front / --door / --no-trim / --strip-ground
  含义与 building_scan 完全一致。多 region 投影会按 schematic 全局包围盒合并。
"""
from __future__ import annotations

import argparse
import os

import numpy as np

from ..config import COMPONENT_ROOT, FRONT_DIRS, GUILD_NAMES, RING_NAMES
from .building_scan import save_component, strip_ground, trim_to_solid

_AIR = "minecraft:air"


def _patch_entity_tolerance() -> None:
    """litemapy 0.11b 读「有内饰」投影会因实体 Rotation 存成 Byte 而崩 load。

    我们只要方块、不要实体：让 Entity.from_nbt 在坐标损坏时只保留 id 退化，
    使 Schematic.load 不再整文件失败。幂等，重复调用无副作用。
    """
    from litemapy import minecraft as lmc
    if getattr(lmc.Entity.from_nbt, "_mc_city_patched", False):
        return
    orig = lmc.Entity.from_nbt

    def safe_from_nbt(nbt):
        try:
            return orig(nbt)
        except Exception as exc:
            print(f"[WARN] litematic entity downgraded: {exc!r}")
            try:
                return lmc.Entity(str(nbt.get("id", "minecraft:area_effect_cloud")))
            except Exception as exc2:
                print(f"[WARN] litematic entity fallback id failed: {exc2!r}")
                return lmc.Entity("minecraft:area_effect_cloud")

    safe_from_nbt._mc_city_patched = True
    lmc.Entity.from_nbt = staticmethod(safe_from_nbt)


def litematic_to_volume(path: str) -> np.ndarray:
    """读 .litematic → (NY, NZ, NX) object 数组，每格 {"id": blockstate_id}。

    多 region 时按 schematic 全局坐标合并到一个包围盒；空隙补 air。
    """
    from litemapy import Schematic  # 局部 import：没装库时只有用到才报错
    _patch_entity_tolerance()

    schem = Schematic.load(path)
    regions = list(schem.regions.values())
    if not regions:
        raise ValueError(f"{path} 里没有任何 region")

    # 全局包围盒（schematic 坐标系：local + region 偏移）。
    gx0 = min(r.min_schem_x() for r in regions)
    gx1 = max(r.max_schem_x() for r in regions)
    gy0 = min(r.min_schem_y() for r in regions)
    gy1 = max(r.max_schem_y() for r in regions)
    gz0 = min(r.min_schem_z() for r in regions)
    gz1 = max(r.max_schem_z() for r in regions)

    NX, NY, NZ = gx1 - gx0 + 1, gy1 - gy0 + 1, gz1 - gz0 + 1
    volume = np.empty((NY, NZ, NX), dtype=object)
    for idx in np.ndindex(volume.shape):       # 先全填 air（独立 dict，不共享）
        volume[idx] = {"id": _AIR}

    for r in regions:
        ox, oy, oz = r.x, r.y, r.z              # region 在 schematic 里的偏移
        for x in r.range_x():
            for y in r.range_y():
                for z in r.range_z():
                    bid = r[x, y, z].to_block_state_identifier()
                    if bid == _AIR:
                        continue
                    iy, iz, ix = (y + oy) - gy0, (z + oz) - gz0, (x + ox) - gx0
                    volume[iy, iz, ix] = {"id": bid}
    return volume


def _parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="mc_city.scan.litematic_import",
        description="Litematica 投影 (.litematic) → component npy")
    p.add_argument("--file", required=True, help="输入 .litematic 路径")
    p.add_argument("--info", action="store_true", help="只打印尺寸，不存盘")
    p.add_argument("--ring", default="mid", choices=RING_NAMES,
                   help="inner/mid/outer")
    p.add_argument("--terrain", default="plains",
                   help="plains/desert/snow/mountain/water（无 --guild 时用）")
    p.add_argument("--guild", default=None, choices=GUILD_NAMES,
                   help="给定则存到 <ring>_<guild>/（与 --terrain 二选一）")
    p.add_argument("--name", default=None,
                   help="文件名（不含 .npy）；默认用投影文件名")
    p.add_argument("--front", default=None, choices=FRONT_DIRS,
                   help="楼正面朝向 N/S/E/W（写入 .json sidecar）")
    p.add_argument("--door", type=int, nargs=3, default=None,
                   metavar=("DX", "DY", "DZ"),
                   help="门方块的体素 local 坐标（裁后 npy 起点起算）")
    p.add_argument("--no-trim", dest="trim", action="store_false",
                   help="不裁剪外壳空气")
    p.add_argument("--strip-ground", dest="strip_ground", action="store_true",
                   help="剥掉楼外能够到的草/土/水（保留围死的内部水景）")
    return p.parse_args(argv)


def main(argv=None) -> None:
    args = _parse_args(argv)
    name = args.name or os.path.splitext(os.path.basename(args.file))[0]
    group = args.guild if args.guild else args.terrain
    print(f"🧩 导入投影 {args.file} → {args.ring}_{group}/{name}")

    volume = litematic_to_volume(args.file)
    if args.trim:
        volume = trim_to_solid(volume)
    if args.strip_ground:
        volume = strip_ground(volume)
        if args.trim:
            volume = trim_to_solid(volume)
    NY, NZ, NX = volume.shape

    if args.info:
        print(f"   尺寸 footprint X×Z = {NX}×{NZ}，高 Y = {NY}（--info 未存盘）")
        if NX > 28 or NZ > 28:
            print("   ⚠️ footprint > 28，塞不进 30×30 街区")
        return

    door = tuple(args.door) if args.door else None
    save_component(volume, args.ring, group, name,
                   front=args.front, door=door)


if __name__ == "__main__":
    main()
