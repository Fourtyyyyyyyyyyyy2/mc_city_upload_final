"""扫描手工搭的单栋建筑 → components/<ring>_<group>/<name>.npy + .json sidecar。

paste_volume 会自动对齐地面 + 裁空气，扫描时把整栋楼连地板框进去即可。

用法（PowerShell 反引号续行；Bash/cmd 把反引号换成反斜杠）：
  # 1) 画框确认
  python -m mc_city.scan.building_scan --box X1 Y1 Z1 X2 Y2 Z2 --mark
  # 2) 正式扫描存盘
  python -m mc_city.scan.building_scan --box X1 Y1 Z1 X2 Y2 Z2 `
      --ring inner --guild scholars --name main_hall_01 `
      --front N --door 12 0 0
  # 3) 清掉玻璃框
  python -m mc_city.scan.building_scan --box X1 Y1 Z1 X2 Y2 Z2 --unmark

  --ring   inner / mid / outer
  --guild  scholars / engineers / merchants / adventurers
           （不填则用 --terrain plains/desert/snow/mountain/water 走地形池）
  --front  N/S/E/W：楼正面朝向（paste 旋转用，当前 paste_volume 不读，先存数据）
  --door   dx dy dz：门方块在裁后体素的相对坐标（npy 的 (0,0,0) 角起算）
  --no-trim 关闭"裁外壳空气"

规格速查（每公会 1 主殿 + 3 大宅 + 3 民居）：
  ring    footprint(X×Z)  height(Y)
  inner   24-28²          12-20
  mid     20-24²           8-14
  outer   14-20²           5-9
footprint > 28 会被警告（30×30 街区会稀疏化邻块）。
"""
from __future__ import annotations

import argparse
import json
import os
from typing import Iterable

import numpy as np

from ..config import (COMPONENT_ROOT, DEFAULT_HOST, FRONT_DIRS, GUILD_NAMES,
                      RING_NAMES)
from ..mc.placement import set_blocks_batch
from .scanner import _BLOCK_SIZE, get_cube

_AIR_IDS = {"minecraft:air", "minecraft:cave_air", "minecraft:void_air"}
_MARK_BATCH = 1000

# --strip-ground 剥除目标：楼外的地表/植被/水。从体素外壳 BFS 穿"空气+这些"，
# 凡能从外面够到的就清成空气 → 去掉草坪/泥土/外部水池，被墙围死的内部水景保留。
_GROUND_IDS = {
    "minecraft:grass_block", "minecraft:dirt", "minecraft:coarse_dirt",
    "minecraft:rooted_dirt", "minecraft:podzol", "minecraft:mud",
    "minecraft:sand", "minecraft:red_sand", "minecraft:gravel",
    "minecraft:water", "minecraft:snow", "minecraft:snow_block",
    "minecraft:short_grass", "minecraft:grass", "minecraft:tall_grass",
    "minecraft:fern", "minecraft:large_fern", "minecraft:dead_bush",
    "minecraft:seagrass", "minecraft:tall_seagrass",
    "minecraft:kelp", "minecraft:kelp_plant",
}


def _norm_box(box: Iterable[int]) -> tuple:
    """6 个坐标 → (x1,y1,z1,x2,y2,z2) 规范化为 min/max 闭区间。"""
    x1, y1, z1, x2, y2, z2 = box
    return (min(x1, x2), min(y1, y2), min(z1, z2),
            max(x1, x2), max(y1, y2), max(z1, z2))


def _block_id(cell) -> str:
    return cell.get("id", "minecraft:air") if isinstance(cell, dict) else str(cell)


def scan_to_volume(box: tuple, host: str = DEFAULT_HOST) -> np.ndarray:
    """扫描闭区间盒子，返回 (NY, NZ, NX) object 数组（元素 {"id": ...}）。"""
    x1, y1, z1, x2, y2, z2 = _norm_box(box)
    NX, NY, NZ = x2 - x1 + 1, y2 - y1 + 1, z2 - z1 + 1
    volume = np.empty((NY, NZ, NX), dtype=object)
    # 不 fill({"id":air}) — 那会让所有元素共享同一个 dict 对象（潜伏雷）。
    # 下面的循环覆盖所有格子，初始化交给覆盖。
    for Y in range(y1, y2 + 1, _BLOCK_SIZE):
        for Z in range(z1, z2 + 1, _BLOCK_SIZE):
            for X in range(x1, x2 + 1, _BLOCK_SIZE):
                dx = min(_BLOCK_SIZE, x2 + 1 - X)
                dy = min(_BLOCK_SIZE, y2 + 1 - Y)
                dz = min(_BLOCK_SIZE, z2 + 1 - Z)
                cube = get_cube(X, Y, Z, dx, dy, dz, host=host,
                                include_state=True)
                for iy in range(dy):
                    for iz in range(dz):
                        for ix in range(dx):
                            bid = cube[iy][iz][ix]
                            volume[(Y + iy) - y1, (Z + iz) - z1, (X + ix) - x1] = \
                                bid if isinstance(bid, dict) else {"id": bid}
    return volume


