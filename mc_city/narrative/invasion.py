"""Priority 7 卡 13.1（MVP）：活入侵彩蛋——延迟·聊天可点菜单触发。

生成器跑完即退出，没法"等一会再召唤"。所以这里**埋一套 vanilla 机关**（计分板 +
命令方块），生成期世界冻结、机关静止；main.py resume_world 解冻后机关才跑：
玩家接近城心 N tick → 聊天弹 [开始入侵]/[再等等] 可点菜单 → 点击触发突袭
（一波劫掠者破城 + 灵魂树基环放火，解冻后火自蔓延上树）。

MVP 范围：计时菜单 + 1 波劫掠者(+劫掠兽) + 灵魂树燃烧。凋零/村民/波次/建筑随机火留迭代。
默认 flag 关；火会真烧城，playtest 专用。命令方块 Command 用单引号 SNBT 避免引号转义地狱。
"""
from __future__ import annotations

import math
import random

import numpy as np

from ..config import (
    DEFAULT_HOST,
    INVASION_BURN_TREE,
    INVASION_EVOKERS,
    INVASION_FOLLOW_RANGE,
    INVASION_GATE_RING,
    INVASION_LIGHTNING,
    INVASION_PILLAGERS,
    INVASION_RAVAGERS,
    INVASION_STORM,
    INVASION_TNT,
    INVASION_TNT_HEIGHT,
    INVASION_TNT_RADIUS,
    INVASION_VANGUARD,
    INVASION_VINDICATORS,
    INVASION_WITHERS,
    WALL_RADIUS,
)
from ..mc.command import _try_cmd
from ..scan.coord_frame import ScanContext


def _surface_y(wx: int, wz: int, height_map: np.ndarray, ctx: ScanContext):
    """该列地表 y（世界）；越界/sentinel 返回 None（铁律：判 > min_y，免落虚空）。"""
    xs, zs = wx - ctx.origin_x, wz - ctx.origin_z
    NZ, NX = height_map.shape
    if not (0 <= xs < NX and 0 <= zs < NZ):
        return None
    y = int(height_map[zs, xs])
    return y if y > int(ctx.min_y) else None


def _cb(x: int, y: int, z: int, kind: str, facing: str,
        command: str, conditional: bool, auto: bool = True) -> str:
    """拼一条放命令方块的 /setblock。Command 用单引号 SNBT，内含 JSON 双引号无需转义。"""
    cond = "true" if conditional else "false"
    a = "1b" if auto else "0b"
    esc = command.replace("\\", "\\\\").replace("'", "\\'")
    return (f"setblock {x} {y} {z} minecraft:{kind}"
            f"[facing={facing},conditional={cond}]{{Command:'{esc}',auto:{a}}}")


def _chain(x0: int, y: int, z: int, head: str, body: list[str]) -> list[str]:
    """一段 gated 链：repeating 头每 tick 跑 head，**仅 head 成功才触发整链**。

    body **全部** conditional=true：链只在每一环都成功时往后传。head 失败(如 state 不符)
    → 被跳过的 conditional 不再把触发传下去 → 整链不跑（严格 one-shot）。
    实测：若 body 设 unconditional，被跳过的 conditional 仍会触发其后 unconditional
    块 → 每 tick 重复执行（劫掠兽无限刷）。故全 conditional。沿 +x 排列。
    代价：某条命令失败(success=0)会 halt 后续——故 body 内命令需都能成功（summon 跳
    sentinel 列、scoreboard reset 恒成功等）。
    """
    cmds = [_cb(x0, y, z, "repeating_command_block", "east", head, False)]
    for i, c in enumerate(body):
        cmds.append(_cb(x0 + 1 + i, y, z, "chain_command_block", "east",
                        c, conditional=True))
    return cmds


def _scoreboard_setup() -> list[str]:
    """祭坛踩踏方案：只需 inv_state(dummy)。0=待触发 1=已踩待开战 2=已开战。"""
    return [
        "scoreboard objectives add inv_state dummy",
        "scoreboard players set #s inv_state 0",
        "difficulty hard",                           # 部署时设(不进链)：确保怪主动攻击
    ]


def _altar_blocks(ax: int, ay: int, az: int) -> list[str]:
    """黑曜祭坛 + 一根高发光灯柱（远处可见的灯塔）+ 4 角魂火灯。玩家走到柱旁即可触发。"""
    cmds = [f"setblock {ax} {ay} {az} minecraft:crying_obsidian"]      # 祭坛基座
    for h in range(1, 13):                                            # 12 格海晶灯柱（醒目）
        cmds.append(f"setblock {ax} {ay + h} {az} minecraft:sea_lantern")
    for dx, dz in ((1, 1), (1, -1), (-1, 1), (-1, -1)):              # 4 角魂火灯
        cmds.append(f"setblock {ax + dx} {ay} {az + dz} minecraft:polished_blackstone")
        cmds.append(f"setblock {ax + dx} {ay + 1} {az + dz} minecraft:soul_lantern")
        cmds.append(f"setblock {ax + dx} {ay + 2} {az + dz} minecraft:air")
    return cmds


