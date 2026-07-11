"""战后废墟化：让城市的物理形态本身讲"被入侵毁灭"的故事——焦黑、余烬烟柱、
断墙豁口、灵魂树断根燃烧、房屋爆炸缺口、雷暴夜落雷。纯环境叙事（评审一眼可见），
取代旧的告示牌/书/盔甲架。

火：树/房屋点真火(minecraft:fire)让可燃处真烧起来（周围石广场/道路挡火不蔓延到全城）；
不可燃处靠爆炸缺口(球形挖空+焦黑)+焦黑表现损毁。落雷靠避雷针吸引 + 主动 summon。
"""
from __future__ import annotations

import math
import random

import numpy as np

from ..config import (
    RUIN_BREACH_LEN_MAX,
    RUIN_BUILDING_FIRES,
    RUIN_BUILDING_FRAC,
    RUIN_BUILDING_TNT,
    RUIN_BURN_BUILDINGS,
    RUIN_DEBRIS_COUNT,
    RUIN_DEFENDERS_CORE,
    RUIN_DEFENDERS_ENABLED,
    RUIN_DEFENDERS_PER_GATE,
    RUIN_ENABLED,
    RUIN_FIREBALL_RADIUS,
    RUIN_FIREBALLS,
    RUIN_INVADER_GLOW,
    RUIN_INVADER_RAVAGER,
    RUIN_INVADERS_ENABLED,
    RUIN_INVADERS_PER_BREACH,
    RUIN_LIGHTNING_RODS,
    RUIN_LIGHTNING_STRIKES,
    RUIN_LOOT_FRAC,
    RUIN_MAX_TNT,
    RUIN_SCALE_BASE_R,
    RUIN_SCALE_ENABLED,
    RUIN_SIEGE_CAMP_SIZE,
    RUIN_SIEGE_CAMPS,
    RUIN_STORM,
    RUIN_TNT_FUSE_MAX,
    RUIN_TNT_FUSE_MIN,
    RUIN_TREE_FIRE,
    RUIN_TREE_TNT,
    RUIN_WALL_BREACHES,
    RUIN_WALL_BREACH_LEN,
)
from ..mc.command import mc_cmd
from ..mc.placement import set_blocks_batch
from ..scan.coord_frame import ScanContext

# 焦黑/炭化材质（烧过的石木）。
_CHAR = ("minecraft:blackstone", "minecraft:basalt",
         "minecraft:polished_basalt", "minecraft:cobbled_deepslate")
# 余烬/烟柱（不蔓延，城内散布用）。soul_campfire 蓝烟。
_SMOKE = "minecraft:campfire[lit=true]"
_SOUL_SMOKE = "minecraft:soul_campfire[lit=true]"
# 真火（会燃烧蔓延）——用在树/房屋可燃处，让它真的烧起来。
_FIRE = "minecraft:fire"
_ROD = "minecraft:lightning_rod"               # 避雷针：吸引雷暴天持续落雷
_NETHERRACK = "minecraft:netherrack"           # 火球核：其上火焰永不熄灭
_MAGMA = "minecraft:magma_block"
_ASH = "minecraft:gray_concrete_powder"       # 灰烬堆

# 可燃材质关键词：命中则火能点着（木/叶/羊毛/菌岩等）；否则视作不可燃（海晶/冰/石）。
_FLAMMABLE_KW = ("_log", "_wood", "planks", "leaves", "_wool", "wart_block",
                 "petals", "bamboo", "hay_block", "scaffolding", "vine")


def _flammable(b) -> bool:
    bid = b.get("id", "") if isinstance(b, dict) else str(b)
    return any(k in bid for k in _FLAMMABLE_KW)


def _surface_y(wx: int, wz: int, height_map: np.ndarray, ctx: ScanContext):
    """世界 xz → 地表 y；越界或 sentinel 返回 None。"""
    xs, zs = ctx.w2s(wx, wz)
    H, W = height_map.shape
    if not (0 <= xs < W and 0 <= zs < H):
        return None
    y = int(height_map[zs, xs])
    if y <= ctx.min_y:
        return None
    return y