def trim_to_solid(volume: np.ndarray) -> np.ndarray:
    """裁掉三轴上全空气的外壳，使 footprint 紧贴实心包围盒。无实心则原样返回。"""
    solid = np.zeros(volume.shape, dtype=bool)
    for idx, cell in np.ndenumerate(volume):
        solid[idx] = _block_id(cell) not in _AIR_IDS
    if not solid.any():
        print("  ⚠️ 框内全是空气，未裁剪（检查坐标）")
        return volume
    ys, zs, xs = np.where(solid)
    return volume[ys.min():ys.max() + 1,
                  zs.min():zs.max() + 1,
                  xs.min():xs.max() + 1]


def _base_id(cell) -> str:
    """方块基名（去掉 [state]）。"""
    return _block_id(cell).split("[", 1)[0]


def strip_ground(volume: np.ndarray) -> np.ndarray:
    """剥掉从体素外壳能够到的地表/植被/水（楼外草坪、泥土、外部水池）。

    6 邻接 BFS：种子=所有边界格，可穿行集合={空气, _GROUND_IDS}。被建筑实心墙
    围死的内部水景/花园够不到 → 保留。原地把够到的 _GROUND_IDS 格清成空气。
    """
    from collections import deque
    NY, NZ, NX = volume.shape
    visited = np.zeros((NY, NZ, NX), dtype=bool)
    dq = deque()

    def _passable(y, z, x) -> bool:
        b = _base_id(volume[y, z, x])
        return b in _AIR_IDS or b in _GROUND_IDS

    for y in range(NY):                       # 边界种子
        for z in range(NZ):
            for x in range(NX):
                if not (x in (0, NX - 1) or y in (0, NY - 1) or z in (0, NZ - 1)):
                    continue
                if not visited[y, z, x] and _passable(y, z, x):
                    visited[y, z, x] = True
                    dq.append((y, z, x))

    while dq:                                  # 泛洪
        y, z, x = dq.popleft()
        for dy, dz, dx in ((1, 0, 0), (-1, 0, 0), (0, 1, 0),
                           (0, -1, 0), (0, 0, 1), (0, 0, -1)):
            ny, nz, nx = y + dy, z + dz, x + dx
            if not (0 <= ny < NY and 0 <= nz < NZ and 0 <= nx < NX):
                continue
            if visited[ny, nz, nx] or not _passable(ny, nz, nx):
                continue
            visited[ny, nz, nx] = True
            dq.append((ny, nz, nx))

    stripped = 0                               # 清掉够到的地表
    for y in range(NY):
        for z in range(NZ):
            for x in range(NX):
                if visited[y, z, x] and _base_id(volume[y, z, x]) in _GROUND_IDS:
                    volume[y, z, x] = {"id": "minecraft:air"}
                    stripped += 1
    print(f"  🧹 strip-ground 清除楼外地表 {stripped} 格（保留围死的内部水景）")
    return volume


def save_component(volume: np.ndarray, ring: str, group: str, name: str,
                   front: str | None = None,
                   door: tuple[int, int, int] | None = None) -> str:
    """存到 components/<ring>_<group>/<name>.npy（+ .json sidecar 存 front/door 元数据）。

    front: N/S/E/W；door: 裁后体素 (dx,dy,dz) local 坐标。任一为 None 则不写该字段。
    """
    folder = os.path.join(COMPONENT_ROOT, f"{ring}_{group}")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{name}.npy")
    np.save(path, volume, allow_pickle=True)
    NY, NZ, NX = volume.shape

    meta: dict = {}
    if front is not None:
        meta["front"] = front
    if door is not None:
        meta["door"] = list(door)
    if meta:
        meta_path = os.path.join(folder, f"{name}.json")
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

    print(f"✅ 已保存 {path}")
    print(f"   尺寸 footprint X×Z = {NX}×{NZ}，高 Y = {NY}")
    if meta:
        print(f"   元数据 {meta}")
    if NX > 28 or NZ > 28:
        print(f"   ⚠️ footprint > 28，塞不进 30×30 街区（会被碰撞框稀疏化邻块）")
    return path


