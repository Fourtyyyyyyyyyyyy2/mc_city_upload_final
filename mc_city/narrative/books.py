"""叙事图层：灵魂树下六角讲台 + 6 本故事书（任务 1.4）。

公开 API：
    BOOK_CHAPTERS          —— 6 本书的初稿（title/author/pages）
    place_story_books(...) —— 在灵魂树外围 6 个 60° 角放讲台，每个讲台预放对应章节
    build_book_payloads(...) —— dry-run 友好：只生成 set_blocks_batch payload，不调 HTTP

设计要点：
- 6 本书围灵魂树呈正六边形布置，r = LECTERN_RADIUS_FROM_CENTER（默认 35，
  对应 60×60 灵魂树外缘 +5 格呼吸）。
- 讲台 facing 朝灵魂树心。lectern 是 block + has_book=true。
- written_book NBT 用 1.20.5+ components 路径格式（1.21.11 实测要求）：
  count:1, components:{"minecraft:written_book_content":{title:{raw:...},
  author:"...",pages:[{raw:"..."},{raw:"..."}]}}。旧 tag 路径在 1.21.11
  上 lectern 显示"无书内容"而无法翻阅。
- 任何一页 > BOOK_PAGE_CHAR_LIMIT 直接 print warning 但仍写入（MC 客户端会自截）。
- 不接入 builder.py 主流程（任务 1.5 才接入）。本模块只暴露 API 供 1.5 调度。
"""
from __future__ import annotations

import json
import math
from typing import Iterable, Optional

import numpy as np

from ..mc.placement import set_blocks_batch
from ..scan.coord_frame import ScanContext

# 灵魂树 100×100 cropped，footprint 半径 ~50。讲台 r=35 在 footprint 内边缘。
# 历史：v1 用 r=35（60×60 树外缘），v2 改 r=60（在 100×100 树外面）但 footprint
# 外的 terraform 没作用，6 讲台漂浮在原地形上 15~25 格。v3 改回 r=35：讲台
# 在 footprint 内，地面已被 terraform fill 到 base_y，base_y+1 不漂浮，
# 视觉上是"在灵魂树枝叶下的讲台"，主题反而更对。
LECTERN_RADIUS_FROM_CENTER = 35
# 灵魂树 footprint 半径（100×100 中心，从 _place_core 推断 center 时用）
TREE_FOOTPRINT_RADIUS = 50
# MC 客户端单页硬约束约 256 字符。任务卡要求 < 200，留余地写到 220 才警告。
BOOK_PAGE_CHAR_LIMIT = 220


BOOK_CHAPTERS: list[dict] = []  # 6 本中文故事书已删除（2026-07-01 改为环境叙事实景）。
# 故事弧仍保留于 docs / 环境实景模块：灵树天降→四公会立约→灵核之炽→北方铁影→断根之夜→幸存者。


# ── 公共入口 ──────────────────────────────────────────────────────────
def place_story_books(soul_tree_origin: tuple[int, int, int],
                      height_map: np.ndarray,
                      ctx: ScanContext,
                      center_x: Optional[int] = None,
                      center_z: Optional[int] = None,
                      tree_radius: int = TREE_FOOTPRINT_RADIUS,
                      lectern_radius: int = LECTERN_RADIUS_FROM_CENTER,
                      base_y: Optional[int] = None) -> int:
    """在灵魂树外围六边形位置放 6 个讲台 + 对应章节书。返回成功放置的数量。

    Args:
        soul_tree_origin: 灵魂树 paste 时的 origin (x, base_y, z)。
            origin[1] 即灵魂树 terraform 后的 base_y；本函数默认用它统一所有讲台高度
            （祭坛环效果），可显式传 base_y 覆盖。
        height_map: (NZ, NX) int32；base_y=None 时各讲台读各自 ground_y + 1。
        ctx: ScanContext。
        center_x/z: 城市中心；省略则用 soul_tree_origin + tree_radius 推断。
        tree_radius: 灵魂树 footprint 半径（默认 50 = 当前 100×100 cropped 版本）。
        lectern_radius: 讲台到中心的距离。
        base_y: 所有讲台齐平的统一高度。None → 推断 = soul_tree_origin[1]。

    Returns:
        实际写入的 lectern + soul_lantern 总块数（≤ 6 + 6 = 12）。
    """
    if center_x is None:
        center_x = soul_tree_origin[0] + tree_radius
    if center_z is None:
        center_z = soul_tree_origin[2] + tree_radius
    if base_y is None:
        base_y = int(soul_tree_origin[1])

    payloads, skipped = build_book_payloads(
        height_map, ctx, center_x, center_z,
        lectern_radius=lectern_radius, base_y=base_y)
    if skipped:
        print(f"  ⚠️ 跳过 {skipped} 个讲台（地形挡住或超出扫描范围）")
    if not payloads:
        print("  没有可放置的故事书讲台")
        return 0
    if set_blocks_batch(payloads):
        return len(payloads)
    print(f"  ⚠️ 故事书讲台批次写入失败（{len(payloads)} 块）")
    return 0