def _is_water(wx: int, wz: int, height_map: np.ndarray,
              ctx: ScanContext, features=None) -> bool:
    """Return True when the world column is known water in terrain features."""
    if features is None:
        return False
    xs, zs = ctx.w2s(wx, wz)
    H, W = height_map.shape
    return 0 <= zs < H and 0 <= xs < W and bool(features.is_water[zs, xs])


def _nearest_land_spawn(wx: int, wz: int, height_map: np.ndarray,
                        ctx: ScanContext, features=None,
                        max_radius: int = 12):
    """Move shoreline mobs from water to the nearest valid land column."""
    if _surface_y(wx, wz, height_map, ctx) is not None \
            and not _is_water(wx, wz, height_map, ctx, features):
        return wx, wz
    for r in range(1, max_radius + 1):
        best = None
        best_d2 = None
        for dz in range(-r, r + 1):
            for dx in range(-r, r + 1):
                if max(abs(dx), abs(dz)) != r:
                    continue
                nx, nz = wx + dx, wz + dz
                if _surface_y(nx, nz, height_map, ctx) is None:
                    continue
                if _is_water(nx, nz, height_map, ctx, features):
                    continue
                d2 = dx * dx + dz * dz
                if best is None or d2 < best_d2:
                    best = (nx, nz)
                    best_d2 = d2
        if best is not None:
            return best
    return None


def _char(rng) -> str:
    return rng.choice(_CHAR)


