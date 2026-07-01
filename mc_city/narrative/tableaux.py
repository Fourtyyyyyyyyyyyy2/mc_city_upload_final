"""叙事图层：环境实景（剧情演绎）—— 冻结场景讲毁灭故事，不靠文字。

用方块搭废墟/焦土/碎根 + 盔甲架(armor stand)摆姿演创始人/守军/入侵者/伏尸。
全静态（活劫掠者大戏归 invasion.py，不混）。生成期世界冻结，盔甲架 NoGravity 静止。

MVP 3 幕（对应已删 6 书的故事节点）：
  1. 立约祭坛  The Founding Pact —— 城心树旁，四公会歃盟立约。
  2. 北门围城  The Fall at the North Gate —— 北门，铁影破城、守军伏尸、焦土。
  3. 断根之夜  The Night the Root Broke —— 树旁黑根破土、碎裂树干。

方块走 set_blocks_batch；盔甲架走 /summon (_try_cmd)。读 height_map 判 sentinel（>min_y）。
flag NARRATIVE_TABLEAUX_ENABLED。非破坏性（静态），可进 GDMC 提交。
"""
from __future__ import annotations

import numpy as np

from ..config import DEFAULT_HOST, WALL_RADIUS
from ..mc.command import _try_cmd
from ..mc.placement import set_blocks_batch
from ..scan.coord_frame import ScanContext
from .signs import _line_to_snbt_json

# 四公会主题色（实景祭坛/装饰用）
_GUILD_COLOR = {
    "scholars":    "minecraft:blue_concrete",
    "engineers":   "minecraft:orange_concrete",
    "merchants":   "minecraft:yellow_concrete",
    "adventurers": "minecraft:red_concrete",
}


# ── 通用工具 ──────────────────────────────────────────────────────────
def _surface_y(wx: int, wz: int, height_map: np.ndarray, ctx: ScanContext):
    """该列地表世界 Y；越界/sentinel → None（铁律：判 > min_y 免落虚空）。"""
    xs, zs = wx - ctx.origin_x, wz - ctx.origin_z
    NZ, NX = height_map.shape
    if not (0 <= xs < NX and 0 <= zs < NZ):
        return None
    y = int(height_map[zs, xs])
    return y if y > int(ctx.min_y) else None


def _blk(batch: list, x: int, y: int, z: int, bid: str):
    batch.append({"x": int(x), "y": int(y), "z": int(z), "id": bid})


def _disc(batch: list, cx: int, cz: int, y: int, r: int, bid: str,
          height_map, ctx, jitter: set = None):
    """在 (cx,cz) 半径 r 的圆盘贴地铺 bid（地表+0）；jitter 给定则只铺其中的格。"""
    for dz in range(-r, r + 1):
        for dx in range(-r, r + 1):
            if dx * dx + dz * dz > r * r:
                continue
            if jitter is not None and (dx, dz) not in jitter:
                continue
            wx, wz = cx + dx, cz + dz
            gy = _surface_y(wx, wz, height_map, ctx)
            if gy is not None:
                _blk(batch, wx, gy, wz, bid)


