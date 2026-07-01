"""通过 GDMC HTTP GET 扫描世界方块。

按 16x16x16 chunk 拉取，结果写成 (NY, NZ, NX) 的 object 数组（dict 元素）。
之后调用 BlockCodec.convert_object_array 转成 uint16 紧凑数组节省内存。
"""
import numpy as np
import requests
from tqdm import tqdm

from ..config import DEFAULT_HOST

_session = requests.Session()
_BLOCK_SIZE = 16


def _extract_block_id(block) -> str:
    if isinstance(block, dict):
        return block.get("id", "minecraft:air")
    elif isinstance(block, str):
        return block
    return "minecraft:air"


def _compose_id(b: dict, include_state: bool) -> str:
    """方块 id 拼上状态 → 'minecraft:oak_door[facing=east,half=lower,...]'。

    include_state=False 或 state 为空 → 返回裸 id（向后兼容）。门/活板门/楼梯/原木
    的 facing/half/hinge/axis 全靠这里带出来，否则扫进 npy 就丢成默认朝向。
    """
    bid = b["id"]
    if not include_state:
        return bid
    state = b.get("state") or {}
    if not state:
        return bid
    props = ",".join(f"{k}={v}" for k, v in state.items())
    return f"{bid}[{props}]"


def get_cube(x: int, y: int, z: int,
             dx: int = _BLOCK_SIZE, dy: int = _BLOCK_SIZE, dz: int = _BLOCK_SIZE,
             host: str = DEFAULT_HOST, include_state: bool = False):
    """拉取 [x..x+dx, y..y+dy, z..z+dz) 区域的方块，返回 [dy][dz][dx] 嵌套列表。

    include_state=True 时带 ?includeState=true 并把状态拼进 id（单栋建筑扫描用，
    保住门/楼梯朝向）；主地形扫描留 False，避免 codec 唯一 id 膨胀。
    """
    url = f"{host}/blocks?x={x}&y={y}&z={z}&dx={dx}&dy={dy}&dz={dz}"
    if include_state:
        url += "&includeState=true"
    try:
        response = _session.get(url, timeout=5)
        raw_list = response.json()
        raw_dict = {(b["x"], b["y"], b["z"]): _compose_id(b, include_state)
                    for b in raw_list if isinstance(b, dict)}

        cube = []
        for iy in range(dy):
            y_row = []
            for iz in range(dz):
                z_row = []
                for ix in range(dx):
                    px, py, pz = x + ix, y + iy, z + iz
                    z_row.append(raw_dict.get((px, py, pz), "minecraft:air"))
                y_row.append(z_row)
            cube.append(y_row)
        return cube

    except Exception as e:
        print(f"❌ get_cube 失败: {e} @ ({x},{y},{z}) size ({dx},{dy},{dz})")
        return [[["minecraft:air"] * dx for _ in range(dz)] for _ in range(dy)]


def scan_minecraft(x1: int, x2: int, y1: int, y2: int, z1: int, z2: int,
                   filename: str = "scan_blocks",
                   host: str = DEFAULT_HOST) -> np.ndarray:
    """扫描整个 [x1,x2)×[y1,y2)×[z1,z2) 区域并存成 {filename}.npy。

    返回 (NY, NZ, NX) 的 object 数组，元素为 {"id": "minecraft:xxx"}。
    """
    NX, NY, NZ = x2 - x1, y2 - y1, z2 - z1
    volume = np.empty((NY, NZ, NX), dtype=object)

    for Y in tqdm(range(y1, y2, _BLOCK_SIZE), desc="Y"):
        for Z in range(z1, z2, _BLOCK_SIZE):
            for X in range(x1, x2, _BLOCK_SIZE):
                dx = min(_BLOCK_SIZE, x2 - X)
                dy = min(_BLOCK_SIZE, y2 - Y)
                dz = min(_BLOCK_SIZE, z2 - Z)

                cube = get_cube(X, Y, Z, dx, dy, dz, host=host)

                for dy_idx in range(dy):
                    for dz_idx in range(dz):
                        for dx_idx in range(dx):
                            yy = (Y + dy_idx) - y1
                            zz = (Z + dz_idx) - z1
                            xx = (X + dx_idx) - x1

                            block_id = cube[dy_idx][dz_idx][dx_idx]
                            if isinstance(block_id, str):
                                volume[yy, zz, xx] = {"id": block_id}
                            elif isinstance(block_id, dict):
                                volume[yy, zz, xx] = block_id
                            else:
                                volume[yy, zz, xx] = {"id": "minecraft:air"}

    np.save(f"{filename}.npy", volume, allow_pickle=True)
    print(f"✅ 保存完成: {filename}.npy")
    return volume
