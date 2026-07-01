==================================================================
mc_city 与 legacy/ 旧实现的对比文档
==================================================================

本文对比当前的 mc_city/ 包与 legacy/ 目录里的旧实现（最后一版是
main_patched_v3.py + city_builder_patched_v3.py 等，文件名带
_patched_v2 / _patched_v3 表明历经多轮 hot-patch）。

旧版的根本问题集中在四点：树木混在高度图里、缓存不验证参数、HTTP
静默失败、扫描区域写死。新版用"源头标记 + 上游过滤 + 兜底校验"
的三层防御重构了整条流水线，并把脚本拆成 Python 包。


------------------------------------------------------------------
1. 项目结构
------------------------------------------------------------------

Legacy：
    legacy/ 目录平铺，多版本并存：
        main.py / main_patched_v2.py / main_patched_v3.py
        city_builder.py / city_builder_patched_v2.py / city_builder_patched_v3.py
        scan_tool.py / paste_tool.py / block_codec.py / ...
    常量与路径散落在各文件，COMPONENT_ROOT = "components" 用
    cwd 相对路径，IDE 启动和命令行启动结果不一致。

当前：
    mc_city/                            Python 包
        main.py                         入口（python -m mc_city.main）
        config.py                       路径 / 半径 / 常量集中
        mc/                             与 GDMC HTTP 交互
            blocks.py / codec.py / placement.py / command.py
        scan/                           扫描与坐标系
            scanner.py / height_map.py / coord_frame.py
        city/                           城市构建
            builder.py / center.py / components.py
            foundation.py / placement.py / suitability.py
            terrain.py / trees.py / wall.py
        roads/                          道路
            system.py / network.py / pathfinding.py
            renderer.py / collision.py
        modular/                        模块化建筑
            builder.py / parts.py


------------------------------------------------------------------
2. 扫描区域：从硬编码到跟随玩家
------------------------------------------------------------------

Legacy（main_patched_v3.py:151-155）：
    scan_cx, scan_cz = 128, 128
    scan_radius = 256
    x1, x2 = scan_cx - scan_radius, scan_cx + scan_radius
    z1, z2 = scan_cz - scan_radius, scan_cz + scan_radius
    y1, y2 = -64, 100
扫描中心写死在 (128, 128)。换世界、远离世界原点时，扫到的是无关地形。

当前（mc_city/main.py:166-211）：
    1) 先调 getBuildArea() 读 /setbuildarea 圈定的范围
    2) 若该命令在当前 MC 版本不可用，自动 fallback 到 getPlayers()，
       以第一个在线玩家位置为中心，半径 256
    3) Y 范围裁到 [-64, 100]，避免 1.21 全高 (-64~320) 撑爆内存

效果：扫描区域始终跟着玩家走。


------------------------------------------------------------------
3. 缓存：从"看文件存不存在"到"参数指纹校验"
------------------------------------------------------------------

Legacy（main_patched_v3.py:172-201）：
    只 os.path.exists 判存在，没有任何参数指纹。改了扫描范围后，
    必须手动删 scan_blocks_compact.npy / height_map.npy / block_codec.json，
    否则一直加载旧的脏数据。

当前（mc_city/main.py:42-87, 178-195）：
    1) 新增 data/scan_meta.json，保存上一次扫描使用的 x1/x2/y1/y2/z1/z2
    2) 启动时对比当前参数与 meta，不一致就把 5 个缓存文件一起作废
       （_SCAN_CACHE_FILES 列出全部受影响文件）
    3) 还支持 --rescan 命令行 flag 强制重扫
    4) 旧缓存缺 meta 文件时一次性补写，迁移友好

效果：换 buildarea、改 y2 等任何扫描参数变化都会自动重扫，
不用手动清缓存。


------------------------------------------------------------------
4. HTTP 通信：从静默失败到带重试
------------------------------------------------------------------

Legacy（legacy/paste_tool.py:8-23）：
    timeout = 10 秒（大批量发送容易超时）
    异常被 print 后吞掉
    返回 True / False，但几乎所有调用方都没检查
    没有重试

当前（mc_city/mc/placement.py:18-50）：
    timeout = 60 秒，常量 HTTP_TIMEOUT 暴露
    3 次重试，指数退避（1s / 2s / 4s）
    失败到达上限才返回 False，并打印 [GIVE UP] 明确日志
    所有调用方（roads / wall / foundation / modular / trees）都受益