def _stand(host: str, wx: int, wy: int, wz: int, yaw: float,
           armor=("", "", "", ""), main: str = "", off: str = "",
           pose: str = "guard", name: str = "") -> bool:
    """召唤一个摆姿盔甲架。armor=(feet,legs,chest,head) 物品 id，空串=不戴。"""
    def _item(bid):
        return "{}" if not bid else f'{{id:"{bid}",count:1}}'
    armor_nbt = ",".join(_item(a) for a in armor)
    hands_nbt = f"{_item(main)},{_item(off)}"
    poses = {
        "guard": "{RightArm:[-100f,20f,0f],LeftArm:[-95f,-15f,0f],Head:[8f,0f,0f]}",
        "fallen": "{RightArm:[-160f,30f,0f],LeftArm:[-160f,-30f,0f],"
                  "Head:[50f,20f,0f],RightLeg:[20f,0f,0f],LeftLeg:[-15f,0f,0f]}",
        "raise": "{RightArm:[-170f,0f,0f],LeftArm:[-30f,0f,0f],Head:[-10f,0f,0f]}",
        "mourn": "{RightArm:[-40f,0f,0f],LeftArm:[-40f,0f,0f],Head:[35f,0f,0f]}",
    }
    pose_nbt = poses.get(pose, poses["guard"])
    name_nbt = ""
    if name:
        name_nbt = f",CustomName:{_line_to_snbt_json(name)},CustomNameVisible:0b"
    nbt = (f"{{NoGravity:1b,Invulnerable:1b,PersistenceRequired:1b,ShowArms:1b,"
           f"NoBasePlate:1b,Rotation:[{float(yaw)}f,0f],"
           f"ArmorItems:[{armor_nbt}],HandItems:[{hands_nbt}],Pose:{pose_nbt}"
           f"{name_nbt}}}")
    return _try_cmd(f"summon minecraft:armor_stand {wx} {wy} {wz} {nbt}", host)


# ── 幕 1：立约祭坛 ────────────────────────────────────────────────────
def _scene_pact_altar(cx: int, cz: int, tree_r: int,
                      height_map, ctx, host: str) -> int:
    """城心树旁的四公会立约祭坛：石台 + 四色徽柱 + 裂誓约石 + 4 创始人盔甲架。"""
    ax = cx + tree_r + 6                          # 树东侧外缘（学者扇区）
    az = cz
    base_y = _surface_y(ax, az, height_map, ctx)
    if base_y is None:
        print("   ⚠️ [幕1 祭坛] 地表无效，跳过")
        return 0
    batch: list = []
    # 5x5 抛光黑石砖台
    for dz in range(-2, 3):
        for dx in range(-2, 3):
            _blk(batch, ax + dx, base_y, az + dz, "minecraft:polished_blackstone_bricks")
    # 中央誓约石（裂、带苔）
    _blk(batch, ax, base_y + 1, az, "minecraft:chiseled_stone_bricks")
    _blk(batch, ax, base_y + 2, az, "minecraft:cracked_stone_bricks")
    _blk(batch, ax, base_y + 3, az, "minecraft:soul_lantern[hanging=false]")
    for d in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        _blk(batch, ax + d[0], base_y + 1, az + d[1], "minecraft:mossy_stone_brick_slab")
    # 四角徽柱：四公会主题色
    corners = [(-2, -2, "scholars"), (2, -2, "engineers"),
               (-2, 2, "merchants"), (2, 2, "adventurers")]
    for dx, dz, guild in corners:
        _blk(batch, ax + dx, base_y + 1, az + dz, "minecraft:polished_blackstone")
        _blk(batch, ax + dx, base_y + 2, az + dz, _GUILD_COLOR[guild])
    set_blocks_batch(batch)
    # 4 创始人盔甲架，绕誓约石而立，举手盟誓
    founders = [(-3, 0, -90, "scholars"), (3, 0, 90, "engineers"),
                (0, -3, 180, "merchants"), (0, 3, 0, "adventurers")]
    n = 0
    for dx, dz, yaw, guild in founders:
        sy = _surface_y(ax + dx, az + dz, height_map, ctx)
        if sy is None:
            continue
        if _stand(host, ax + dx, sy + 1, az + dz, yaw,
                  armor=("", "", _guild_chest(guild), ""),
                  main="minecraft:iron_ingot", pose="raise",
                  name=f"Founder of the {guild.capitalize()}"):
            n += 1
    print(f"   🗿 [幕1 立约祭坛] 台已建 + 创始人 {n}/4")
    return 1


def _guild_chest(guild: str) -> str:
    return {"scholars": "minecraft:leather_chestplate",
            "engineers": "minecraft:chainmail_chestplate",
            "merchants": "minecraft:golden_chestplate",
            "adventurers": "minecraft:iron_chestplate"}.get(guild, "")