def load_component_meta(npy_path: str) -> dict:
    """读取 <name>.npy 旁的 <name>.json sidecar。不存在返回空 dict。"""
    meta_path = os.path.splitext(npy_path)[0] + ".json"
    if not os.path.isfile(meta_path):
        return {}
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"  ⚠️ 读 {meta_path} 失败：{e}")
        return {}


def list_components(root: str = COMPONENT_ROOT) -> dict:
    """扫 components/ 下所有 <ring>_<group>/<name>.npy → 嵌套 dict。

    返回 {(ring, group): [{"name": str, "path": str, "shape": (Y,Z,X), "meta": {...}}, ...]}
    shape 为 None 表示读盘失败。group 可以是 guild 名或 terrain 名。
    """
    result: dict = {}
    if not os.path.isdir(root):
        return result
    for folder in sorted(os.listdir(root)):
        folder_path = os.path.join(root, folder)
        if not os.path.isdir(folder_path) or "_" not in folder:
            continue
        ring, _, group = folder.partition("_")
        entries = []
        for fn in sorted(os.listdir(folder_path)):
            if not fn.endswith(".npy"):
                continue
            npy_path = os.path.join(folder_path, fn)
            name = fn[:-4]
            try:
                vol = np.load(npy_path, allow_pickle=True)
                shape = tuple(vol.shape)
            except (OSError, ValueError) as e:
                print(f"  ⚠️ 读 {npy_path} 失败：{e}")
                shape = None
            entries.append({
                "name": name,
                "path": npy_path,
                "shape": shape,
                "meta": load_component_meta(npy_path),
            })
        if entries:
            result[(ring, group)] = entries
    return result


def mark_box(box: tuple, block_id: str = "minecraft:glass",
             host: str = DEFAULT_HOST) -> int:
    """用方块画出盒子 12 条棱，进游戏确认框选范围。返回放置块数。"""
    x1, y1, z1, x2, y2, z2 = _norm_box(box)
    payload: list[dict] = []
    total = 0
    for x in range(x1, x2 + 1):
        for y in range(y1, y2 + 1):
            for z in range(z1, z2 + 1):
                edges = ((x in (x1, x2)) + (y in (y1, y2)) + (z in (z1, z2)))
                if edges < 2:                 # 棱 = 至少两个坐标在边界上
                    continue
                payload.append({"x": x, "y": y, "z": z, "id": block_id})
                if len(payload) >= _MARK_BATCH:
                    if set_blocks_batch(payload):
                        total += len(payload)
                    payload = []
    if payload and set_blocks_batch(payload):
        total += len(payload)
    print(f"✅ 已画框 {total} 块（{block_id}）")
    return total


def _parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="mc_city.scan.building_scan",
        description="扫描单栋建筑存为 component npy")
    p.add_argument("--box", type=int, nargs=6, required=True,
                   metavar=("X1", "Y1", "Z1", "X2", "Y2", "Z2"),
                   help="两个对角坐标（闭区间，F3 读）")
    p.add_argument("--mark", action="store_true", help="只画玻璃框确认，不扫描")
    p.add_argument("--unmark", action="store_true", help="把框清成空气")
    p.add_argument("--ring", default="mid", choices=RING_NAMES,
                   help="inner/mid/outer")
    p.add_argument("--terrain", default="plains",
                   help="plains/desert/snow/mountain/water（无 --guild 时用）")
    p.add_argument("--guild", default=None, choices=GUILD_NAMES,
                   help="给定则存到 <ring>_<guild>/（与 --terrain 二选一）")
    p.add_argument("--name", default="building", help="文件名（不含 .npy）")
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
    box = tuple(args.box)
    if args.unmark:
        mark_box(box, block_id="minecraft:air")
        return
    if args.mark:
        mark_box(box)
        return
    group = args.guild if args.guild else args.terrain
    print(f"🔍 扫描建筑 box={_norm_box(box)} → {args.ring}_{group}/{args.name}")
    volume = scan_to_volume(box)
    if args.trim:
        volume = trim_to_solid(volume)
    if args.strip_ground:
        volume = strip_ground(volume)
        if args.trim:
            volume = trim_to_solid(volume)        # 剥地表后再收紧外壳
    door = (args.door[0], args.door[1], args.door[2]) if args.door else None
    save_component(volume, args.ring, group, args.name,
                   front=args.front, door=door)


if __name__ == "__main__":
    main()