------------------------------------------------------------------
5. 树木清除：从"先记账后发送"到"原子化"
------------------------------------------------------------------

Legacy（city_builder_patched_v3.py:742-763）：
    batch.append(...)
    cleared += 1                            # 先计数
    if mutate_scan:
        scan_volume[yi, zs, xs] = air_code  # 先改内存
    if len(batch) >= batch_size:
        set_blocks_batch(batch)              # 最后发 HTTP，返回值不管

HTTP 超时时：内存以为清空了，世界里树仍在 → 后续选址用的
scan_volume 与现实脱节 → 建筑落到树冠上 / 旁边幽灵树。

当前（mc_city/city/trees.py:45-100）：
    维护并行的 batch_positions 列表
    调 set_blocks_batch，只有返回 True 时才递增 cleared、回写 scan_volume
    失败的批次单独计入 failed，打印
        ⚠️ 树木清除：成功 X / 失败 Y
    clear_trees_in_footprint 也按同样模式重写


------------------------------------------------------------------
6. 高度图：从"含树冠"到"跳树 + 撞顶检测"
------------------------------------------------------------------

这是最关键的一处。Legacy 的高度图基本算错了，污染所有下游。

Legacy（scan_tool.py:119-166）：
    从顶往下扫，第一个非空气方块就当作地表
    没有"跳过树木"逻辑 → 森林区里 height_map 记录的是树叶顶
    纯 Python 三重循环，慢

当前（mc_city/scan/height_map.py）：
    1) skip_trees = True 默认开启，跳过
       _log / _wood / _leaves / vine / bamboo / azalea_leaves / ...
       （mc/blocks.py:50-68 的 is_tree_block_id）
    2) uint16 矢量化路径：np.isin + np.argmax 取代三重循环，~200x 加速
    3) 撞天花板检测（新特性）：
       某列顶层 (scan y = NY-1) 是 surface → 真实地形 > 扫描 y2
       → 该列标记为 min_y（视作"无效列"）。
       这一步根治了"建筑悬空在 y=99"的问题——以前山顶被截到 99
       后会被当成"y=99 大片平地"，建筑全往那里堆。
    4) 日志同时区分有效列 / 无效列：
       🌄 Height map: surface found in 174832/262144 列，min/max=29/87
          ⚠️  85198/262144 列地形超出扫描天花板 (y=99)，已标记为无效。


------------------------------------------------------------------
7. 选址：上游过滤与下游兜底
------------------------------------------------------------------

Legacy：直接拿 height_map 算坡度 / 方差，没有"无效列"概念。

当前的三层防御：

    源头（scan/height_map.py）
        撞顶列 / 无 surface 列 → 都标记为 min_y sentinel

    上游过滤（city/suitability.py:39-41）
        compute_suitability_map 对 height_map <= ctx.min_y 的格子
        直接 suitability = 0

    下游兜底（city/placement.py:217-219）
        place_buildings_grid 检测 footprint 内是否含 sentinel，
        含则跳过该候选

模块化建造（mc_city/modular/builder.py）用的也是 suitability_map
→ 自动受益。


------------------------------------------------------------------
8. 模块化建筑（住宅 / 商铺）
------------------------------------------------------------------

定位：圈层内非"特征建筑"的填充建筑，按地形动态拼装。

数据流（mc_city/modular/builder.py）：

    suitability_map ≥ 0.45 → 二值化
        ↓
    圈层环带 mask 相与（限制在 r_min..r_max）
        ↓
    scipy.ndimage.label → 连通适宜区
        ↓
    每个连通域：largest_interior_rectangle（直方图 + 单调栈 O(NZ·NX)）
        ↓
    split_rectangle → 切成 5..16 见方的小地块
        ↓
    assemble_building 逐地块拼装：
        base_y = max(footprint 高度)
        逐格找 ground_y < base_y 的，gen_foundation_column 拉地基柱
        gen_floor_plate / gen_wall_section / gen_roof_*
        门朝向城市中心（依 dx/dz 算 east/west/north/south）
        楼层数：inner ≤ 2，mid 看面积，outer = 1

地块切分逻辑：
    > 16 见方时按 ceil/2 切两半
    最小不低于 5×5 否则丢弃

与 legacy 的差别：
    算法基本一致，主要是从 modular_builder.py + part_generator.py
    单文件，重组进 mc_city/modular/ 子包并加类型标注。
    无功能性增强。

