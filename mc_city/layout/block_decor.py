"""Priority 2 卡 9.4：公会主题装饰（主殿前广场）。

现实化（1 街区 1 栋后没中庭）：把每公会的主题装饰组放在**主殿与中心广场之间
的开阔内圈**（r≈forecourt_r，plaza_outer 55 ~ mid_start 90 中间），形成 4 个
公会前广场地标。

公开 API:
    GUILD_BLOCK_DECOR_RECIPE  —— 公会 → [{offset:(dx,dz), id}] 装饰配方（≤12 块）
    decorate_blocks(placed, ctx, height_map, center_x, center_z, dry_run)
        吃 place_block_buildings 的返回（取 role=="main" 的主殿方位），放配方。
        返回放置块数；dry_run=True 时只算 payload 数不调 HTTP。

不调 narrative.guild_decor（那是"每栋建筑+1块"，与本卡正交）；借其 SNBT 思路。
"""
from __future__ import annotations

import math

from ..config import FORECOURT_RADIUS
from ..mc.placement import set_blocks_batch
from ..scan.coord_frame import ScanContext

DECOR_BATCH_SIZE = 256

# 公会 → 主题装饰配方。offset = 相对前广场锚点的 (dx, dz)；id 末尾可带 [属性]。
# 每组 ≤ 12 块（CONSTRAINT）。只用方块（不用 armor_stand 等实体，setblock 放不了）。
GUILD_BLOCK_DECOR_RECIPE: dict[str, list[dict]] = {
    "soul_scholars": [   # 学者坊：中央讲台 + 四周藏书 + 四角樱花
        {"offset": (0, 0), "id": "minecraft:lectern[facing=south]"},
        {"offset": (-2, 0), "id": "minecraft:chiseled_bookshelf"},
        {"offset": (2, 0), "id": "minecraft:chiseled_bookshelf"},
        {"offset": (0, -2), "id": "minecraft:chiseled_bookshelf"},
        {"offset": (0, 2), "id": "minecraft:chiseled_bookshelf"},
        {"offset": (-3, -3), "id": "minecraft:cherry_sapling"},
        {"offset": (3, -3), "id": "minecraft:cherry_sapling"},
        {"offset": (-3, 3), "id": "minecraft:cherry_sapling"},
        {"offset": (3, 3), "id": "minecraft:cherry_sapling"},
    ],
    "soul_engineers": [  # 工程坊：中央铁砧 + 两侧高炉锻造台 + 岩浆池
        {"offset": (0, 0), "id": "minecraft:anvil"},
        {"offset": (-2, 0), "id": "minecraft:blast_furnace[facing=east]"},
        {"offset": (2, 0), "id": "minecraft:smithing_table"},
        {"offset": (0, -2), "id": "minecraft:furnace[facing=south]"},
        {"offset": (0, 2), "id": "minecraft:lava"},
        {"offset": (1, 2), "id": "minecraft:lava"},
        {"offset": (-1, 2), "id": "minecraft:lava"},
    ],
    "merchants": [       # 商人坊：木桶 + 堆肥 + 箱子市集摊 + 干草垛
        {"offset": (0, 0), "id": "minecraft:barrel"},
        {"offset": (-2, 0), "id": "minecraft:composter"},
        {"offset": (2, 0), "id": "minecraft:chest[facing=west]"},
        {"offset": (0, -2), "id": "minecraft:barrel"},
        {"offset": (0, 2), "id": "minecraft:chest[facing=north]"},
        {"offset": (-2, -2), "id": "minecraft:hay_block"},
        {"offset": (2, 2), "id": "minecraft:hay_block"},
    ],
    "adventurers": [     # 勇者坊：箭靶 + 磨刀石 + 铁砧训练场 + 篝火
        {"offset": (0, 0), "id": "minecraft:target"},
        {"offset": (-2, 0), "id": "minecraft:grindstone[face=floor,facing=east]"},
        {"offset": (2, 0), "id": "minecraft:anvil"},
        {"offset": (0, -2), "id": "minecraft:campfire"},
        {"offset": (0, 2), "id": "minecraft:target"},
        {"offset": (-2, 2), "id": "minecraft:iron_bars"},
        {"offset": (2, 2), "id": "minecraft:iron_bars"},
    ],
}


def build_decor_payloads(placed: dict,
                         ctx: ScanContext,
                         height_map,
                         center_x: int,
                         center_z: int,
                         forecourt_r: int = FORECOURT_RADIUS,
                         ) -> tuple[list[dict], int]:
    """按主殿方位算 4 公会前广场装饰 payload。不调 HTTP。返回 (payloads, skipped)。"""
    NZ, NX = height_map.shape
    mains = [info for ring in placed.values() for info in ring
             if info.get("role") == "main"]
    payloads: list[dict] = []
    skipped = 0

    for m in mains:
        recipe = GUILD_BLOCK_DECOR_RECIPE.get(m["guild"])
        if not recipe:
            continue
        ox, _oy, oz = m["origin"]
        ang = math.atan2(oz - center_z, ox - center_x)   # 主殿方位
        ax = int(round(center_x + forecourt_r * math.cos(ang)))
        az = int(round(center_z + forecourt_r * math.sin(ang)))

        for item in recipe:
            dx, dz = item["offset"]
            wx, wz = ax + dx, az + dz
            xs, zs = ctx.w2s(wx, wz)
            if not (0 <= xs < NX and 0 <= zs < NZ):
                skipped += 1
                continue
            gy = int(height_map[zs, xs])
            if gy <= int(ctx.min_y):
                skipped += 1
                continue
            payloads.append({"x": int(wx), "y": int(gy + 1), "z": int(wz),
                             "id": item["id"]})

    return payloads, skipped


def decorate_blocks(placed: dict,
                    ctx: ScanContext,
                    height_map,
                    center_x: int,
                    center_z: int,
                    forecourt_r: int = FORECOURT_RADIUS,
                    dry_run: bool = False,
                    ) -> int:
    """4 公会前广场主题装饰。返回放置块数（dry_run 时返回 payload 数）。"""
    payloads, skipped = build_decor_payloads(
        placed, ctx, height_map, center_x, center_z, forecourt_r)
    if skipped:
        print(f"  ⚠️ 跳过 {skipped} 块装饰（越界 / sentinel）")
    if dry_run:
        return len(payloads)

    success = 0
    for i in range(0, len(payloads), DECOR_BATCH_SIZE):
        batch = payloads[i:i + DECOR_BATCH_SIZE]
        if set_blocks_batch(batch):
            success += len(batch)
        else:
            print(f"  ⚠️ 装饰批次写入失败（{len(batch)} 块），继续")
    return success


__all__ = [
    "GUILD_BLOCK_DECOR_RECIPE",
    "build_decor_payloads", "decorate_blocks",
]
