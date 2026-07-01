# mc_city — Codex 工作守则

## 项目目标
本项目参加 GDMC 2026 比赛（官方赛道，评分四项：Adaptation 30% / Functionality 25% / Narrativity 25% / Aesthetics 20%）。
目标：在一个 Minecraft 玩家所在区域（半径 256）程序化生成一座"灵魂树城邦"——
中心是巨大的灵魂树，周围是四大公会（学院/工程院/商人协会/冒险者协会）建立的城市，
最终呈现为"被入侵后毁灭"的废墟状态，但通过书本/告示牌让玩家穿越时间线读懂故事。

总生成耗时不能超过 10 分钟。

## 你（Codex）的角色
你是这个项目的执行 agent。你的工作模式：
1. **接到任务后先读 UPGRADE_FROM_LEGACY.md 和当前涉及的源文件**，对齐已有约定。
2. **不主动重构已有模块**，除非任务里明确要求。新功能优先用"新增模块 + 在入口追加一步"的方式接入。
3. **每完成一个子任务就停下来**，输出修改清单，等我确认再做下一个。**不要一次改十几个文件。**
4. **不要假设**。不清楚就问。下面"禁区"里的几类事是高危区，必须问。

## 项目结构铁律（不要打破）
```
mc_city/
    main.py             入口，--rescan 强制重扫
    config.py           所有路径/半径/常量。不要散到其他文件
    mc/                 GDMC HTTP 通信（codec、batch、command）
    scan/               扫描和 height_map
    city/               城市主流程（builder.py 是编排器）
    roads/              道路系统
    modular/            模块化建筑（住宅/商铺）
    narrative/          【可能新增】叙事图层（告示牌、书本、命名）
```

- **所有新模块必须放在合适的子包里**，不要在根目录新建散文件。
- **路径常量加到 config.py**，不要在新模块里写 `os.path.join(...)`。
- **HTTP 调用走 mc/placement.py 的 set_blocks_batch**，不要自己写 requests。
- **缓存文件加到 config.py 的 _SCAN_CACHE_FILES**，否则 --rescan 不会清它。

## 已知设计决策（不要"优化"掉）
这些是经过痛的教训定下来的，不要因为"看起来可以简化"就改：

1. **height_map 的 sentinel = min_y**：表示"无效列"（撞扫描天花板或全空气）。
   下游所有 height_map 读取者**必须**判 `height_map[zs, xs] <= ctx.min_y`，否则会把方块铺到 y=-64。
   见 UPGRADE_FROM_LEGACY.md §6-7。

2. **scan_volume 和 height_map 是两条平行真相**：清树后 scan_volume 原地改 + height_map 重算。
   不要假设它们任何时候完全同步。

3. **set_blocks_batch 失败必须有兜底**：返回 False 时不能更新内存状态，
   否则会出现"以为放了实际没放"的鬼魂方块。见 UPGRADE_FROM_LEGACY.md §5。

4. **建筑选址三层防御**：source（height_map sentinel）→ filter（suitability）→ fallback（placement）。
   新增的选址用模块必须接入这三层之一，不能凭空判可建。

5. **scan_volume 用 uint16 + codec**，不要回退到 dict / object array（内存差 200 倍）。

## 禁区（必须先问我）
遇到以下情况，**停下来问**：
- 想动 city/builder.py 主编排顺序的（[0]~[9] 那 10 步）
- 想改 config.py 的 RADIUS_MAP / WALL_RADIUS / SEA_LEVEL 的
- 想改 scan/height_map.py 或 mc/placement.py 的
- 想新增依赖（除已有的 numpy/scipy/requests/tqdm 之外）的
- 想跑全流程测试（生成耗时长，先用单元测试或 dry-run）的

## 工作方式
- **改之前**：`git status` 看清楚，必要时让我先 commit。
- **改完后**：列出修改的文件 + 修改要点（不超过 10 行 bullet），等我确认。
- **不要写超长 docstring**：每个新函数 3-5 行说明足够。
- **不要静默 except**：所有异常要么处理要么 print 出来，不能吞。
- **新功能默认有 feature flag**：通过 config.py 或函数参数开关，方便我 A/B。

## 当前任务
（每次开新任务时，把这一节替换成具体任务描述。任务描述模板见 docs/task_template.md。）