# ── dry-run 可见的核心：构造每个讲台的 PUT payload ────────────────
def build_book_payloads(height_map: np.ndarray,
                        ctx: ScanContext,
                        center_x: int,
                        center_z: int,
                        lectern_radius: int = LECTERN_RADIUS_FROM_CENTER,
                        base_y: Optional[int] = None,
                        ) -> tuple[list[dict], int]:
    """计算 6 个讲台 + 6 个 soul_lantern 装饰 payload。不调 HTTP，dry-run 友好。

    base_y 给定 → 所有讲台和 lantern 站这个高度（祭坛环效果）。
    base_y=None → 退到旧行为：各讲台读各自 ground_y。

    Returns (payloads, skipped_count)。lectern 与 BOOK_CHAPTERS 一一对应；
    每个 lectern 后接一个 soul_lantern（在 lectern 朝外偏 1 格）。
    """
    NZ, NX = height_map.shape
    positions = lectern_positions(center_x, center_z, lectern_radius)

    payloads: list[dict] = []
    skipped = 0
    for i, (wx, wz) in enumerate(positions):
        if i >= len(BOOK_CHAPTERS):
            break
        xs, zs = ctx.w2s(wx, wz)
        if not (0 <= xs < NX and 0 <= zs < NZ):
            skipped += 1
            continue

        # 高度：祭坛环 base_y / 各自地表
        if base_y is not None:
            y_here = int(base_y) + 1
        else:
            ground_y = int(height_map[zs, xs])
            if ground_y <= ctx.min_y:
                skipped += 1
                continue
            y_here = ground_y + 1

        facing = _facing_toward_center(wx, wz, center_x, center_z)
        chapter = BOOK_CHAPTERS[i]
        block_id = _lectern_block_id(facing, chapter)
        payloads.append({"x": int(wx), "y": y_here, "z": int(wz),
                         "id": block_id})

        # 装饰：lectern 朝心反方向偏 1 格放 soul_lantern，
        # 玩家从城内方向来读 lectern 时 lantern 在 lectern 背后亮起。
        lx, lz = _lantern_offset(wx, wz, center_x, center_z)
        payloads.append({"x": int(lx), "y": y_here, "z": int(lz),
                         "id": "minecraft:soul_lantern[hanging=false]"})
    return payloads, skipped


def _lantern_offset(wx: int, wz: int, cx: int, cz: int) -> tuple[int, int]:
    """从 lectern 位置朝远离中心方向偏 1 格。整数 chebyshev 单位向量。"""
    dx = wx - cx
    dz = wz - cz
    norm = max(abs(dx), abs(dz)) or 1
    return wx + round(dx / norm), wz + round(dz / norm)


def lectern_positions(cx: int, cz: int, r: int) -> list[tuple[int, int]]:
    """围中心 (cx, cz) 在半径 r 的圆上取 6 个 60° 间隔的整数点。

    第 0 个在正东 (theta=0)，逆 MC 默认 +Z 向南的世界，按 math.cos/sin 顺序
    生成 east → south → west → north 方向。
    """
    out: list[tuple[int, int]] = []
    for k in range(6):
        theta = math.radians(60 * k)
        wx = int(round(cx + r * math.cos(theta)))
        wz = int(round(cz + r * math.sin(theta)))
        out.append((wx, wz))
    return out


def _facing_toward_center(wx: int, wz: int,
                          cx: int, cz: int) -> str:
    """讲台 facing 取朝向中心的 4 正方向中最接近的一个。"""
    dx = cx - wx
    dz = cz - wz
    if abs(dx) >= abs(dz):
        return "east" if dx >= 0 else "west"
    return "south" if dz >= 0 else "north"


# ── lectern + written_book SNBT 拼接 ──────────────────────────────
def _lectern_block_id(facing: str, chapter: dict) -> str:
    """拼 `minecraft:lectern[...]{Book:{...written_book...}, Page:0}`。

    用 1.20.5+ components 路径格式（1.21.11 实测要求；旧 tag 路径下
    lectern 显示但无法翻阅）。结构：
        Book:{
          id:"minecraft:written_book",
          count:1,
          components:{
            "minecraft:written_book_content":{
              title:{raw:"标题"},
              author:"作者",
              pages:[{raw:"第一页"},{raw:"第二页"}]
            }
          }
        }
    """
    title = chapter["title"]
    author = chapter["author"]
    pages = chapter["pages"]

    # 每页超长直接 warn（MC 客户端硬约 256 截断）
    for i, p in enumerate(pages):
        if len(p) > BOOK_PAGE_CHAR_LIMIT:
            print(f"  ⚠️ 《{title}》第 {i + 1} 页 {len(p)} 字（> {BOOK_PAGE_CHAR_LIMIT}），"
                  "MC 客户端会截断")

    pages_snbt = ",".join(_page_to_raw_component(p) for p in pages)
    title_snbt = _snbt_string(title)
    author_snbt = _snbt_string(author)

    book_content = (
        f'{{title:{{raw:{title_snbt}}},author:{author_snbt},'
        f'pages:[{pages_snbt}]}}'
    )
    book_components = f'{{"minecraft:written_book_content":{book_content}}}'
    book_item = (f'{{id:"minecraft:written_book",count:1,'
                 f'components:{book_components}}}')
    return (f"minecraft:lectern[facing={facing},has_book=true,powered=false]"
            f"{{Book:{book_item},Page:0}}")


def _page_to_raw_component(text: str) -> str:
    """把一页文本包成 components 路径下的 {raw:"..."}（text component）。

    示例：text='余生于...' → '{raw:"余生于..."}'
    """
    return f"{{raw:{_snbt_string(text)}}}"


def _snbt_string(text: str) -> str:
    """SNBT 简单字符串字面量（用双引号包裹，内部 escape）。

    用于 title / author / raw 内容这类纯文本字段。
    """
    json_str = json.dumps(text, ensure_ascii=False)  # 已含外层 "
    return json_str  # 直接用 JSON 字符串当 SNBT 字符串字面量（双引号语义一致）


def _chunked(seq: list, n: int) -> Iterable[list]:
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


__all__ = [
    "BOOK_CHAPTERS", "LECTERN_RADIUS_FROM_CENTER", "BOOK_PAGE_CHAR_LIMIT",
    "place_story_books", "build_book_payloads",
    "lectern_positions",
]
