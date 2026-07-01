"""通过 GDMC HTTP 接口批量放置方块。

set_blocks_batch:  一次性 PUT 一批方块
apply_volume:      把 (Y,Z,X) 方块数组按 origin/rotation 应用到世界
paste_volume:      从 .npy 加载并粘贴一栋建筑（自动对齐底面）
"""
import os
import time
from typing import Iterable

import numpy as np
import requests

from ..config import DEFAULT_HOST

BATCH_SIZE = 1024
HTTP_TIMEOUT = 60.0      # 单次 PUT 等待秒数；大批量方块在 GDMC 服务端处理较慢
HTTP_MAX_RETRIES = 3     # 总尝试次数 = 1 + 重试


def set_blocks_batch(blocks: list, host: str = DEFAULT_HOST,
                     timeout: float = HTTP_TIMEOUT,
                     max_retries: int = HTTP_MAX_RETRIES,
                     do_block_updates: bool = True) -> bool:
    """批量 PUT 方块；失败按指数退避重试。

    返回 True 仅当 HTTP 200。调用方必须在 True 之后再去同步内存状态，
    否则会出现"以为清掉了实际没清"的内存/世界不一致。

    do_block_updates=False：放块时不触发邻块物理更新 → 水/岩浆不外流、门不掉、
    红石不联动，结构原样落地（建筑 paste 用，防水景泛滥）。
    """
    for b in blocks:
        if "x" in b: b["x"] = int(b["x"])
        if "y" in b: b["y"] = int(b["y"])
        if "z" in b: b["z"] = int(b["z"])

    url = host + "/blocks"
    if not do_block_updates:
        url += "?doBlockUpdates=false"
    for attempt in range(1, max_retries + 1):
        try:
            r = requests.put(url, json=blocks, timeout=timeout)
            if r.status_code == 200:
                return True
            print(f"[FAIL BATCH] attempt {attempt}/{max_retries} "
                  f"status={r.status_code} - {r.text[:200]}")
        except Exception as e:
            print(f"[RETRY {attempt}/{max_retries}] {e}")
        if attempt < max_retries:
            time.sleep(2 ** (attempt - 1))  # 1s, 2s, 4s, ...
    print(f"[GIVE UP] set_blocks_batch 在 {max_retries} 次尝试后仍失败 "
          f"(batch 大小 {len(blocks)})")
    return False


# 绕 Y 轴旋转时方块朝向的映射（与下方位置变换 dx,dz 同向：本实现 90°=逆时针）。
# facing：门/活板门/楼梯/梯子等；axis：原木/柱（90/270 时 x<->z）。
_FACING_ROT = {
    90:  {"north": "west", "west": "south", "south": "east", "east": "north"},
    180: {"north": "south", "south": "north", "east": "west", "west": "east"},
    270: {"north": "east", "east": "south", "south": "west", "west": "north"},
}


def _rotate_block_id(block_id: str, rotation: int) -> str:
    """按 rotation 旋转方块状态里的 facing / axis。

    block_id 形如 'minecraft:oak_door[facing=east,half=lower,hinge=left,...]'。
    无 '[' 状态或 rotation==0 原样返回（向后兼容旧的无状态 npy）。
    hinge(left/right) 相对 facing 不变 → 不动；只转 facing 和 axis。
    """
    if rotation == 0 or "[" not in block_id:
        return block_id
    fmap = _FACING_ROT.get(rotation)
    if fmap is None:
        return block_id
    base, props_str = block_id.split("[", 1)
    props_str = props_str.rstrip("]")
    out = []
    for kv in props_str.split(","):
        if "=" not in kv:
            out.append(kv)
            continue
        k, v = kv.split("=", 1)
        if k == "facing" and v in fmap:
            v = fmap[v]
        elif k == "axis" and rotation in (90, 270) and v in ("x", "z"):
            v = "z" if v == "x" else "x"
        out.append(f"{k}={v}")
    return f"{base}[{','.join(out)}]"