# ── 幕 2：北门围城 ────────────────────────────────────────────────────
def _scene_north_gate(cx: int, cz: int, height_map, ctx, host: str) -> int:
    """北门：铁影破城——焦土、断墙、守军伏尸、铁影入侵者举手而立。"""
    gx, gz = cx, cz - WALL_RADIUS                 # 北门（-Z 方向）
    base_y = _surface_y(gx, gz, height_map, ctx)
    if base_y is None:
        print("   ⚠️ [幕2 北门] 地表无效，跳过")
        return 0
    batch: list = []
    # 焦土带：门内外 ±8 一片，黑石/玄武岩/煤+零星岩浆光
    rng = np.random.default_rng(7)
    for dz in range(-8, 9):
        for dx in range(-8, 9):
            if dx * dx + dz * dz > 64:
                continue
            wx, wz = gx + dx, gz + dz
            gy = _surface_y(wx, wz, height_map, ctx)
            if gy is None:
                continue
            roll = rng.random()
            bid = ("minecraft:magma_block" if roll < 0.06 else
                   "minecraft:coal_block" if roll < 0.16 else
                   "minecraft:basalt" if roll < 0.45 else
                   "minecraft:blackstone")
            _blk(batch, wx, gy, wz, bid)
    # 散落断石 + 蛛网（颓败）
    for dx, dz, h in [(-3, 1, 1), (2, -2, 2), (4, 0, 1), (-5, -3, 1), (1, 3, 1)]:
        wx, wz = gx + dx, gz + dz
        gy = _surface_y(wx, wz, height_map, ctx)
        if gy is None:
            continue
        for k in range(h):
            _blk(batch, wx, gy + 1 + k, wz, "minecraft:cobblestone")
        _blk(batch, wx, gy + 1 + h, wz, "minecraft:cobweb")
    # 余烬：灵魂篝火（方块，不蔓延、有烟有光）——"仍在闷烧"，静态可提交。
    for dx, dz in [(-4, 2), (3, -1), (0, 6), (5, 3)]:
        wx, wz = gx + dx, gz + dz
        gy = _surface_y(wx, wz, height_map, ctx)
        if gy is not None:
            _blk(batch, wx, gy + 1, wz, "minecraft:soul_campfire[lit=true]")
    set_blocks_batch(batch)
    # 守军伏尸（铁甲，倒地姿）在门内侧
    n = 0
    for dx, dz, yaw in [(-2, 2, 30), (3, 3, 200), (0, 4, 110)]:
        sy = _surface_y(gx + dx, gz + dz, height_map, ctx)
        if sy is None:
            continue
        if _stand(host, gx + dx, sy + 1, gz + dz, yaw,
                  armor=("minecraft:iron_boots", "", "minecraft:iron_chestplate",
                         "minecraft:iron_helmet"),
                  main="minecraft:iron_sword", pose="fallen",
                  name="Fallen Defender"):
            n += 1
    # 铁影入侵者（黑甲，举手而立）在门外侧
    for dx, dz, yaw in [(-1, -4, 0), (2, -5, 0)]:
        sy = _surface_y(gx + dx, gz + dz, height_map, ctx)
        if sy is None:
            continue
        if _stand(host, gx + dx, sy + 1, gz + dz, yaw,
                  armor=("minecraft:netherite_boots", "minecraft:netherite_leggings",
                         "minecraft:netherite_chestplate", "minecraft:netherite_helmet"),
                  main="minecraft:netherite_sword", pose="raise",
                  name="Iron Shadow"):
            n += 1
    print(f"   ⚔️ [幕2 北门围城] 焦土+断墙已建 + 人形 {n}/5")
    return 1