当前还在的问题：
    a) 切分出的地块边缘紧贴 → 屋顶相互堆叠时观感像"一栋大体块"，
       没有巷道。
    b) base_y = max(footprint) 在地形微起伏时会让室内地板比真实地面
       高出 1~2 格，地基柱掩盖了 → 看起来是一个"高台"。
    c) 楼层数随机不考虑相邻地块高度，密集区可能高低错落严重。


------------------------------------------------------------------
9. 城墙
------------------------------------------------------------------

入口：mc_city/city/wall.py 的 build_city_wall。

流程：
    1) 沿半径 WALL_RADIUS（默认 190）取圆周点，
       circumference = 2πr + 4 个采样
    2) 每点查 height_map 得 ground_y；
       水柱列（terrain_map == 4）向下扫 scan_volume，找水底实体地面
       （跳过 water / flowing_water / kelp / seagrass / air）→
       墙从水底起，不悬在水面
    3) gate_interval 度间隔留门洞，门宽 gate_width
    4) tower_interval 度间隔放塔楼
    5) ground_y 滑动平均（window=5）减少锯齿
    6) 相邻段 ground_y 落差 → 垂直填充使之连续
    7) 与已放建筑包围盒相交时跳过该段（_overlaps_building）

材质（按当前点的 terrain）：
    plains   stone_bricks       + stone_brick_wall
    desert   sandstone          + sandstone_wall
    snow     smooth_stone       + stone_brick_wall
    mountain deepslate_bricks   + deepslate_brick_wall

与 legacy 的差别：
    从 city_builder_patched_v3.py:1499 抽到 city/wall.py 独立模块，
    类型标注 + 内部函数加 _ 前缀；逻辑等价，无功能性增强。

当前还在的问题：
    a) wall 用 height_map 取 ground_y，没读取本次新增的"无效列
       sentinel"。若圆周经过被截顶的山，wall 会跳到 y=-64 处
       （sentinel = min_y），生成深坑里的城墙片段。
       → 修复方案：参考 city/placement.py 的兜底检查，
         遇到 ground_y <= ctx.min_y 时跳过这一段或就近插值。
    b) 滑动平均仅 window=5，跨越较大落差时仍会出现"楼梯式"突变。
    c) 门洞和塔楼按角度划分，不考虑该角度上是否恰好在悬崖；
       门有可能开在 5 格落差的悬崖上。


------------------------------------------------------------------
10. 道路 —— 自然融入地形
------------------------------------------------------------------

入口：mc_city/roads/system.py 的 SmartRoadSystem。

骨架（generate_structural_roads）：
    每个圈层在中线半径处绕一圈
    从中心向外射 radial_count 条放射道路（默认 8）
    返回 backbone_nodes 给建筑选址作距离评分

建筑接入（connect_buildings_to_nearest_road）：
    用 RoadPathfinder（A*）从建筑边缘找最近骨架节点

渲染（roads/renderer.py）—— 关键的"自然融入"逻辑：
    每个中心线点独立查 _terrain_y
    每格按局部坡度 / 是否水柱 选择材质：
        水柱（y < SEA_LEVEL - 1）
            清掉水柱，oak_planks 桥 + 边缘 oak_fence 栏杆
        陡坡（slope ≥ 4）
            oak_stairs，朝向上坡邻居
        缓坡（slope ≥ 2）
            stone_slab
        平地
            配置的 road_block（默认 cobblestone）
    每个路段上方 3 格清空气（防止穿过悬空树叶 / 山体）

与 legacy 的差别：
    路径渲染算法本身与 legacy/road_renderer.py 几乎一致；
    重构成更小的 class + 常量 SEA_LEVEL 来自 config.py。
    slope 阈值、楼梯朝向、桥的细节均保留。