def apply_volume(volume: np.ndarray, origin: tuple,
                 rotation: int = 0, skip_air: bool = True,
                 host: str = DEFAULT_HOST,
                 do_block_updates: bool = False):
    """把 (NY, NZ, NX) 形状的体素数组按 origin 写入世界。

    rotation 仅支持 0/90/180/270（绕 Y 轴）。
    do_block_updates 默认 False：结构原样落地，水景不外流（见 set_blocks_batch）。
    """
    ox, oy, oz = origin
    NY, NZ, NX = volume.shape

    batch = []
    for y in range(NY):
        for z in range(NZ):
            for x in range(NX):
                block = volume[y, z, x]
                block_id = block.get("id", "minecraft:air") if isinstance(block, dict) else str(block)

                if skip_air and block_id == "minecraft:air":
                    continue

                block_id = _rotate_block_id(block_id, rotation)

                if rotation == 0:
                    dx, dz = x, z
                elif rotation == 90:
                    dx = z
                    dz = NX - 1 - x
                elif rotation == 180:
                    dx = NX - 1 - x
                    dz = NZ - 1 - z
                elif rotation == 270:
                    dx = NZ - 1 - z
                    dz = x
                else:
                    raise ValueError("rotation must be 0/90/180/270")

                batch.append({
                    "x": ox + dx,
                    "y": oy + y,
                    "z": oz + dz,
                    "id": block_id,
                })

                if len(batch) >= BATCH_SIZE:
                    set_blocks_batch(batch, host=host,
                                     do_block_updates=do_block_updates)
                    batch = []

    if batch:
        set_blocks_batch(batch, host=host,
                         do_block_updates=do_block_updates)


def _remap_volume_ids(volume, block_remap) -> None:
    """就地把 volume 里每个方块的 id 过一遍 block_remap(bid)->bid（保留状态由调用方负责）。

    block_remap 是个纯函数；返回值与原 id 相同则不动。仅用于 paste_volume 的可选
    材质重映射（reskin），不传则完全不调用，零行为变化。
    """
    NY0, NZ0, NX0 = volume.shape
    for yy in range(NY0):
        for zz in range(NZ0):
            for xx in range(NX0):
                b = volume[yy, zz, xx]
                if not isinstance(b, dict):
                    continue
                old = b.get("id", "minecraft:air")
                new = block_remap(old)
                if new != old:
                    nb = dict(b)
                    nb["id"] = new
                    volume[yy, zz, xx] = nb


def paste_volume(npy_path: str,
                 origin: tuple = (0, 10, 0),
                 clear_target: bool = True,
                 skip_air: bool = True,
                 rotation: int = 0,
                 host: str = DEFAULT_HOST,
                 block_remap=None):
    """加载 .npy 建筑并粘贴。origin Y 表示期望的地面高度。

    自动找最低实心层 (base_y) 并对齐到 origin_y，建筑不会陷地或漂浮。
    block_remap：可选 bid->bid 函数（reskin 材质重映射），None=零行为变化。
    """
    volume = np.load(npy_path, allow_pickle=True)
    if block_remap is not None:
        _remap_volume_ids(volume, block_remap)
    NY, NZ, NX = volume.shape
    tx1, ty1_input, tz1 = origin

    base_y = 0
    for y in range(NY):
        layer = volume[y, :, :]
        has_solid = False
        for b in layer.ravel():
            bid = b.get("id", "minecraft:air") if isinstance(b, dict) else str(b)
            if bid != "minecraft:air":
                has_solid = True
                break
        if has_solid:
            base_y = y
            break

    ty1 = ty1_input - base_y

    print(f"  [paste] npy={os.path.basename(npy_path)}")
    print(f"  [paste] origin_y={ty1_input}, base_y={base_y}, 实际粘贴Y={ty1}")

    apply_volume(volume, (tx1, ty1, tz1),
                 skip_air=skip_air, rotation=rotation, host=host)