# ── 幕 3：断根之夜 ────────────────────────────────────────────────────
def _scene_broken_root(cx: int, cz: int, tree_r: int,
                       height_map, ctx, host: str) -> int:
    """树旁：黑化主根破土斜出 + 碎裂树干段 + 焦痕，旁立一哀悼幸存者。"""
    # 主根从树南缘斜向城外伸出
    rx, rz = cx, cz + tree_r + 2
    base_y = _surface_y(rx, rz, height_map, ctx)
    if base_y is None:
        print("   ⚠️ [幕3 断根] 地表无效，跳过")
        return 0
    batch: list = []
    # 斜出的黑根（dark_oak_log + 黑石包覆），逐格抬升
    root_path = [(0, 0), (0, 1), (1, 2), (1, 3), (2, 3), (3, 4), (4, 4), (5, 5)]
    for i, (dx, dz) in enumerate(root_path):
        wx, wz = rx + dx, rz + dz
        gy = _surface_y(wx, wz, height_map, ctx)
        if gy is None:
            continue
        top = gy + 1 + i // 2
        _blk(batch, wx, top, wz, "minecraft:dark_oak_log[axis=z]")
        _blk(batch, wx, top - 1, wz, "minecraft:polished_blackstone")
    # 碎裂的树干段（横躺的原木）
    for dx, dz, ax_ in [(-3, 1, "x"), (-2, 4, "z"), (3, 1, "x")]:
        wx, wz = rx + dx, rz + dz
        gy = _surface_y(wx, wz, height_map, ctx)
        if gy is None:
            continue
        _blk(batch, wx, gy + 1, wz, f"minecraft:dark_oak_log[axis={ax_}]")
        _blk(batch, wx + (1 if ax_ == "x" else 0), gy + 1,
             wz + (1 if ax_ == "z" else 0), f"minecraft:stripped_dark_oak_log[axis={ax_}]")
    # 焦痕圆斑
    _disc(batch, rx + 1, rz + 2, base_y, 4, "minecraft:coal_block",
          height_map, ctx,
          jitter={(dx, dz) for dx in range(-4, 5) for dz in range(-4, 5)
                  if (dx + dz) % 3 == 0})
    # 余烬篝火（不蔓延）：黑根旁仍在闷烧
    for dx, dz in [(-2, 3), (4, 2)]:
        wx, wz = rx + dx, rz + dz
        gy = _surface_y(wx, wz, height_map, ctx)
        if gy is not None:
            _blk(batch, wx, gy + 1, wz, "minecraft:soul_campfire[lit=true]")
    set_blocks_batch(batch)
    # 哀悼的幸存者
    sy = _surface_y(rx - 4, rz + 1, height_map, ctx)
    n = 0
    if sy is not None and _stand(host, rx - 4, sy + 1, rz + 1, 45,
                                 armor=("", "", "minecraft:leather_chestplate", ""),
                                 main="minecraft:torch", pose="mourn",
                                 name="The Survivor"):
        n = 1
    print(f"   🌑 [幕3 断根之夜] 黑根+碎干+焦痕已建 + 幸存者 {n}/1")
    return 1


# ── 主入口 ────────────────────────────────────────────────────────────
def stage_tableaux(center_x: int, center_z: int,
                   height_map: np.ndarray, ctx: ScanContext,
                   core_info: dict, host: str = DEFAULT_HOST) -> int:
    """埋 3 幕环境实景。返回成功的幕数。core_info 给灵魂树落点/尺寸推树半径。"""
    from ..city.placement import footprint_xz
    try:
        tx, tz = footprint_xz(core_info["path"], core_info.get("rotation", 0))
        tree_r = max(tx, tz) // 2
    except Exception as exc:
        print(f"   [WARN] tableaux core footprint read failed: {exc!r}")
        tree_r = 30
    done = 0
    for fn in (
        lambda: _scene_pact_altar(center_x, center_z, tree_r, height_map, ctx, host),
        lambda: _scene_north_gate(center_x, center_z, height_map, ctx, host),
        lambda: _scene_broken_root(center_x, center_z, tree_r, height_map, ctx, host),
    ):
        try:
            done += fn()
        except Exception as exc:
            print(f"   ⚠️ 实景一幕异常：{exc!r}")
    print(f"   📜 环境叙事实景：{done}/3 幕完成")
    return done


__all__ = ["stage_tableaux"]