当前还在的问题（你说道路融入还有问题，这部分集中讲）：

    a) sentinel 列没被道路 renderer 识别。_terrain_y 拿到 min_y
       时直接当地表用 → 在被截顶的山区附近，路面会"潜入"地底
       y=-64。
       → 修复方案：renderer._terrain_y 增加判断
         if int(self.height_map[zs, xs]) <= MIN_VALID_Y: return None
         上层在 _create_road_section 已经处理了 None。

    b) _local_slope 只看 4 个直接邻居（曼哈顿距离 1），对 2~3 格
       范围的快速过渡不敏感。一条路在 y=70→y=68→y=66→y=64 的
       逐步下坡上每个邻居只差 2，全部判为"缓坡石板"——但视觉上
       是连续下降的台阶应该用楼梯。
       → 修复方案：把 _local_slope 改成看 ±2 范围最大差，
         阈值同步调整。

    c) 楼梯朝向逻辑（_stair_facing）按"指向上坡邻居"，
       但在十字交叉点会与相邻路段方向冲突 → 楼梯朝向反向，
       视觉上像"反向滑梯"。
       → 修复方案：交叉点强制水平（slab 或 road_block），
         不放楼梯。

    d) 桥的清水只清 col_y+1 到 SEA_LEVEL（含），但有些水深超过
       sea level 的列里 col_y > SEA_LEVEL（极少数 lake 内陆），
       渲染会失败。
       → 修复方案：clamp 桥面高度到 max(col_y+1, SEA_LEVEL)。

    e) 道路从未调用 clear_trees_in_footprint：
       道路穿过森林时，仍会把石板 / 楼梯放在树叶里面，从外面看
       像"埋在树冠里的路面"。
       → 修复方案：在 _create_road_section 前对路径 footprint
         调 clear_trees_in_footprint（带 ±road_width//2 padding）。

    f) 路与城墙交叉时没有任何特殊处理：
       骨架道路的最外圈半径 = 230，城墙半径 = 190，
       outer 环路绕在城墙外侧没问题；
       但 radial 放射线从 0 一路射到 outer，会从内向外穿城墙。
       目前 wall._overlaps_building 不检查道路，wall 直接盖到
       道路上 → 路被挡死。
       → 修复方案：放射道路与城墙交点位置预留 gate_width 的开口，
         或者把 gate_angles 与 radial 数量对齐（8 条放射 +
         8 个门，每 45°）。


------------------------------------------------------------------
11. 其它细节差异
------------------------------------------------------------------

存储格式：
    Legacy：dict 元素的 object ndarray（每格一个 {"id": "..."}）
            512×512×164 ≈ 7 GB
    当前：BlockCodec 把方块名编码成 uint16，相同尺寸 ≈ 27 MB

路径：
    Legacy：相对 cwd，IDE 启动和命令行启动结果不一致
    当前：config.py 用 os.path.dirname(__file__) 反推项目根，
           所有路径都是绝对路径

命令行：
    Legacy：无参
    当前：--rescan 强制重扫

命令调用：
    Legacy：mc_cmd 自己解析 runCommand 文本判错
    当前：mc/command.py 同思路，但移到独立模块

旋转：同（0 / 90 / 180 / 270）
圈层：同（inner / mid / outer，RADIUS_MAP）


------------------------------------------------------------------
12. 仍存在的问题（汇总）
------------------------------------------------------------------

针对各模块"自然融入环境"还在的问题，统一列在这里方便后续处理：

    sentinel 兼容：
        高度图新增的撞顶 sentinel (= min_y) 已在 suitability /
        placement 处理，但 wall / road renderer / modular 的
        直接 height_map 读取没读这个 sentinel，遇到截顶山区会
        把方块铺到 y=-64。

    道路：
        slope 判定半径太小 / 交叉点楼梯朝向冲突 / 桥水深 clamp /
        穿森林不伐树 / 与城墙交点没开门洞

    城墙：
        门 / 塔不避开陡坎；ground_y 平滑窗口 5 太小

    模块化建筑：
        地块边缘紧贴无巷道；base_y 取 max 导致"高台感"；
        楼层数与邻居无关

    gamerule 报错：
        本机 MC 版本的 gamerule 命令解析与 GDMC HTTP 桥兼容性
        问题，不影响主流程。

    /setbuildarea 不可用：
        某些 MC 版本里这条命令不存在，main.py 已加 getPlayers()
        fallback。


------------------------------------------------------------------
13. 升级路径速查
------------------------------------------------------------------

如果只看"为什么要从 legacy 升上来"，对照 § 4 / 5 / 6 三节就够了——
它们对应实际踩到的三个 bug：

    1. HTTP read timeout 频发        → § 4 timeout 60s + 重试
    2. 树没真清掉                    → § 5 原子化清除（HTTP 成功才记账）
    3. 建筑悬浮在 y=99               → § 6 撞顶检测 + § 7 上游过滤

每一项在 legacy 都缺失。