def _nbt(follow: int = 0) -> str:
    """summon NBT：防 despawn + 可选 follow_range（远刷也锁定玩家成队推进）。"""
    if follow:
        return (f"{{PersistenceRequired:1b,Attributes:["
                f'{{id:"minecraft:follow_range",base:{follow}.0}}]}}')
    return "{PersistenceRequired:1b}"


def _ring_summon(body: list, mob: str, n: int, cx: int, cz: int, r: int,
                 height_map: np.ndarray, ctx: ScanContext, phase: float = 0.0,
                 follow: int = 0):
    """绕城心半径 r 均布 summon n 只 mob。"""
    for k in range(max(0, n)):
        ang = 2 * math.pi * k / max(1, n) + phase
        x, z = int(cx + r * math.cos(ang)), int(cz + r * math.sin(ang))
        y = _surface_y(x, z, height_map, ctx)
        if y is not None:
            body.append(f"summon minecraft:{mob} {x} {y + 1} {z} {_nbt(follow)}")


def _gate_army(body: list, mob: str, n: int, cx: int, cz: int, gate_r: int,
               follow: int, height_map: np.ndarray, ctx: ScanContext):
    """4 路城门纵队：n 只分到 4 个 cardinal 门，各成 3 宽×多排的密集纵队，朝城心推进。"""
    gates = ((1, 0), (-1, 0), (0, 1), (0, -1))
    per = max(1, n // 4)
    for gx, gz in gates:
        px, pz = -gz, gx                          # 垂直于推进方向（横排展开）
        for k in range(per):
            depth = k // 3                        # 第几排（往城外退）
            lat = (k % 3) - 1                      # 横向 -1/0/1（3 宽）
            x = cx + gx * (gate_r + depth * 2) + px * lat * 2
            z = cz + gz * (gate_r + depth * 2) + pz * lat * 2
            y = _surface_y(int(x), int(z), height_map, ctx)
            if y is not None:
                body.append(f"summon minecraft:{mob} {int(x)} {y + 1} {int(z)} "
                            f"{_nbt(follow)}")


def _scatter_lightning(body: list, cx: int, cz: int, n: int,
                       height_map: np.ndarray, ctx: ScanContext):
    """城内随机落雷：视觉震撼 + 引火。"""
    rng = random.Random(f"light_{cx}_{cz}")
    for _ in range(max(0, n)):
        r = INVASION_TNT_RADIUS * math.sqrt(rng.random())
        a = rng.random() * 2 * math.pi
        x, z = int(cx + r * math.cos(a)), int(cz + r * math.sin(a))
        y = _surface_y(x, z, height_map, ctx)
        if y is not None:
            body.append(f"summon minecraft:lightning_bolt {x} {y + 1} {z}")


def _raid_body(cx: int, cz: int, height_map: np.ndarray, ctx: ScanContext) -> list[str]:
    """攻城大戏（绝对坐标）：氛围 + 4 路城门纵队推进 + 凋零 + 落雷 + 整树燃烧 + 天降 TNT。

    全 conditional 链：每条须 success≥1 否则 halt 后续。故只放保证成功的命令（难度移到
    部署、不放 scoreboard reset）。one-shot 由 head 的 state→2 保证。title 打头必成功。
    """
    body = ["weather thunder 600", "time set midnight"] if INVASION_STORM else []
    body += ["title @a times 10 80 30",
             'title @a title {"text":"THE RAID BEGINS","color":"dark_red","bold":true}',
             'title @a subtitle {"text":"The guilds have come to burn it all.",'
             '"color":"gray"}']
    gate_r = INVASION_GATE_RING or (WALL_RADIUS - 6)
    fr = INVASION_FOLLOW_RANGE
    # 先锋波：就在城心 28 格刷，触发即看见开打（解决"看不见敌人从哪来"）
    _ring_summon(body, "pillager", INVASION_VANGUARD, cx, cz, 28, height_map, ctx, follow=fr)
    # 主力 4 路城门纵队（72 格，视野内可见行军推进）
    _gate_army(body, "pillager", INVASION_PILLAGERS, cx, cz, gate_r, fr, height_map, ctx)
    _gate_army(body, "vindicator", INVASION_VINDICATORS, cx, cz, gate_r - 4, fr,
               height_map, ctx)
    _gate_army(body, "evoker", INVASION_EVOKERS, cx, cz, gate_r - 8, fr, height_map, ctx)
    _ring_summon(body, "ravager", INVASION_RAVAGERS, cx, cz, gate_r - 2, height_map,
                 ctx, follow=fr)
    _ring_summon(body, "wither", INVASION_WITHERS, cx, cz, 14, height_map, ctx)
    # 4 门刷怪点各劈一道闪电（标记"敌人从这来"）+ 随机落雷
    for gx, gz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        lx, lz = cx + gx * gate_r, cz + gz * gate_r
        ly = _surface_y(lx, lz, height_map, ctx)
        if ly is not None:
            body.append(f"summon minecraft:lightning_bolt {lx} {ly + 1} {lz}")
    _scatter_lightning(body, cx, cz, INVASION_LIGHTNING, height_map, ctx)
    if INVASION_BURN_TREE:
        cy = _surface_y(cx, cz, height_map, ctx)
        if cy is not None:
            # 灵魂树巨大(半径~40、高 cy+10..55)且中心空心 → 在树冠体积撒网格火，
            # 命中密布木头即引燃，火自蔓延烧全株（火落空气会灭但命中即可）。
            for h in (12, 26, 40, 54):
                for dx in range(-24, 25, 8):
                    for dz in range(-24, 25, 8):
                        if dx * dx + dz * dz <= 24 * 24:
                            body.append(f"setblock {cx + dx} {cy + h} "
                                        f"{cz + dz} minecraft:fire")
    _tnt_rain(body, cx, cz, height_map, ctx)
    return body


def _tnt_rain(body: list, cx: int, cz: int,
             height_map: np.ndarray, ctx: ScanContext):
    """天降 TNT 轰炸：城内随机散布点上空刷已点燃 TNT，引信错开 → 连环爆炸。"""
    rng = random.Random(f"tnt_{cx}_{cz}")
    for k in range(max(0, INVASION_TNT)):
        r = INVASION_TNT_RADIUS * math.sqrt(rng.random())   # 均匀散布于圆盘
        a = rng.random() * 2 * math.pi
        x, z = int(cx + r * math.cos(a)), int(cz + r * math.sin(a))
        sy = _surface_y(x, z, height_map, ctx)
        if sy is None:
            continue
        fuse = 40 + (k % 12) * 6                            # 错开引信 → 落地高度/时间不同
        body.append(f"summon minecraft:tnt {x} {sy + INVASION_TNT_HEIGHT} {z} "
                    f"{{fuse:{fuse}s}}")


def stage_invasion(center_x: int, center_z: int,
                   height_map: np.ndarray, ctx: ScanContext,
                   placed_origins: list, core_info: dict,
                   host: str = DEFAULT_HOST) -> None:
    """埋入侵机关：灵魂树旁建黑曜祭坛 + 命令方块。解冻后玩家逛够，**走上祭坛**即开战。

    祭坛踩踏触发（弃用聊天点击菜单——这版命令方块里的 tellraw 点击不稳）。玩家近城心
    时 actionbar 提示去踩祭坛；踩上 → 一波劫掠者/凋零破城 + 灵魂树燃烧（严格 one-shot）。
    placed_origins 留作后续迭代（村民）。
    """
    cx, cz = int(center_x), int(center_z)
    cy = _surface_y(cx, cz, height_map, ctx) or (int(ctx.min_y) + 70)
    by = int(ctx.min_y) + 5                       # 命令方块埋深处，不碍观瞻
    # 祭坛选址：城心南偏 48（灵魂树半径 ~40，48 出树、落南向主道空地），取该列地表当顶面
    ax, az = cx, cz + 48
    ay = _surface_y(ax, az, height_map, ctx) or cy

    cmds: list[str] = list(_scoreboard_setup())
    cmds += _altar_blocks(ax, ay, az)
    # 提示（standalone repeating）：state=0 且玩家在城心 40 内 → actionbar 引导去踩祭坛
    cmds.append(_cb(cx, by, cz, "repeating_command_block", "east",
        f"execute if score #s inv_state matches 0 "
        f"as @a[x={cx},y={cy},z={cz},distance=..40] run title @s actionbar "
        f'{{"text":"A dark altar smolders south of the soul tree '
        f'— step onto it to call down the raid","color":"gold"}}', False))
    # 踩踏检测（standalone repeating）：state=0 且玩家站祭坛上 → state→1
    cmds.append(_cb(cx, by, cz + 2, "repeating_command_block", "east",
        f"execute if score #s inv_state matches 0 "
        f"as @a[x={ax},y={ay + 1},z={az},distance=..2.5] "
        f"run scoreboard players set #s inv_state 1", False))
    # 开战链：state=1 → state→2 + 突袭（全 conditional，严格 one-shot）
    cmds += _chain(cx, by, cz + 4,
        "execute if score #s inv_state matches 1 "
        "run scoreboard players set #s inv_state 2",
        _raid_body(cx, cz, height_map, ctx))

    ok = sum(1 for c in cmds if _try_cmd(c, host))
    print(f"   ⚔️ 入侵机关已埋：命令方块/计分板 {ok}/{len(cmds)} 条；"
          f"祭坛 @({ax},{ay},{az})，走上去即开战")


__all__ = ["stage_invasion"]