def _ruin_soul_tree(batch: list, core_info: dict,
                    height_map: np.ndarray, ctx: ScanContext, rng,
                    tnt_points: list) -> None:
    """灵魂树断根燃烧态：烧**树的表面**（每列最高实心块）→ 焦黑残枝 + 烧穿破洞 +
    整株冒蓝烟。从外部一眼可见在燃烧，而不是把焦黑埋在树内部被树冠遮住。"""
    path = core_info.get("path")
    ox, oy, oz = core_info["origin"]
    try:
        vol = np.load(path, allow_pickle=True)
    except Exception as e:
        print(f"   ⚠️ ruin 读核心 npy 失败：{e!r}")
        return
    nh, nz, nx = vol.shape
    cx, cz = ox + nx // 2, oz + nz // 2
    tree_r = max(nx, nz) // 2

    def _solid(b) -> bool:
        bid = b.get("id", "minecraft:air") if isinstance(b, dict) else str(b)
        return bid != "minecraft:air"

    # 1) 树脚焦土环（地表一圈换焦黑，随机稀疏）。
    for _ in range(tree_r * 6):
        a = rng.uniform(0, 6.283)
        r = rng.uniform(tree_r * 0.4, tree_r * 0.95)
        wx, wz = int(cx + r * np.cos(a)), int(cz + r * np.sin(a))
        y = _surface_y(wx, wz, height_map, ctx)
        if y is not None:
            batch.append({"x": wx, "y": y, "z": wz, "id": _char(rng)})

    # 2) 树表面损毁：遍历每列最高实心块——可燃(木/叶)点真火烧起来；
    #    不可燃(海晶/冰/石)炸出破洞 + 焦黑残骸。从外部一眼可见在毁灭。
    tops = []                                    # 记录表面列，供挖缺口/放避雷针
    for iz in range(nz):
        for ix in range(nx):
            top = None
            for iy in range(nh - 1, -1, -1):
                if _solid(vol[iy, iz, ix]):
                    top = iy
                    break
            if top is None or top < nh * 0.3:
                continue
            wx, wy, wz = ox + ix, oy + top, oz + iz
            tops.append((wx, wy, wz))
            roll = rng.random()
            if _flammable(vol[top, iz, ix]):         # 可燃 → 真火点燃
                if roll < 0.5:
                    batch.append({"x": wx, "y": wy + 1, "z": wz, "id": _FIRE})
                elif roll < 0.68:
                    batch.append({"x": wx, "y": wy, "z": wz, "id": _char(rng)})
                elif roll < 0.8:
                    batch.append({"x": wx, "y": wy, "z": wz, "id": "minecraft:air"})
            else:                                    # 不可燃 → 炸毁破洞 + 焦黑
                if roll < 0.5:
                    batch.append({"x": wx, "y": wy, "z": wz, "id": "minecraft:air"})
                    if rng.random() < 0.4 and top - 1 >= 0:
                        batch.append({"x": wx, "y": wy - 1, "z": wz,
                                      "id": "minecraft:air"})
                elif roll < 0.72:
                    batch.append({"x": wx, "y": wy, "z": wz, "id": _char(rng)})
                elif roll < 0.82:
                    batch.append({"x": wx, "y": wy + 1, "z": wz, "id": _SOUL_SMOKE})

    # 3) 树上引爆 TNT（收集爆点，写盘后 summon）+ 树顶插避雷针（吸引落雷劈树）。
    if tops:
        for _ in range(RUIN_TREE_TNT):
            wx, wy, wz = rng.choice(tops)
            tnt_points.append((wx, wy + 1, wz))
        # 取最高的几个表面点插避雷针，雷暴天会反复劈燃烧的树顶。
        for wx, wy, wz in sorted(tops, key=lambda t: -t[1])[:3]:
            batch.append({"x": wx, "y": wy + 1, "z": wz, "id": _ROD})

    # 4) 树干基部焦黑柱 + 岩浆核（还在烧的断根）。
    for dy in range(0, max(3, nh // 3)):
        batch.append({"x": cx, "y": oy + dy, "z": cz, "id": _char(rng)})
    batch.append({"x": cx, "y": oy, "z": cz, "id": _MAGMA})


def _wall_breaches(batch: list, cx: int, cz: int, wall_radius: int,
                   height_map: np.ndarray, ctx: ScanContext, rng,
                   n_breaches: int, seg_len: int):
    """方城墙攻破口：随机在几条边上清出一段墙 + 豁口边缘焦黑碎块。
    返回 (豁口数, [(豁口中心x, 豁口中心z, 向城外法向x, 向城外法向z)])——供敌人对齐。"""
    made = 0
    positions = []
    # 4 条边：(固定轴, 固定值, 变化轴范围, 向城外单位法向)
    edges = [
        ("z", cz - wall_radius, (cx - wall_radius, cx + wall_radius), (0, -1)),
        ("z", cz + wall_radius, (cx - wall_radius, cx + wall_radius), (0, 1)),
        ("x", cx - wall_radius, (cz - wall_radius, cz + wall_radius), (-1, 0)),
        ("x", cx + wall_radius, (cz - wall_radius, cz + wall_radius), (1, 0)),
    ]
    picks = rng.sample(edges, min(n_breaches, len(edges)))
    for axis, fixed, (lo, hi), (nx, nz) in picks:
        if hi - lo <= seg_len + 4:
            continue
        start = rng.randint(lo + 2, hi - seg_len - 2)
        for t in range(seg_len):
            if axis == "z":
                wx, wz = start + t, fixed
            else:
                wx, wz = fixed, start + t
            y = _surface_y(wx, wz, height_map, ctx)
            if y is None:
                continue
            # 清掉这一段墙体（地表上方若干格设空）。
            for dy in range(-1, 7):
                batch.append({"x": wx, "y": y + dy, "z": wz, "id": "minecraft:air"})
            # 豁口地面撒焦黑碎块。
            if rng.random() < 0.6:
                batch.append({"x": wx, "y": y, "z": wz, "id": _char(rng)})
        mid = start + seg_len // 2
        if axis == "z":
            positions.append((mid, fixed, nx, nz))
        else:
            positions.append((fixed, mid, nx, nz))
        made += 1
    return made, positions


def _scatter_debris(batch: list, cx: int, cz: int, wall_radius: int,
                    plaza_r: int, height_map: np.ndarray, ctx: ScanContext,
                    rng, count: int) -> None:
    """城内散布焦土/余烬烟柱/灰烬堆——飞过一眼看到处处冒烟、烧焦。"""
    lo = max(6, int(plaza_r) + 2)
    hi = max(lo + 4, int(wall_radius) - 3)
    for _ in range(count):
        a = rng.uniform(0, 6.283)
        r = rng.uniform(lo, hi)
        wx, wz = int(cx + r * np.cos(a)), int(cz + r * np.sin(a))
        y = _surface_y(wx, wz, height_map, ctx)
        if y is None:
            continue
        roll = rng.random()
        if roll < 0.4:                                   # 冒烟余烬堆
            batch.append({"x": wx, "y": y, "z": wz, "id": _char(rng)})
            batch.append({"x": wx, "y": y + 1, "z": wz, "id": _SMOKE})
        elif roll < 0.6:                                 # 岩浆余烬（还在烧）
            batch.append({"x": wx, "y": y, "z": wz, "id": _MAGMA})
        elif roll < 0.85:                                # 焦黑斑
            for _dz in range(-1, 2):
                for _dx in range(-1, 2):
                    if rng.random() < 0.6:
                        batch.append({"x": wx + _dx, "y": y, "z": wz + _dz,
                                      "id": _char(rng)})
        else:                                            # 灰烬堆
            batch.append({"x": wx, "y": y, "z": wz, "id": _ASH})


def _ruin_buildings(batch: list, placed_boxes: list,
                    height_map: np.ndarray, ctx: ScanContext, rng,
                    frac: float, fires: int, tnt: int,
                    tnt_points: list) -> int:
    """建筑废墟化：选一部分屋子，多点撒真火（可燃屋自燃蔓延烧起来）+ 收集 TNT 爆点
    （写盘后 summon 真爆炸）+ 墙脚焦黑。用 height_map 地基高度定位。返回被损毁屋数。"""
    if not placed_boxes:
        return 0
    n = max(1, int(len(placed_boxes) * frac))
    chosen = rng.sample(placed_boxes, min(n, len(placed_boxes)))
    for box in chosen:
        x0, x1, z0, z1 = int(box[0]), int(box[1]), int(box[2]), int(box[3])
        # 多点撒火（贴地板/墙 → 可燃屋点燃蔓延）。
        for _ in range(fires):
            wx, wz = rng.randint(x0, x1), rng.randint(z0, z1)
            y = _surface_y(wx, wz, height_map, ctx)
            if y is not None:
                batch.append({"x": wx, "y": y + 1, "z": wz, "id": _FIRE})
        # 收集 TNT 爆点（屋子中上部，写盘后 summon primed TNT）+ 墙脚焦黑。
        for _ in range(tnt):
            wx, wz = rng.randint(x0, x1), rng.randint(z0, z1)
            y = _surface_y(wx, wz, height_map, ctx)
            if y is not None:
                tnt_points.append((wx, y + 2, wz))
        for _ in range(4):
            wx, wz = rng.randint(x0, x1), rng.randint(z0, z1)
            y = _surface_y(wx, wz, height_map, ctx)
            if y is not None:
                batch.append({"x": wx, "y": y, "z": wz, "id": _char(rng)})
    return len(chosen)


def _place_lightning_rods(batch: list, placed_boxes: list,
                          height_map: np.ndarray, ctx: ScanContext, rng,
                          n: int) -> list:
    """在几栋建筑顶插避雷针（该列高处 → 雷暴天反复被雷劈）。返回避雷针世界坐标。"""
    pts = []
    if not placed_boxes or n <= 0:
        return pts
    chosen = rng.sample(placed_boxes, min(n, len(placed_boxes)))
    for box in chosen:
        x0, x1, z0, z1 = int(box[0]), int(box[1]), int(box[2]), int(box[3])
        wx, wz = (x0 + x1) // 2, (z0 + z1) // 2
        y = _surface_y(wx, wz, height_map, ctx)
        if y is not None:
            ry = y + 6
            batch.append({"x": wx, "y": ry, "z": wz, "id": _ROD})
            pts.append((wx, ry, wz))
    return pts


def _burn_trees(batch: list, tree_boxes: list,
                height_map: np.ndarray, ctx: ScanContext, rng) -> int:
    """绿化树 100% 烧毁：每棵在中心柱(树干)贴真火——云杉可燃，火会自蔓延烧整树。
    随机点火对稀疏树 box 常打空，这里专打中心树干，确保每棵都点着。"""
    burned = 0
    for box in tree_boxes:
        x0, x1, z0, z1 = int(box[0]), int(box[1]), int(box[2]), int(box[3])
        cx, cz = (x0 + x1) // 2, (z0 + z1) // 2
        y = _surface_y(cx, cz, height_map, ctx)
        if y is None:
            continue
        # 中心柱 + 4 邻贴真火（贴住树干/枝叶 → 点燃蔓延），树脚焦黑。
        for h in range(1, 7):
            batch.append({"x": cx, "y": y + h, "z": cz, "id": _FIRE})
            for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                if rng.random() < 0.5:
                    batch.append({"x": cx + dx, "y": y + h, "z": cz + dz,
                                  "id": _FIRE})
        batch.append({"x": cx, "y": y, "z": cz, "id": _char(rng)})
        burned += 1
    return burned


def _summon_mob(ex: int, ez: int, cx: int, cz: int, mob: str,
                height_map: np.ndarray, ctx: ScanContext, face=None,
                features=None) -> bool:
    """在 (ex,ez) 地表 summon 一只定格实体(NoAI 站桩、不消失)。
    默认朝城心；face=(fx,fz) 时朝指定点（守军朝城外/朝豁口）。
    features 给定时：落点是水面则跳过（敌人只站岸上，不浮水上）。"""
    moved = _nearest_land_spawn(ex, ez, height_map, ctx, features)
    if moved is None:
        return False
    ex, ez = moved
    y = _surface_y(ex, ez, height_map, ctx)
    if y is None:
        return False
    fx, fz = face if face else (cx, cz)
    yaw = math.degrees(math.atan2(-(fx - ex), (fz - ez)))
    try:
        # Invulnerable+NoGravity+NoAI：定格站桩、免疫全城大火/TNT爆炸/坠落，不消失。
        mc_cmd(f"summon minecraft:{mob} {ex} {y + 1} {ez} "
               f"{{NoAI:1b,NoGravity:1b,Invulnerable:1b,PersistenceRequired:1b,"
               f"Silent:1b,Rotation:[{yaw:.1f}f,0f]}}")
        return True
    except Exception as exc:
        print(f"   ⚠️ summon {mob} 失败：{exc!r}")
        return False


def _summon_invaders(cx: int, cz: int, breach_positions: list, wall_radius: int,
                     plaza_r: int, placed_boxes: list,
                     height_map: np.ndarray, ctx: ScanContext, rng,
                     features=None) -> int:
    """围城大军：每个豁口外摆纵深梯队(3排前锋)+攻城劫掠兽，再从豁口一路推进到城心
    (敌人已杀穿全城)，沿整圈城墙外散布围城营地，房子附近散零星劫掠者。"""
    n = 0
    cols = max(1, RUIN_INVADERS_PER_BREACH // 3)
    for bx, bz, nx, nz in breach_positions:
        tx, tz = -nz, nx                                     # 沿墙切向
        # 豁口外掠夺者纵深梯队（3 排，从近到远）。
        for row in range(3):
            for col in range(cols):
                off_n = 5 + row * 4
                off_t = (col - (cols - 1) / 2) * 2
                ex = int(round(bx + nx * off_n + tx * off_t))
                ez = int(round(bz + nz * off_n + tz * off_t))
                n += _summon_mob(ex, ez, cx, cz, "pillager", height_map, ctx,
                                 features=features)
        # 豁口正外劫掠兽（攻城兽）。
        if RUIN_INVADER_RAVAGER:
            n += _summon_mob(int(bx + nx * 3), int(bz + nz * 3),
                             cx, cz, "ravager", height_map, ctx,
                             features=features)
        # 从豁口一路推进到城心（沿 -法向往城内，每 8 格一个，杀穿全城）。
        d = 4
        while d < wall_radius - int(plaza_r):
            ex = int(round(bx - nx * d + tx * rng.randint(-2, 2)))
            ez = int(round(bz - nz * d + tz * rng.randint(-2, 2)))
            mob = "vindicator" if rng.random() < 0.5 else "pillager"
            n += _summon_mob(ex, ez, cx, cz, mob, height_map, ctx,
                             features=features)
            d += 8

    # 沿整圈方城墙外散布围城营地（4 条边均分），整城被围。
    R = wall_radius + 10
    per_edge = max(1, RUIN_SIEGE_CAMPS // 4)
    for e in range(4):
        for j in range(per_edge):
            pos = -R + (j + 0.5) / per_edge * 2 * R
            if e == 0:
                bx0, bz0 = cx + pos, cz - R
            elif e == 1:
                bx0, bz0 = cx + pos, cz + R
            elif e == 2:
                bx0, bz0 = cx - R, cz + pos
            else:
                bx0, bz0 = cx + R, cz + pos
            for _ in range(RUIN_SIEGE_CAMP_SIZE):
                ex = int(bx0 + rng.randint(-2, 2))
                ez = int(bz0 + rng.randint(-2, 2))
                n += _summon_mob(ex, ez, cx, cz, "pillager", height_map, ctx,
                                 features=features)
            if rng.random() < 0.4:
                n += _summon_mob(int(bx0), int(bz0), cx, cz, "ravager",
                                 height_map, ctx, features=features)

    # 房子附近零星劫掠者（抽一部分建筑，门口/墙角摆个卫道士在劫掠）。
    if placed_boxes and RUIN_LOOT_FRAC > 0:
        k = max(1, int(len(placed_boxes) * RUIN_LOOT_FRAC))
        for box in rng.sample(placed_boxes, min(k, len(placed_boxes))):
            x0, x1, z0, z1 = int(box[0]), int(box[1]), int(box[2]), int(box[3])
            ex = rng.choice([x0 - 2, x1 + 2, (x0 + x1) // 2])
            ez = rng.choice([z0 - 2, z1 + 2, (z0 + z1) // 2])
            n += _summon_mob(ex, ez, cx, cz, "vindicator", height_map, ctx,
                             features=features)
    return n


def _summon_defenders(cx: int, cz: int, breach_positions: list, plaza_r: int,
                      height_map: np.ndarray, ctx: ScanContext, rng,
                      features=None) -> int:
    """守军反击：铁傀儡列阵挡在豁口内侧(朝豁口/城外)，城心一圈守最后防线(朝外)。"""
    n = 0
    # 豁口内侧守军（挡住入口，朝城外迎敌）。
    for bx, bz, nx, nz in breach_positions:
        tx, tz = -nz, nx
        for i in range(RUIN_DEFENDERS_PER_GATE):
            off_t = (i - (RUIN_DEFENDERS_PER_GATE - 1) / 2) * 2
            ex = int(round(bx - nx * 8 + tx * off_t))
            ez = int(round(bz - nz * 8 + tz * off_t))
            n += _summon_mob(ex, ez, cx, cz, "iron_golem", height_map, ctx,
                             face=(int(bx), int(bz)), features=features)
    # 城心最后防线（围灵魂树一圈，各自朝外守卫）。
    R = int(plaza_r) + 3
    for k in range(RUIN_DEFENDERS_CORE):
        ang = 6.283 * k / max(1, RUIN_DEFENDERS_CORE)
        ex = int(cx + R * math.cos(ang))
        ez = int(cz + R * math.sin(ang))
        face = (int(cx + 2 * R * math.cos(ang)), int(cz + 2 * R * math.sin(ang)))
        n += _summon_mob(ex, ez, cx, cz, "iron_golem", height_map, ctx,
                         face=face, features=features)
    return n


def _sky_fireballs(batch: list, cx: int, cz: int, wall_radius: int,
                   height_map: np.ndarray, ctx: ScanContext, rng,
                   n_balls: int, radius: int) -> int:
    """城市上空悬浮燃烧火球：netherrack 核(其上火不灭) + fire 壳，像投石/陨石轰炸。"""
    made = 0
    for _ in range(n_balls):
        a = rng.uniform(0, 6.283)
        r = rng.uniform(0, wall_radius * 0.85)
        fx, fz = int(cx + r * math.cos(a)), int(cz + r * math.sin(a))
        gy = _surface_y(fx, fz, height_map, ctx)
        if gy is None:
            continue
        fy = gy + rng.randint(28, 52)                        # 高空悬浮
        core = (radius - 0.4) ** 2
        shell = (radius + 0.6) ** 2
        for dy in range(-radius, radius + 1):
            for dz in range(-radius, radius + 1):
                for dx in range(-radius, radius + 1):
                    d2 = dx * dx + dy * dy + dz * dz
                    if d2 <= core:                           # 核：magma/netherrack
                        bid = _NETHERRACK if rng.random() < 0.6 else _MAGMA
                        batch.append({"x": fx + dx, "y": fy + dy, "z": fz + dz,
                                      "id": bid})
                    elif d2 <= shell:                        # 壳：贴 netherrack 的火
                        batch.append({"x": fx + dx, "y": fy + dy, "z": fz + dz,
                                      "id": _FIRE})
        made += 1
    return made


def _set_apocalypse_weather() -> None:
    """暴雨 + 白天：白天暴雨雷暴下的废墟战场，雷暴天本身持续打雷。

    注意：此服务端(1.21.11)不向命令源开放 gamerule 子节点（pause_world 也因此
    改用 tick freeze），所以不能用 doWeatherCycle false 锁天气，改用超长持续时间的
    weather thunder（≈277 小时游戏内 = 常驻雷暴）。每条命令独立容错，互不连累。"""
    for cmd in ("weather thunder 1000000", "time set day"):
        try:
            mc_cmd(cmd)
        except Exception as exc:
            print(f"   ⚠️ 天气命令失败 [{cmd}]：{exc!r}")


def apply_ruin(center_x: int, center_z: int,
               height_map: np.ndarray, ctx: ScanContext,
               core_info: dict, wall_radius: int, plaza_r: int,
               placed_boxes: list = None, tree_boxes: list = None,
               batch_size: int = 4096) -> None:
    """建城末尾追加一步：把城市"打成"被入侵毁灭的废墟（纯环境叙事）。"""
    if not RUIN_ENABLED:
        return
    rng = random.Random(f"ruin_{center_x}_{center_z}")
    batch: list = []
    tnt_points: list = []
    features = getattr(ctx, "terrain_features", None)

    # 大图缩放：散布类随面积放大、豁口随周长加宽；小图 scale≈1 不变。
    scale = max(1.0, wall_radius / RUIN_SCALE_BASE_R) if RUIN_SCALE_ENABLED else 1.0
    area_scale = scale * scale
    debris_n = int(RUIN_DEBRIS_COUNT * area_scale)
    rods_n = int(RUIN_LIGHTNING_RODS * scale)
    breach_n = min(4, round(RUIN_WALL_BREACHES * scale))          # 方城最多 4 条边
    breach_len = min(int(RUIN_WALL_BREACH_LEN * scale), RUIN_BREACH_LEN_MAX)
    strikes_n = int(RUIN_LIGHTNING_STRIKES * scale)

    if RUIN_TREE_FIRE and core_info:
        _ruin_soul_tree(batch, core_info, height_map, ctx, rng, tnt_points)
    n_breach, breach_pos = _wall_breaches(batch, center_x, center_z,
                                          int(wall_radius), height_map, ctx, rng,
                                          breach_n, breach_len)
    n_burn = 0
    if RUIN_BURN_BUILDINGS:
        n_burn = _ruin_buildings(batch, placed_boxes or [], height_map, ctx, rng,
                                 RUIN_BUILDING_FRAC, RUIN_BUILDING_FIRES,
                                 RUIN_BUILDING_TNT, tnt_points)
    n_trees = _burn_trees(batch, tree_boxes or [], height_map, ctx, rng)
    _scatter_debris(batch, center_x, center_z, int(wall_radius), int(plaza_r),
                    height_map, ctx, rng, debris_n)
    rod_pts = _place_lightning_rods(batch, placed_boxes or [], height_map, ctx,
                                    rng, rods_n)
    n_ball = 0
    if RUIN_FIREBALLS > 0:
        n_ball = _sky_fireballs(batch, center_x, center_z, int(wall_radius),
                                height_map, ctx, rng,
                                int(RUIN_FIREBALLS * scale), RUIN_FIREBALL_RADIUS)

    # TNT 总数封顶，防大城几百个连环爆卡死。
    if len(tnt_points) > RUIN_MAX_TNT:
        tnt_points = rng.sample(tnt_points, RUIN_MAX_TNT)

    # 分批写盘。
    ok_total = True
    for i in range(0, len(batch), batch_size):
        if not set_blocks_batch(batch[i:i + batch_size]):
            ok_total = False
    print(f"   🔥 废墟化(scale={scale:.2f})：城墙豁口 {n_breach}，烧毁建筑 {n_burn}，"
          f"烧毁树木 {n_trees}，焦土/余烬 ~{debris_n} 处，避雷针 {len(rod_pts)}，"
          f"天降火球 {n_ball}，灵魂树断根燃烧"
          f"{'（写盘有失败）' if not ok_total else ''}")

    # 引爆 primed TNT（世界恢复运行后倒计时真爆炸），随机引信做连环爆。
    n_tnt = 0
    for wx, wy, wz in tnt_points:
        fuse = rng.randint(RUIN_TNT_FUSE_MIN, RUIN_TNT_FUSE_MAX)
        try:
            mc_cmd(f"summon minecraft:tnt {wx} {wy} {wz} {{fuse:{fuse}}}")
            n_tnt += 1
        except Exception as exc:
            print(f"   ⚠️ summon TNT 失败：{exc!r}")
    if n_tnt:
        print(f"   💥 引爆 TNT {n_tnt} 处（连环爆）")

    if RUIN_INVADERS_ENABLED:
        n_inv = _summon_invaders(center_x, center_z, breach_pos, int(wall_radius),
                                 int(plaza_r), placed_boxes or [],
                                 height_map, ctx, rng, features=features)
        if n_inv:
            print(f"   ⚔️  入侵者 {n_inv} 个（纵深梯队+攻城兽+已攻入卫道士+围城营地）")
        if RUIN_DEFENDERS_ENABLED:
            n_def = _summon_defenders(center_x, center_z, breach_pos,
                                      int(plaza_r), height_map, ctx, rng,
                                      features=features)
            if n_def:
                print(f"   🛡️  守军 {n_def} 个（豁口列阵+城心最后防线，铁傀儡反击）")
        if RUIN_INVADER_GLOW:
            for t in ("pillager", "vindicator", "ravager", "iron_golem"):
                try:
                    mc_cmd(f"effect give @e[type=minecraft:{t}] "
                           f"minecraft:glowing 100000 0 true")
                except Exception as exc:
                    print(f"   ⚠️ 发光失败 [{t}]：{exc!r}")

    if RUIN_STORM:
        _set_apocalypse_weather()
        print("   ⛈️  末日天气：雷暴 + 午夜常驻")

    # 主动劈几道闪电（在避雷针处），配合雷暴天持续落雷。
    if rod_pts and strikes_n > 0:
        n_hit = 0
        for wx, wy, wz in rng.sample(rod_pts, min(strikes_n, len(rod_pts))):
            try:
                mc_cmd(f"summon minecraft:lightning_bolt {wx} {wy} {wz}")
                n_hit += 1
            except Exception as exc:
                print(f"   ⚠️ summon 闪电失败：{exc!r}")
        if n_hit:
            print(f"   ⚡ 落雷 {n_hit} 道 + 避雷针持续引雷")


__all__ = ["apply_ruin"]
