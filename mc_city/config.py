"""项目级常量与文件路径。

所有外部资源（components/、缓存 npy、json 编码表）都从项目根目录加载。
其它模块只读取本文件，不要自己拼路径。
"""
import os

# 项目根 = mc_city/..
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 组件库（建筑 .npy 仓库）— 手动维护的资产
COMPONENT_ROOT = os.path.join(ROOT_DIR, "components")

# 数据/缓存目录（所有 JSON / npy 缓存放这里）
DATA_DIR = os.path.join(ROOT_DIR, "data")
GLASS_FRAME_BACKUP_DIR = os.path.join(DATA_DIR, "component_backups", "glass_frame")

# 手动维护：方块名 → class_id 分类表（删了不会自动重建）
BLOCK_CLASS_MAP_PATH = os.path.join(DATA_DIR, "block_class_map_from_blocks_json.json")

# 缓存（删了下次运行会自动重建）
BLOCK_CODEC_JSON        = os.path.join(DATA_DIR, "block_codec.json")
SCAN_BLOCKS_NPY         = os.path.join(DATA_DIR, "scan_blocks.npy")
SCAN_BLOCKS_COMPACT_NPY = os.path.join(DATA_DIR, "scan_blocks_compact.npy")
HEIGHT_MAP_NPY          = os.path.join(DATA_DIR, "height_map.npy")
# 扫描参数元数据（x1..z2, y1..y2）。参数不一致时缓存自动失效
SCAN_META_JSON          = os.path.join(DATA_DIR, "scan_meta.json")

# 第一次运行时自动创建 data/ 目录
os.makedirs(DATA_DIR, exist_ok=True)

# GDMC HTTP 接口
DEFAULT_HOST = "http://127.0.0.1:9000"

# Large GDMC maps can be 1000x1000. Scanning that full area is too slow for
# the 10 minute target, so main.py caps the detailed scan and keeps the
# original size only as a visual-scale hint for far monster landmarks.
MAX_DETAILED_SCAN_SIZE = 512
LARGE_BUILDAREA_VISUALS_ENABLED = True

# 城市圈层半径配置 (r_min, r_max)
RADIUS_MAP = {
    "inner": (20, 80),
    "mid":   (100, 180),
    "outer": (200, 260),
}

# 城墙半径（位于 mid 和 outer 之间）
WALL_RADIUS = 190
# 城墙形状："square"=方形城墙（中式方城，半边长=WALL_RADIUS），"circle"=旧圆形。
# 方形时门洞落 4 条边中点（gate_angles 0/90/180/270），塔楼放 4 个角。
WALL_SHAPE = "square"

# Chinese-style wall skin. This only changes the visual blocks when the
# current environment resolves to the "chinese" building style.
CHINESE_WALL_STYLE_ENABLED = True
CHINESE_WALL_TOWER_HALF_SIZE = 3
# 城墙横截面宽度（格）：中式/非中式统一。奇数最自然（中线压在 perimeter 点上，两侧对称）。
WALL_WIDTH = 5
# True：整圈墙顶拉平到「最高有效地表点 + wall_height」，雉堞齐平（低处墙从地表建到统一顶）。
# 塔顶随统一墙顶再抬高。False = 旧行为（墙顶随各点地形起伏）。
WALL_FLAT_TOP = True

# 自适应：[3] 城市底板填水后，重算 is_water/height_map。否则填出来的陆地仍被旧
# is_water 当水 → enumerate_blocks 判 blocked → 海岸/沼泽地填好也全空。GDMC 适应核心。
WATER_REFRESH_AFTER_FLOOR = True

# 海平面 Y 坐标（Minecraft 默认）
SEA_LEVEL = 63

# ── 海城模式（卡 17.1）：水上吐脚城，不填水、楼浮海面、路成栈桥 ────────────
# True：水当 SEA_LEVEL 可建（suitability/选址放行）+ 跳过 fill_water_only（留水可见）
# + A* 路在水上走 SEA_LEVEL（栈桥）。水上楼自动走 water reskin（深橡木+海晶）+ 破船。
# 对纯陆地图无副作用（没水可建）。吊脚桩腿留 v2。默认开，A/B 关回退填海旧行为。
SEA_CITY_ENABLED = True
SEA_CITY_WATER_WEIGHT = 0.85    # 海城时水格的 suitability 地形权重（当平地算）
# 水上吊脚楼：footprint 多数在水上时，甲板抬到 SEA_LEVEL+offset，实心填土→栅栏腿+
# 露水面（即使 terraform 主路成功也强制吊脚化）。water_frac 超阈值才算"水上楼"。
SEA_CITY_STILT_OVER_WATER = True
SEA_CITY_STILT_DECK_OFFSET = 2        # 甲板高出海平面格数
SEA_CITY_STILT_WATER_FRAC = 0.4       # footprint 水格占比 ≥ 此值 → 当水上楼吊脚化

# ── 叙事图层（Priority 1，task 1.3+） ────────────────────────────
# 旧叙事：告示牌/故事书/路牌。评审不会细读文字，改用环境废墟叙事（见下 RUIN_*），
# 故默认关。=True 恢复文字叙事层。
NARRATIVE_ENABLED = False

# 旧环境实景：盔甲架小人偶 3 幕（用户反馈"看不出来"），已被 RUIN 环境废墟取代，默认关。
NARRATIVE_TABLEAUX_ENABLED = False

# ── 环境废墟叙事（Narrativity 重做）─────────────────────────────
# 建城末尾把城市"打成"被入侵毁灭的废墟：灵魂树断根燃烧、城墙攻破豁口、城内焦土/
# 余烬烟柱。纯环境、一眼可见，不靠文字/小人偶。火用 campfire（不蔓延）静态安全。
RUIN_ENABLED = True
RUIN_TREE_FIRE = True              # 灵魂树断根燃烧态（焦干+树冠冒火/蓝烟）
RUIN_WALL_BREACHES = 3             # 城墙攻破豁口数
RUIN_WALL_BREACH_LEN = 7           # 每个豁口清出的墙段长度(格)
RUIN_DEBRIS_COUNT = 80             # 城内焦土/余烬/灰烬散布点数
# 末日天气：雷暴+午夜常驻（雷雨夜里火光废墟更震撼；doWeatherCycle/doDaylightCycle
# 置 false 让它不循环回晴/白天）。雷暴天本身持续打雷。
RUIN_STORM = True
# 建筑废墟化：在选中建筑上多点撒真火（可燃屋自燃蔓延烧起来）+ TNT + 屋顶焦黑。
# 火会在城内蔓延——正是"被焚毁的城"。石质/不可燃屋靠 TNT+焦黑表现损毁。
RUIN_BURN_BUILDINGS = True
RUIN_BUILDING_FRAC = 1.0           # 施加损毁的建筑比例（1.0=每栋都炸）
RUIN_BUILDING_FIRES = 8            # 每栋点火数
RUIN_BUILDING_TNT = 1              # 每栋引爆的 primed TNT 数（真爆炸，会炸坑/点火）
RUIN_TREE_TNT = 3                  # 灵魂树上引爆的 TNT 数
RUIN_TNT_FUSE_MIN = 10             # TNT 引信随机范围(tick)，做连环爆
RUIN_TNT_FUSE_MAX = 120
# 落雷：雷暴天随机打雷太稀疏，放避雷针(lightning_rod)在树顶/高处吸引持续落雷
# （玩家可见的常驻雷击点）+ 生成时主动 summon 几道闪电制造焦痕。
RUIN_LIGHTNING_RODS = 8            # 避雷针数量（吸引持续落雷）
RUIN_LIGHTNING_STRIKES = 6         # 生成时主动劈的闪电数
# 大图缩放：上面的数量都按小图(半径≈95)定的，大图城墙长/面积大会显得稀疏。
# 按城墙半径缩放——散布类(焦土/避雷针)随面积放大、豁口随周长加宽；TNT/落雷封顶
# 防大城几百个连环爆卡死。小图 scale=1 保持原样。
RUIN_SCALE_ENABLED = True
RUIN_SCALE_BASE_R = 95             # 基准城墙半径，此半径下用上面的原始数量
RUIN_MAX_TNT = 40                  # primed TNT 引爆总数上限（防连环爆过载）
RUIN_BREACH_LEN_MAX = 18           # 豁口长度上限（大图豁口更宽）
# 入侵者军阵：城墙豁口外 summon illager 定格军队让评审一眼看到"谁攻的城"。
# 用 illager 系（不被阳光烧）+ NoAI+PersistenceRequired（定格站桩、朝城、不乱跑不消失）。
RUIN_INVADERS_ENABLED = True
RUIN_INVADERS_PER_BREACH = 9       # 每豁口外掠夺者(pillager)数（3排纵深梯队）
RUIN_INVADER_RAVAGER = True        # 豁口正中放劫掠兽(ravager)当攻城兽
RUIN_SIEGE_CAMPS = 8               # 城墙外围围城营地数（绕城一圈，整城被围）
RUIN_SIEGE_CAMP_SIZE = 4           # 每个围城营地掠夺者数
RUIN_LOOT_FRAC = 0.3               # 房子附近散布零星劫掠者(vindicator)的建筑比例
# 守军反击：铁傀儡(iron_golem)当灵魂树城邦守卫，与入侵者定格对峙——不是单方屠杀。
RUIN_DEFENDERS_ENABLED = True
RUIN_DEFENDERS_PER_GATE = 3        # 每豁口内侧守军数（列阵挡住入口）
RUIN_DEFENDERS_CORE = 6            # 城心最后防线守军数（围灵魂树朝外）
RUIN_INVADER_GLOW = True           # 入侵者常驻发光（穿墙可见，评审一眼定位军阵）
# 天降火球：城市上空悬浮的燃烧火球（netherrack 核=火不灭 + fire 壳），像投石/陨石轰炸。
RUIN_FIREBALLS = 6                 # 火球数量
RUIN_FIREBALL_RADIUS = 2           # 火球半径（2=5×5×5）

# ── 地形分析（Priority 0 卡 1） ───────────────────────────────────
# scan/terrain_analysis.analyze_terrain 读这些阈值。下游卡 2/3/4/5 复用产出。
FLAT_SLOPE_THRESHOLD = 1.5          # |∇h| < 此值视为平地（每格高度差，单位 block）
ROUGHNESS_WINDOW = 5                # 起伏度计算的方窗边长，必须为奇数
RIDGE_PROMINENCE = 3                # 山脊：比 5x5 邻域中位数高出此值（block）
ELEVATION_ZONE_PERCENTILES = (33, 66)   # 低/中/高分带：低于 p33=low, p33..p66=mid, ≥p66=high
WATER_BLOCK_IDS = {                 # is_water 检测时认作"水"的方块名（含岩浆）
    "minecraft:water",
    "minecraft:flowing_water",
    "minecraft:lava",
    "minecraft:flowing_lava",
}

# ── 中心选址（Priority 0 卡 2） ────────────────────────────────────
# city.center.find_dramatic_center 读这些参数。
CENTER_DRAMA_RADIUS = 16            # 评分时考察周围多少格（drama / prominence 用）
CENTER_SAMPLE_STRIDE = 4            # 候选点采样步长（每 stride 格采一次，减少候选数）
CENTER_MIN_BUILDABLE_RATIO = 0.6    # 候选 8x8 邻域内 is_flat 比例必须 ≥ 此值
# 灵魂树 footprint 尺度的"可平整"硬过滤：候选周围 WINDOW×WINDOW 高差 > MAX_RELIEF
# 的点排除——否则城心落陡坡时核心 terraform too_steep_cut、树半埋、广场大面积跳过。
# 数学：range ≤ MAX_RELIEF ≤ TERRAFORM_MAX_CUT_CORE(50) 保证 cut/fill 都不超 cap。
# 留余量取 40。WINDOW≈plains soul_TREE(100)。MAX_RELIEF<=0 关闭此过滤。
CENTER_CORE_RELIEF_WINDOW = 100
CENTER_MAX_CORE_RELIEF = 40

# ── 区域可建率选址（卡 16.1，治本城稀疏）─────────────────────────────
# 旧评分 drama+prominence=0.55 偏爱崎岖/尖峰，buildable 只看城心 8×8 局部 → 城心
# 局部平但四周全山/水 → 城稀疏（用户实测 21.9% 可建只放 25 栋）。新增"城半径内可
# 建格占比"评分（大窗），并重平衡权重压低 drama/prom。flag=False 回退旧权重。
CENTER_REGIONAL_BUILDABLE_ENABLED = True
CENTER_REGIONAL_BUILDABLE_WINDOW = 160   # 评估可建率的方窗边长(格)，覆盖内/中圈

# ── 单栋 terraforming（Priority 0 卡 5） ───────────────────────────
# city.terraform.terraform_for_building 读这些参数。
TERRAFORM_MAX_CUT = 3               # 单栋最多向下凿 N 格；超过就换位置（外圈默认）
TERRAFORM_MAX_FILL = 3              # 单栋最多向上垫 N 格；超过就换位置（外圈默认）
TERRAFORM_DEFAULT_STRATEGY = "p70"  # 选 base_y 策略：'p70'/'median'/'min'/'max'
                                    # p70 让多数格略垫、少数高点凿掉，最自然

# ── 激进 terraform（用户决策：建筑驱动地形） ─────────────────────
# 灵魂树和内圈建筑用更大的 cut/fill 上限，让"长进山坡"成为常态。
# 代价：HTTP 量+土方量增大；收益：山地不会 0 房子，地标有祭坛感。
# v3（用户决策：要"长进地里"，不要金字塔）：
#   - strategy="median" 让 base_y 取 footprint 中位数：一半格 cut 山头，
#     一半格 fill 洼地。平台高度接近原地表的中等高度，最自然。
#   - max_cut=max_fill=30：对称容差，能处理 60 格内 ±30 起伏。
#   - 历史：v1 (15/15/p70) 在大起伏山地 fail；v2 (60 fill/max) 视觉
#     变成 60 格方土台 + 细高树，比例失调。median + 30/30 折中。
TERRAFORM_MAX_CUT_CORE  = 50
TERRAFORM_MAX_FILL_CORE = 50
TERRAFORM_CORE_STRATEGY = "median"
TERRAFORM_MAX_CUT_INNER  = 10       # 内圈建筑：梯田式坐落
TERRAFORM_MAX_FILL_INNER = 10
# 内圈 suitability 阈值：放宽，让有机 ring_mask 内的所有合理位置都能选
INNER_MIN_SUITABILITY        = 0.1   # 第一轮
INNER_RETRY_MIN_SUITABILITY  = 0.05  # 第一轮没候选时降到这个值

# ── 圈层有机生长（Priority 0 卡 3） ────────────────────────────────
# city.rings.grow_organic_rings 读这些参数。
# 从中心 Dijkstra 扩展，按"地形友好距离"累计面积分三圈。
TARGET_AREAS = {
    # 按 RADIUS_MAP 同心圆环面积反推（π·(r_max² − r_min²)，向下取整）：
    #   inner: π·(80² − 20²)  ≈ 18,850
    #   mid:   π·(180² − 100²) ≈ 70,400
    #   outer: π·(260² − 200²) ≈ 86,700
    # 总 ≈ 175,950 格——BFS 累计扩展到约 r=236，匹配 outer 圈外缘。
    # 早期 700/1800/3300 是小尺度（半径 ~50）调参遗留，与当前 RADIUS_MAP 不匹配。
    "inner":  18850,
    "mid":    70400,
    "outer":  86700,
}
# terrain_cost：基础 1.0 + slope/roughness 惩罚。water 是 inf（永远绕开）。
SLOPE_COST_STEEP = 3.0              # slope > 此值算"陡坡"
SLOPE_COST_GENTLE = 1.5             # slope > 此值算"缓坡"
ROUGHNESS_COST_THRESHOLD = 2.0      # roughness > 此值额外惩罚
TERRAIN_COST_FOR_STEEP = 3.0        # 陡坡每格的惩罚（额外）
TERRAIN_COST_FOR_GENTLE = 1.0       # 缓坡每格的惩罚（额外）
TERRAIN_COST_FOR_ROUGH = 1.0        # 起伏每格的惩罚（额外）
# 全局开关：False 时 main.py 跳过有机圈层，退回旧的同心圆 fallback。
ORGANIC_RINGS_ENABLED = True

# ── 主干道沿地形（Priority 0 卡 4） ────────────────────────────────
# roads.system.select_backbone_endpoints + plan_main_road_path 读这些参数。
NUM_BACKBONE_ENDPOINTS = 4          # 主干道数量（4-6 较合理；过多会让画面碎）
ROAD_MIN_ENDPOINT_ANGLE_DEG = 60    # 相邻端点的最小角度差，防止两端点靠太近
# 全局开关：False 时回退到等角度直线放射。
ORGANIC_BACKBONE_ENABLED = True

# ── Grid Layout 改造（Priority 2 卡 9.1+） ─────────────────────────
# 顶层开关。卡 9.5 起默认 True：builder.py 走 grid 流程（广场+主道+街区+装饰）。
# 设 False 可一键回退到 Priority 0/1 的散点+扇区旧流程（baseline 兜底）。
GRID_LAYOUT_ENABLED = True

# 中心广场（卡 9.1）：灵魂树 footprint 外缘的"环"（不进树 footprint，避免覆写树）。
# mask = (|dx|≤r) & (|dz|≤r) & (|dx|+|dz| ≤ r·OCTAGON_MANHATTAN_FACTOR)
#   再减去树 footprint 矩形（inner_half_x/z）= 环形。
# 系数：1.0 → 菱形；~1.3 → 经典八角（但环窄时对角会被 footprint 吃掉只剩 4 凸台）；
#   ≥2.0 → 正方形（无切角，环连续，四角也有广场）。当前用方框 2.0。
# 外接圆半径按实际树 footprint 推：r = max(half_x, half_z) + PLAZA_PADDING。
# plains soul_TREE 100×100 → r=55；desert/water GOLDEN_TREE 165×151 → r=87。
PLAZA_PADDING = 5                                 # 树外缘呼吸格数（环宽 ≈ 此值）
PLAZA_RADIUS = 55                                 # fallback：取不到树 footprint 时用
PLAZA_MATERIAL = "minecraft:polished_andesite"    # 广场顶层
PLAZA_SUB_MATERIAL = "minecraft:cobblestone"      # 广场之下的柱基
OCTAGON_MANHATTAN_FACTOR = 2.0                    # 方框（连续环）；<2 会切角
# 中央广场是纪念碑平台，应与树连成连续石台：凿深≤此值的列铲平到 base_y，
# 个别凿深>此值的高点才保留原地形（避免极端断坎）。中心选址有 relief 过滤
# (CENTER_MAX_CORE_RELIEF) 保证够平，所以放宽到 20 不会凿出沟、广场也不残缺。
PLAZA_MAX_CUT = 20

# 广场裙边台阶（收边）：广场落在高石峰上时，外缘对下坡地形是一堵笔直挡土墙。
# 曾试图用同心台阶放坡收边，但在陡山上反而摊成一大片灰色阶梯金字塔、比直墙更丑
# （2026-07-10 Caldera 实测）。默认关闭；根治改走「陡坡核心跳过广场」PLAZA_SKIP_ON_STEEP_CORE。
PLAZA_SKIRT_ENABLED = False
PLAZA_SKIRT_RINGS = 4            # 台阶级数（外扩这么多环）
PLAZA_SKIRT_STEP = 2            # 每级相对上一级下降的高度（格）
PLAZA_SKIRT_RING_WIDTH = 3      # 每级台面的宽度（格）
PLAZA_SKIRT_MAX_FILL = 24      # 单列裙边填土上限；超过（深谷）跳过，不起高柱

# 陡坡核心（terraform 撞 sentinel/太陡而回退兜底）时，跳过中心广场铺块——避免在
# 悬崖山顶硬铺一大片灰石平台/悬空墙。仍算 plaza_r 几何供主道/商业街用；只是不铺广场环。
# 平坦区（核心 terraform 正常成功）照常有广场。见 builder [4.5]。
PLAZA_SKIP_ON_STEEP_CORE = True

# 核心（灵魂树/许愿树）terraform 失败时的兜底：True=改用 terraform_force_platform
# 强制平整出地基（低列填、高列/sentinel 削，地形材质），保证树底不悬空；
# False=旧行为（回退单点 base_y、不填地基 → 陡坡上树会飘）。见 _place_core。
CORE_FORCE_PLATFORM_ON_FAIL = True

# 堆积坡 talus 裙（卡 14.1）：陡坡核心走 force_platform 后，地基边缘仍是一堵墙。
# talus 从 footprint 边界 grassfire 向外生长：每格降 1/RUN_PER_DROP，碰真实地面即收，
# 悬崖（填土>MAX_FILL）止步。只填不凿、地形材质 → 平台边与地形自然融合，不再悬空墙。
# 见 terraform.terraform_talus_apron，_place_core 在 force_platform 兜底后调。
CORE_TALUS_APRON_ENABLED = True
TALUS_APRON_RUN_PER_DROP = 1    # 每下降 1 格所跨的水平格数（1≈45°，贴 MC 山地 1:1 坡最好；2≈26°缓）
TALUS_APRON_MAX_REACH = 12      # apron 相对 base_y 最多下降多少格（限外扩范围，免碰建筑环）
TALUS_APRON_MAX_FILL = 10       # 单列填土上限；超过（悬崖）止步不填

# 嵌入山体（卡 14.1 方案 B）：陡坡核心的 force_platform 改取**低分位** base_y
# → 大多数列变「削峰」而非「填高」，平台陷进山里、不再架成高台。分位越低嵌得越深。
# 许愿树等自带大基座的组件在陡峰上专治「一整个平台」。见 _place_core force_platform。
CORE_NESTLE_ON_STEEP = True
CORE_NESTLE_STRATEGY = "p30"    # base_y 取 30 分位（p65=原架高；越低越往下削、嵌得越深）

# 4 条中轴主道（卡 9.1）：从广场外缘到城墙的 cardinal 直道。
# 宽度奇数最自然（中线对称）。地形高差用 RoadRenderer 已有的 stair_step 处理。
CARDINAL_ROAD_WIDTH = 5
CARDINAL_ROAD_MATERIAL = "minecraft:cobblestone"
# True：4 条 cardinal 主道的"铺块"延后到 [9]（建筑之后）渲染，传 blocked_boxes 遇楼
# 断开 → 主道不被 [4.7]地标/[4.8]商业街/[5']网格覆盖（与环城路/网格街道同时序）。
# [4.5] 仍算 plaza_r 几何供商业街/网格用，只是不在那铺块。False = 旧行为（[4.5] 即铺）。
DEFER_CARDINAL_ROADS = True

# 次轴 + 街区切分（卡 9.2）。BLOCK_SIZE = 唐长安坊 30 格见方。
BLOCK_SIZE = 30
NEXT_ROAD_WIDTH = 3
NEXT_ROAD_MATERIAL = "minecraft:cobblestone"
MID_RING_START_R = 90                             # 避开 100×100 灵魂树外缘 +40
OUTER_RING_END_R = 260

# 网格街道渲染（卡 9.6）：补齐 enumerate_blocks 街区之间的"次道"，中式棋盘。
# enumerate_blocks 把城切成周期网格街区但街缝一直没渲染（卡 9.2 注释"留卡 9.3"未做）。
# True 时 builder 沿同一套周期网格渲染横平竖直街道，并关掉放射主道 + 逐栋接入
# （被棋盘街道取代）；中心十字主道 + 环城路保留。False 回退旧放射/接入流程。
GRID_STREETS_ENABLED = True
GRID_STREET_MATERIAL = NEXT_ROAD_MATERIAL

# 卡 9.6 修（§10.f 城墙豁口）：网格街道不穿城墙。True 时街道外缘 clip 到
# WALL_RADIUS 内侧（墙前留 _WALL_MARGIN 空隙），街道根本不碰墙 → 墙体完整、不冲
# 豁口。outer(200~260) 郊区那圈改由环城路 + 4 cardinal 主道经既有门洞接入。
# False 回退旧行为（街道铺到 OUTER_RING_END_R=260，会从内向外横穿城墙）。
GRID_STREET_CLIP_AT_WALL = True
GRID_STREET_WALL_MARGIN = 5                        # 街道外缘与城墙之间留的空隙(格)

# ── 城外郊区（Priority 5 卡 11.1）─────────────────────────────────
# True 时方城也枚举城墙外 outer 圈街区（200~OUTER_RING_END_R），墙外建郊区/村落，
# 用上闲置的 outer 池建筑、填满城外空带。横跨墙体的街区仍丢弃（不盖墙，§10.f）。
# False = 旧行为（方城街区只到墙内 WALL_RADIUS-NEXT_ROAD_WIDTH，城外全空）。
GRID_SUBURB_ENABLED = True   # 卡 11.1 验证临时开；review 后定默认（card 默认 False）
SUBURB_WALL_GAP = 8          # 墙外第一排街区与城墙之间留的空隙(格)，防贴墙

# ── 商业街（Priority 6 卡 12.1）──────────────────────────────────
# True 时沿 4 条 cardinal 主道两侧密铺 05_商业街_* 小店（店面朝街），
# builder 新步 [4.8] 先占 footprint，[5'] 网格/[8] 城墙/[9] 街道据 placed_boxes 避让。
# False = 旧行为（路侧只有网格碰巧落的店）。
COMMERCIAL_STREET_ENABLED = True   # 卡 12.1 验证临时开；review 后定默认（card 默认 False）
COMMERCIAL_STREET_PREFIX = "05_商业街_"   # 店池筛选前缀（components/* 下递归找）
COMMERCIAL_STREET_GAP = 1          # 店与路缘之间留的空隙(格)，0=紧贴路
COMMERCIAL_STREET_SPACING = 0      # 相邻店之间沿街方向的间隔(格)，0=紧贴排（卡12.1 B 提产出）
# 卡 12.1 (B)：起点从 plaza 外缘再外推此值，跳过近 plaza 的地标带（四不合院/戏台/许愿树
# 锚在小半径），减少内段 box_intersect 碰撞、把店铺让到主道中外段连排。
COMMERCIAL_STREET_START_OFFSET = 16
# 店宽上限(格)：05_商业街_* 实测 footprint 24~55、中位 34.5，≤40 admits 15 个不同店。
COMMERCIAL_STREET_MAX_FOOTPRINT = 40

# ── 活入侵彩蛋（Priority 7 卡 13.1）— 默认关，不进 GDMC 提交 ──────────
# 生成期埋命令方块+计分板机关；解冻后玩家接近城心 N tick → 聊天弹可点菜单
# [开始入侵]/[再等等] → 点击触发突袭（劫掠者破城 + 灵魂树燃烧）。火/凋零会真毁城，
# playtest 专用。机关与召唤全靠 vanilla 命令方块，无新依赖。
INVASION_RAID_ENABLED   = False  # 默认关；测入侵用重埋脚本临时加，不进整城生成
INVASION_TRIGGER_DELAY  = 200        # 玩家进入城心半径后多少 tick 弹菜单（200=10秒）
INVASION_TRIGGER_RADIUS = 48         # 玩家进入城心此 3D 半径才开始计时
INVASION_VANGUARD       = 40         # 先锋波：就在城心 28 格刷，触发即看见开打
INVASION_PILLAGERS      = 120        # 劫掠者主力（分 4 路纵队，行军破城）
INVASION_VINDICATORS    = 40         # 卫道士（近战斧兵，随纵队冲）
INVASION_EVOKERS        = 6          # 唤魔者（召唤尖牙/恼鬼，视觉法术）
INVASION_RAVAGERS       = 8          # 劫掠兽
INVASION_WITHERS        = 3          # 凋零怪（召唤即蓄力爆炸+拆墙=玩家要的"爆炸"）
INVASION_FOLLOW_RANGE   = 256        # 突袭者仇恨/跟踪范围(格)：远刷也能锁定玩家成队推进
INVASION_GATE_RING      = 72         # 4 路纵队起点半径(格)：72=视野内可见行军，非城墙184看不见
INVASION_LIGHTNING      = 16         # 闪电数（4门刷怪点各1道标记"敌人从这来"+随机）
INVASION_STORM          = True       # 切午夜+雷暴 → 黑夜风雨里火光爆炸更震撼
INVASION_BURN_TREE      = True       # 灵魂树燃烧（树干多高度点火 → 快速整株燃起）
INVASION_TNT            = 60         # 天降 TNT 轰炸数（已点燃，引信错开 → 连环爆炸）
INVASION_TNT_HEIGHT     = 45          # TNT 在地表上方多少格落下
INVASION_TNT_RADIUS     = 55          # TNT 散布半径（城心起）
# 劫掠者 spawn 环半径：必须在玩家(城心)仇恨范围内(~32)才会真攻击，太远只站桩。0=28。
INVASION_SPAWN_RING     = 0

# ── 道路找坡 + 干裂谷架桥（renderer.py） ─────────────────────────
# 旧找坡把路面硬钳在每列地表 ±GRADE_MAX_DEV 内 → 小起伏紧贴、深裂谷被逼着
# 下到谷底再爬上来（用户反馈，图 12/15）。这组参数放宽磨平力度 + 给干裂谷加
# 架桥模式（桥面+栏杆+桥墩，跨过去而不沉底）。
ROAD_SMOOTH_RADIUS = 4              # 中线滑动平均半径（越大越磨平小起伏）
ROAD_GRADE_MAX_DEV = 8             # 路面对地表的最大偏离（削/填纵向上限，适中）
ROAD_BRIDGING_ENABLED = True       # False 回退旧行为（干裂谷实心填/沉底，不架桥）
# 架桥深度阈值：必须 > renderer._FILL_CAP(=10)，否则 ≤10 深的凹本可填土铺实路，
# 却被当"裂谷"架成悬空木板桥 → 滚动丘陵上大面积虚假架桥、路断续无法通行。
# 实测旧值 5 让一条主道 144 列里 56 列架桥；提到 12 后仅真正填不平(>10)的才架桥。
ROAD_BRIDGE_MIN_DEPTH = 12         # 地表比两侧崖肩低≥此值(且填不平)才架桥
ROAD_BRIDGE_MAX_SPAN = 64          # 两崖肩跨度＞此值不架桥（宽对称谷也架，避免下切刷墙）
ROAD_BRIDGE_PILLAR_SPACING = 6     # 每隔几格一组桥墩（从桥面落到谷底）
# 梯化（卡：山地主道阶梯加平台）：把零散单台阶重排成「梯段+平台」节奏，
# 陡坡上的路读成有意的阶梯街而非碎楼梯。偏离用 ROAD_GRADE_MAX_DEV 封顶防土方爆。
ROAD_TERRACE_ENABLED = True
ROAD_TERRACE_FLIGHT = 3            # 一段梯最多连续几级台阶
ROAD_TERRACE_LANDING = 3           # 两段梯之间平台最短几格
# 路基贴地材质：填土(roadbed)用该列实际地表方块而非一律 cobblestone，红恶地→红陶/
# 红砂、草地→草土，削填墙不再一片灰扎眼。路面(road_block)仍保留 cobblestone 当可辨路。
# 需调用方把 scan_volume+codec 传进 RoadRenderer；缺时回退 _FILL_BLOCK。
ROAD_FILL_MATCH_TERRAIN = True
ROAD_BRIDGE_RAIL_BLOCK = "minecraft:cobblestone_wall"
ROAD_BRIDGE_PILLAR_BLOCK = "minecraft:cobblestone"
ROAD_WALKABLE_CORRIDOR_ENABLED = True
ROAD_WALKABLE_CLEARANCE = 4
ROAD_BUILDING_CONNECTORS_ENABLED = True
ROAD_BUILDING_CONNECTOR_MAX_LEN = 24

# ── A* 路网（Priority 9 卡 15.1）替代笔直主道+削填架桥 ─────────────────
# "astar"：[9] 用高度图加权 A* 连建筑门/城门/城心，drape 贴地铺（无桥无栏杆）。
# "legacy"：旧的 cardinal 主道 + 环城路 + 网格街道（回退 A/B）。见 roads/astar_router.py。
ROAD_SYSTEM = "astar"
ROAD_ASTAR_MAX_STEP = 2       # 邻格高差>此值不可通行。1=最严但易孤立(31%连不上)；2=连通率高
ROAD_ASTAR_STEP_PENALTY = 4.0 # 每爬 1 格的额外代价（越大越爱走平路、绕缓坡）
ROAD_ASTAR_WIDTH = 3          # drape 路宽（格）
ROAD_ASTAR_SURFACE = "minecraft:cobblestone"
ROAD_ASTAR_STAIRS = "minecraft:cobblestone_stairs"
ROAD_ASTAR_CLEARANCE = 3      # 路面上方清空格数（留头顶净空）
ROAD_ASTAR_DOOR_OFFSET = 2    # 建筑门锚点：朝城心那条边外推几格（绕到楼外接路）
ROAD_ASTAR_GATES = True       # 把 4 城门（城心±墙半径）也作锚点，路通到墙

# 道路找坡——孤立高柱(天然冰锥/石柱)切过去而不爬坡（卡 55 修，用户图 55）。
# 现象：一根孤立高柱落在中线 → 滑动平均+偏离钳位把路面顶上去、_CUT_CAP 又削不掉
# 锥顶 → 路骑上冰锥。修法：① grade 基线改局部中位数，离群高柱(高出基线≥PROM)不
# 拽高路面；② 路面经过时把高于路面的地形全削掉(不受 _CUT_CAP 限)，从柱中切出路槽。
# False 回退旧行为（mean 基线 + cut 受 _CUT_CAP=10 限）。
ROAD_SPIKE_CUTTHROUGH_ENABLED = True
ROAD_SPIKE_PROMINENCE = 4          # 中线列高出基线中位数≥此值 → 判离群高柱，找坡时压平
# 基线中位数的窗口半径(中线采样点数，非格)。要够宽，孤立冰锥在宽窗里才算离群点
# 被中位数滤掉；太窄(=平滑半径)会被锥+锥肩占满污染。densify 每格约 2 采样点。
ROAD_SPIKE_BASE_RADIUS = 16

# grid 街区建筑用真实 npy（按公会路由）还是占位色块。
# True：place_block_buildings 走 npy 模式，按 components/<ring>_<guild>/ 选楼
#   （guild token 去 soul_ 前缀对上文件夹）；某 ring_guild 池空则该街区留空（不放）。
# False：用占位色块盒子（4 公会上色，俯视看分区）。
GRID_USE_NPY_BUILDINGS = True

# Environment skin packs for grid buildings.
# When enabled, matching terrain uses components/<ring>_<style>/ before guild pools.
ENV_BUILDING_PACKS_ENABLED = True
# True：每个街区按"它自己脚下"的地形解析风格 → 跨地形的城真正混搭建筑
# （badlands 街区西部楼、plains 街区中式楼…）。False = 旧行为：整城统一用城心地形
# 的风格（一座城一种皮）。用户要"不同地形完全不一样的建筑" → 默认 True。
ENV_STYLE_PER_BLOCK = True
STEAMPUNK_PACK_DIR = os.path.join(ROOT_DIR, "蒸汽朋克建筑包")
WESTERN_PACK_DIR = os.path.join(ROOT_DIR, "西部荒野建筑构建")
MEDIEVAL_PACK_DIR = os.path.join(ROOT_DIR, "z中世纪")
# 火山地形（Caldera 等 basalt/blackstone/magma 地表）独立识别开关。True：地表火山
# 方块归为 "volcano" 地形 → 蒸汽朋克建筑 + 黑石 reskin + 焦黑树。False：回退旧行为
# （volcano→mountain，即中式楼）。没有专门火山建筑包，故复用蒸汽朋克（深色工业感）。
VOLCANO_TERRAIN_ENABLED = True
ENV_BUILDING_STYLE_BY_TERRAIN = {
    "plains": "chinese",
    "mountain": "chinese",
    "water": "chinese",
    "jungle": "chinese",
    "snow": "steampunk",
    "badlands": "western",
    "desert": "medieval",
    "volcano": "steampunk",
}
ENV_BUILDING_STYLE_FALLBACKS = {
    "steampunk": ("medieval",),
    "western": ("medieval",),
}

# ── 地形材质重映射（reskin，Priority 8 卡 14.1）─────────────────────
# 贴 npy 时按该街区/树脚地形把"木系/石系"方块整体换成地形主题，让任意风格的房子
# "入乡随俗"（中世纪房落 badlands → 红砂+相思木）。flag 默认开；plains/water 不设
# 主题 = 用素材原生材质。主题名 → 具体替换表在 city/reskin.py 展开（木系自动扩展
# 全套后缀，石系显式列已存在的变体，避免造出 minecraft:stone_wall 这种不存在的块）。
TERRAIN_RESKIN_ENABLED = True
TERRAIN_RESKIN_THEMES = {
    "badlands": {"wood": "acacia", "stone": "red_sandstone"},
    "desert":   {"wood": "birch",  "stone": "sandstone"},
    "snow":     {"wood": "spruce", "stone": "stone_bricks"},
    "jungle":   {"wood": "jungle", "stone": "mossy_cobblestone"},
    "mountain": {"wood": "spruce", "stone": "stone"},
    "water":    {"wood": "dark_oak", "stone": "dark_prismarine"},  # 海上：深橡木+深海晶
    "volcano":  {"wood": "dark_oak", "stone": "blackstone"},        # 火山：焦黑木+黑石
    # plains：不设主题 = 原生材质（中式素材本就为平原设计）
}
# 灵魂树（cherry_wood/cherry_leaves）按地形换基调；缺省/plains = 不换（保留粉樱花招牌）。
# 雪地→云杉冻树、丛林→丛林树、山地→云杉；badlands/desert→枯树（裸干，叶换空气=凋零，
# 也呼应"被入侵后毁灭"叙事）。叶→air 时 make_tree_remap 自动丢方块状态。
TERRAIN_TREE_REMAP = {
    # 雪原：冰封树——树冠用 packed_ice（实心不化），树干云杉。
    "snow":     {"minecraft:cherry_wood": "minecraft:spruce_wood",
                 "minecraft:cherry_leaves": "minecraft:packed_ice",
                 "minecraft:cherry_fence": "minecraft:spruce_fence"},
    "mountain": {"minecraft:cherry_wood": "minecraft:spruce_wood",
                 "minecraft:cherry_leaves": "minecraft:spruce_leaves",
                 "minecraft:cherry_fence": "minecraft:spruce_fence"},
    "jungle":   {"minecraft:cherry_wood": "minecraft:jungle_wood",
                 "minecraft:cherry_leaves": "minecraft:jungle_leaves",
                 "minecraft:cherry_fence": "minecraft:jungle_fence"},
    # badlands/陶土地 → 枯树（叶换空气=裸干凋零，呼应"被毁灭"叙事）。
    "badlands": {"minecraft:cherry_wood": "minecraft:stripped_acacia_wood",
                 "minecraft:cherry_leaves": "minecraft:air",
                 "minecraft:cherry_fence": "minecraft:acacia_fence"},
    # 水域：覆盖两种树。
    #  - 黄金树（core_water/GOLDEN_TREE）：金/黄块→海晶发光冠+海晶，灰/青结构本就海色保留。
    #  - 樱花灵魂树（core_plains/soul_TREE，主导地形=water 时复用）：粉樱→深橡木干+
    #    warped_wart_block 青冠，与海上建筑(dark_oak+dark_prismarine)统一。
    "water":    {"minecraft:gold_block": "minecraft:sea_lantern",
                 "minecraft:yellow_concrete": "minecraft:prismarine",
                 "minecraft:hay_block": "minecraft:dark_prismarine",
                 "minecraft:yellow_wool": "minecraft:cyan_wool",
                 "minecraft:cherry_wood": "minecraft:dark_oak_wood",
                 "minecraft:cherry_log": "minecraft:dark_oak_log",
                 "minecraft:cherry_planks": "minecraft:dark_oak_planks",
                 "minecraft:cherry_leaves": "minecraft:warped_wart_block",
                 "minecraft:cherry_fence": "minecraft:dark_oak_fence"},
    # desert：不动（用户要求），灵魂树在沙漠保持原樱花。
    # 火山：焦黑枯树——干换焦黑（stripped_dark_oak），叶→air 裸干，呼应火山+被毁灭。
    "volcano":  {"minecraft:cherry_wood": "minecraft:stripped_dark_oak_wood",
                 "minecraft:cherry_leaves": "minecraft:air",
                 "minecraft:cherry_fence": "minecraft:dark_oak_fence"},
}

# 灵魂树材质按「全图主导地形」而非中心单列地形选（水多地少图上中心常落在平原小岛，
# 否则灵魂树永远不变材质）。True：统计 terrain_map 出现最多的非平原地形当树主题。
# npy 模型仍按中心地形选（树物理坐落在中心），只有 reskin 跟主导地形走。
SOUL_TREE_DOMINANT_TERRAIN = True

# ── 小图核心树：许愿树（用户决策 2026-07-01）─────────────────────────
# 小图（R=min(build_w,build_h)//2 ≤ 阈值）用许愿树当核心，替代 soul_TREE：后者
# 100×100 高166，在 256 小城里挤掉地标/叙事（实测地标全跳过）；许愿树 74×89 高39，
# 小城更协调。大图仍用 soul_TREE。False = 一律 soul_TREE（旧行为）。
SMALL_CITY_CORE_WISH_TREE = True
SMALL_CITY_R_THRESHOLD = 160          # R ≤ 此值算小图（256 图 R=128 用；500 图 R=250 不用）
WISH_TREE_PATH = os.path.join(COMPONENT_ROOT, "landmarks", "许愿树.npy")
# 小图城墙：窄且矮（覆盖 WALL_WIDTH / wall_height / WALL_FLAT_TOP）。大图不受影响。
SMALL_CITY_WALL_WIDTH = 1             # 小图城墙宽度（格）
SMALL_CITY_WALL_HEIGHT = 2            # 小图城墙高度（矮墙）
SMALL_CITY_WALL_FLAT_TOP = False      # 小图矮墙随地形（不拉平顶）
# 小图专用中式地标（从建筑合集转的小建筑）。小城可建带 plaza~wall 很窄，只放小件；
# radius 落在 keepout(避广场) 与墙半径之间。四水归堂偏大，放不下会自动跳过。
SMALL_CITY_LANDMARK_KEEPOUT = 50      # 小图地标避让城心距（略大于小图 plaza 半径）
SMALL_CITY_LANDMARK_SPECS = [
    {"file": "牌坊.npy", "angle": 0,   "radius": 62},
    {"file": "亭子.npy", "angle": 180, "radius": 64},
    # Horror-fantasy ward: Eye King (63x61, height 76).
    {"file": "eye_king.npy", "angle": 90, "radius": 72,
     "monster_statue": True, "floating": True},
]

# Extra Eye Kings for GDMC's largest maps. The official evaluation page says
# maps can be around 1000x1000; the fixed LANDMARK_SPECS cluster too close to
# the core there, so on very large city dims we add a second outer ward ring.
SUPER_LARGE_EYE_KINGS_ENABLED = True
SUPER_LARGE_EYE_KINGS_MIN_WALL_R = 300
SUPER_LARGE_EYE_KING_RADIUS_FRAC = 0.84
SUPER_LARGE_EYE_KING_ANGLES = (15, 75, 165, 255, 330)

# Eye King horror-fantasy statue skin. The source model is bright white/red;
# for the invaded ruined city it should read as a black warding idol.
EYE_KING_DARK_RESKIN_ENABLED = True
EYE_KING_FLOATING_ENABLED = True
EYE_KING_FLOAT_HEIGHT = 175
WATER_MONSTER_SINK_DEPTH = 34
EYE_KING_DARK_RESKIN = {
    "minecraft:white_concrete": "minecraft:black_concrete",
    "minecraft:white_terracotta": "minecraft:black_terracotta",
    "minecraft:light_gray_concrete": "minecraft:gray_concrete",
    "minecraft:light_gray_terracotta": "minecraft:gray_terracotta",
    "minecraft:gray_concrete": "minecraft:black_concrete",
    "minecraft:gray_terracotta": "minecraft:black_terracotta",
    "minecraft:red_concrete": "minecraft:red_nether_bricks",
    "minecraft:red_terracotta": "minecraft:nether_wart_block",
    "minecraft:pink_concrete": "minecraft:crimson_hyphae",
    "minecraft:pink_terracotta": "minecraft:crimson_nylium",
    "minecraft:magenta_terracotta": "minecraft:polished_blackstone",
    "minecraft:cyan_concrete": "minecraft:polished_blackstone_bricks",
    "minecraft:cyan_terracotta": "minecraft:polished_blackstone",
    "minecraft:light_blue_terracotta": "minecraft:cracked_polished_blackstone_bricks",
    "minecraft:yellow_terracotta": "minecraft:gilded_blackstone",
    "minecraft:orange_terracotta": "minecraft:magma_block",
    "minecraft:brown_concrete": "minecraft:basalt",
    "minecraft:brown_terracotta": "minecraft:polished_basalt",
    "minecraft:mushroom_stem": "minecraft:bone_block",
    "minecraft:brown_mushroom_block": "minecraft:blackstone",
}

# Super-large map colossus. It is intentionally limited to GDMC's largest
# maps (around 1000x1000) because the model is 117x76 and 244 blocks tall.
# The placement pass keeps it near the far inner wall, facing the soul tree,
# rejects ridge/high-mountain sites, and adds rubble around the legs.
DARK_COLOSSUS_ENABLED = True
DARK_COLOSSUS_MIN_WALL_R = 300
DARK_COLOSSUS_ANGLE = 90
DARK_COLOSSUS_ANGLES = (70, 170, 285)
DARK_COLOSSUS_MAX_COUNT = 3
DARK_COLOSSUS_RADIUS_FRAC = 0.78
DARK_COLOSSUS_WALL_CLEARANCE = 95
DARK_COLOSSUS_OUTER_CLEARANCE = 120
DARK_COLOSSUS_MAX_RELIEF = 22
DARK_COLOSSUS_MAX_MEDIAN_SLOPE = 2.8
DARK_COLOSSUS_DEBRIS_COUNT = 90
DARK_COLOSSUS_CARVE_ENABLED = True
DARK_COLOSSUS_CARVE_MARGIN = 18
DARK_COLOSSUS_CARVE_FRONT = 44
DARK_COLOSSUS_CARVE_HEIGHT = 115

# 许愿树按地形换材质（全换风格，含粉花）。键=许愿树原方块，值=地形主题方块。
# 许愿树主材：spruce_wood(干)/jungle_wood(次干)/moss_block(绿冠)/moss_carpet(地苔)/
# cherry_leaves(叶)/pink_wool·pink_petals·pink_glazed_terracotta(粉花)/azalea。
# plains 不设主题 = 原生粉花许愿树（招牌）。叶/花→air 时 _make_state_preserving 丢状态。
TERRAIN_WISH_TREE_REMAP = {
    "snow": {   # 冰封：云杉干 + 冰冠，粉花转冰蓝
        "minecraft:jungle_wood": "minecraft:spruce_wood",
        "minecraft:moss_block": "minecraft:packed_ice",
        "minecraft:moss_carpet": "minecraft:snow",
        "minecraft:cherry_leaves": "minecraft:packed_ice",
        "minecraft:pink_wool": "minecraft:light_blue_wool",
        "minecraft:pink_petals": "minecraft:air",
        "minecraft:pink_glazed_terracotta": "minecraft:light_blue_glazed_terracotta",
        "minecraft:azalea": "minecraft:air",
    },
    "mountain": {   # 云杉山：针叶冠
        "minecraft:jungle_wood": "minecraft:spruce_wood",
        "minecraft:moss_block": "minecraft:spruce_leaves",
        "minecraft:cherry_leaves": "minecraft:spruce_leaves",
        "minecraft:pink_wool": "minecraft:white_wool",
        "minecraft:pink_petals": "minecraft:air",
        "minecraft:pink_glazed_terracotta": "minecraft:stone",
        "minecraft:azalea": "minecraft:air",
    },
    "jungle": {   # 丛林：繁茂绿，换丛林木+叶，保留粉花
        "minecraft:spruce_wood": "minecraft:jungle_wood",
        "minecraft:cherry_leaves": "minecraft:jungle_leaves",
    },
    "badlands": {   # 枯树：相思裸干，冠/叶/花全枯（呼应"被毁灭"叙事）
        "minecraft:spruce_wood": "minecraft:stripped_acacia_wood",
        "minecraft:jungle_wood": "minecraft:stripped_acacia_wood",
        "minecraft:moss_block": "minecraft:air",
        "minecraft:moss_carpet": "minecraft:air",
        "minecraft:cherry_leaves": "minecraft:air",
        "minecraft:pink_wool": "minecraft:air",
        "minecraft:pink_petals": "minecraft:air",
        "minecraft:pink_glazed_terracotta": "minecraft:red_terracotta",
        "minecraft:azalea": "minecraft:dead_bush",
    },
    "water": {   # 海上：深橡木干 + 青海晶冠
        "minecraft:spruce_wood": "minecraft:dark_oak_wood",
        "minecraft:jungle_wood": "minecraft:dark_oak_wood",
        "minecraft:moss_block": "minecraft:warped_wart_block",
        "minecraft:moss_carpet": "minecraft:warped_wart_block",
        "minecraft:cherry_leaves": "minecraft:warped_wart_block",
        "minecraft:pink_wool": "minecraft:cyan_wool",
        "minecraft:pink_petals": "minecraft:air",
        "minecraft:pink_glazed_terracotta": "minecraft:cyan_glazed_terracotta",
        "minecraft:azalea": "minecraft:air",
    },
    "desert": {   # 沙漠绿洲：白桦干，保留绿冠+粉花（绿洲意象）
        "minecraft:spruce_wood": "minecraft:birch_wood",
        "minecraft:jungle_wood": "minecraft:birch_wood",
        "minecraft:pink_glazed_terracotta": "minecraft:sandstone",
    },
    "volcano": {   # 焦黑枯树：焦干，冠/叶/花全枯
        "minecraft:spruce_wood": "minecraft:stripped_dark_oak_wood",
        "minecraft:jungle_wood": "minecraft:stripped_dark_oak_wood",
        "minecraft:moss_block": "minecraft:air",
        "minecraft:moss_carpet": "minecraft:air",
        "minecraft:cherry_leaves": "minecraft:air",
        "minecraft:pink_wool": "minecraft:air",
        "minecraft:pink_petals": "minecraft:air",
        "minecraft:pink_glazed_terracotta": "minecraft:blackstone",
        "minecraft:azalea": "minecraft:dead_bush",
    },
    # plains：不设主题 = 原生粉花许愿树
}

# 城市绿化（卡 16.1）：建筑/道路放完后追加一步，散种标准树木模型。空地填空 + 路边，
# 按地形 reskin（云杉→jungle 丛林 / badlands 相思 / snow 云杉…），plains 保留云杉。
# GREENERY_TREE_GLOB 匹配 components/** 下的树木 npy（20 棵中世纪奇幻树系列）。
GREENERY_ENABLED = True
GREENERY_TREE_GLOB = "*树木*.npy"
GREENERY_SPACING = 13            # 候选网格步长（越小越密）；中密度≈13
GREENERY_JITTER = 4              # 候选点随机抖动 ±格，避免成行死板
GREENERY_MAX = 400              # 全城绿化树上限（防爆块/超时）
GREENERY_MAX_SLOPE = 4          # footprint 高度跨度超此值视作陡坡 → 不种

# 街区驱动 placement（卡 9.3）。素材都是大楼 → 1 街区 = 1 栋建筑（非多栋小楼）。
BLOCK_TERRAFORM_MAX_CUT = 30                      # 单街区 terraform 上限，超则跳过整块
BLOCK_TERRAFORM_MAX_FILL = 30

# 地基兜底（卡：地形修补）。陡街区 terraform 超限时不再跳过，换"高台策略"重算
# 一次：用高分位 base_y + 放宽 fill 上限 → 填土垫高台（fill-biased pedestal），
# 让楼照样能建（崎岖地也能铺满，不再整扇区空）。仍跳水/sentinel footprint。
FOUNDATION_FALLBACK_ENABLED = True
FOUNDATION_STRATEGY = "p65"                        # base_y 取 footprint 高度 65 分位
FOUNDATION_MAX_CUT = 25                            # 高台仍削掉最高的少数列（封顶）
FOUNDATION_MAX_FILL = 60                           # 放宽填土上限，容忍高台地基

# 吊脚楼地基（中式）：把高台兜底的实心填土改成"实心顶甲板 + 栅栏腿 + 下镂空"。
# 只作用于地基兜底结果，不碰普通建筑 terraform。栅栏腿放在 footprint 周边 + 内部网格。
FOUNDATION_STILT_ENABLED = True
FOUNDATION_STILT_LEG_BLOCK = "minecraft:dark_oak_fence"
FOUNDATION_STILT_LEG_SPACING = 4                   # 内部每隔几格一根栅栏腿

# 强制平台兜底（卡：保证地标/街区一定落地）。terraform + 高台兜底都失败时，无上限
# 造平台：太陡列无限 fill/cut 到 base_y；sentinel 列(地形超扫描天花板、高度未知)从
# base_y+1 削空 PLATFORM_CLEARANCE 格 carve 出建筑空间，顶铺平台材质。地标先正常搜
# 49 候选找平地，全失败才对锚点强平；街区作为第三层兜底直接强平。fill 仍走吊脚楼。
# 代价：极陡/大量 sentinel 的 footprint 土方+HTTP 量增大。False 回退旧行为(失败跳过)。
FORCE_PLATFORM_ENABLED = True
PLATFORM_CLEARANCE = 45        # sentinel/削空列向上清出的高度(格)，容纳建筑本体
BLOCK_BUILDING_PADDING = 1                        # 碰撞框 padding：小=grid 密集；大 mid 楼溢出会稀疏化邻块
GUILD_MAIN_HALL_BLOCK = "nearest_to_plaza"       # 4 主殿放各公会最靠 plaza 的 mid 街区

# 卡 11.2：inner_<guild> 池空时，公会主殿回退到 mid_<guild> 池挑最大一栋当主殿，
# 而不是 no_pool 跳过。False = 旧行为（inner 空 → 该公会主殿不放）。
MAIN_HALL_FALLBACK_TO_MID = True

# 大楼合并地块：footprint 任一边 > 此阈值的"超标楼"占 2×2 相邻同公会街区簇
# （约 60×60），不再溢出刷空邻格。凑不齐 2×2 退回单格溢出。
GRID_MERGE_LARGE_BLOCKS = True
GRID_LARGE_THRESHOLD = BLOCK_SIZE - 2             # >28 判为大楼

# 网格建筑去重：一种 npy（按文件名）整城最多放一栋，优先没用过的，用完才允许重复。
# False = 旧行为（每街区独立 rng.choice，会撞脸）。
GRID_UNIQUE_BUILDINGS = True

# 削减大型建筑：优先挑小楼（按 footprint 升序的较小一半里随机），
# 且全城占 2×2 的大楼（footprint>GRID_LARGE_THRESHOLD）最多 GRID_MAX_LARGE 栋，
# 超额后该街区只从小楼里选。城更密、以小店为主、大楼当点缀。
GRID_PREFER_SMALL = True
GRID_MAX_LARGE = 6

CITY_PLANNING_PROFILES_ENABLED = True
CITY_PLANNING_PROFILES = {
    # 中式城：大街区、棋盘格，像唐长安坊。
    "chinese": {
        "block_size": BLOCK_SIZE,
        "next_road_width": NEXT_ROAD_WIDTH,
        "building_padding": BLOCK_BUILDING_PADDING,
        "large_threshold": GRID_LARGE_THRESHOLD,
        "max_large": GRID_MAX_LARGE,
    },
    # 雪地/蒸汽朋克：房子偏小，现实上也会更紧凑以避风保温。
    "steampunk": {
        "block_size": 22,
        "next_road_width": 2,
        "building_padding": 0,
        "large_threshold": 24,
        "max_large": 3,
    },
    # 中世纪：小巷更窄、地块更碎，保留少量大建筑当视觉锚点。
    "medieval": {
        "block_size": 26,
        "next_road_width": 2,
        "building_padding": 0,
        "large_threshold": 28,
        "max_large": 5,
    },
    # 西部：主街感更强，道路略宽，街区比中式小。
    "western": {
        "block_size": 24,
        "next_road_width": 3,
        "building_padding": 0,
        "large_threshold": 26,
        "max_large": 4,
    },
}

# 街区装饰（卡 9.4）。1街区1栋后没中庭 → 装饰放主殿前广场（开阔内圈）。
BLOCK_DECOR_ENABLED = True
FORECOURT_RADIUS = 72                             # 装饰锚点半径（plaza_outer 55 ~ mid_start 90 中间）

# ── 大地标摆放（Priority 4：超 63 格放不进网格的标志性建筑） ──────────
# city.landmarks 在 [4.5]广场 之后、[5']网格 之前摆放（builder 新步 [4.7]），先占
# footprint（写 placed_boxes/locked_rects）→ 网格/城墙/道路自动避让。每个 spec 锚定
# 角度+半径，附近搜可建点；terraform 失败走高台地基兜底，再失败跳过（不硬塞）。
# 扇区中心：scholars45 / engineers135 / merchants225 / adventurers315（见 _sector_guild）。
LANDMARK_ENABLED = True
LANDMARK_ROOT = os.path.join(COMPONENT_ROOT, "landmarks")
LANDMARK_MAX_CUT = 20                              # 地标 terraform 削方上限（大 footprint 别强凿）
LANDMARK_MAX_FILL = 20
LANDMARK_CORE_KEEPOUT = 58                         # footprint 任一角到城心的最小距离（避开广场/灵魂树）
# 中式地标优先放轮廓差异大的物件：塔、船、树，再保留一个正式大院。
LANDMARK_SPECS = [
    {"file": "闲隅.npy",       "angle": 45,  "radius": 120},
    # 破船：requires_water → footprint 必须有水才放（无水整体跳过），坐 SEA_LEVEL 不 terraform、
    # 不建平台、不强制平台兜底（船就该泡水里）。
    {"file": "破船.npy",       "angle": 225, "radius": 120, "requires_water": True},
    # 许愿树已改作城市核心（小图），不再当地标。
    {"file": "四不合院.npy",   "angle": 315, "radius": 120},
    # Horror-fantasy ward totems: Eye King (63x61, height 76).
    {"file": "eye_king.npy",   "angle": 90,  "radius": 130,
     "monster_statue": True, "floating": True},
    {"file": "eye_king.npy",   "angle": 135, "radius": 150,
     "monster_statue": True, "floating": True},
    {"file": "eye_king.npy",   "angle": 180, "radius": 115,
     "monster_statue": True, "floating": True},
    {"file": "eye_king.npy",   "angle": 270, "radius": 140,
     "monster_statue": True, "floating": True},
    # Bloop ocean monster statue (103x414, height 227): huge, water-only.
    # 找不到大水域自动跳过。radius 大让它落城外靠海一侧。
    {"file": "bloop_ocean_monster_statue.npy", "angle": 0, "radius": 180,
     "requires_water": True, "monster_statue": True, "water_submerge": True},
]
LANDMARK_STYLE_OVERRIDES_ENABLED = True
# 水域标志物（破船 / bloop 海怪，requires_water）独立于建筑风格。中心地形非中式时，
# 风格化地标（steampunk/western）本会整体替换 LANDMARK_SPECS，把水地标一起丢掉
# （bug：沼泽/海上城中心落雪地→steampunk→无破船）。=True 时把 LANDMARK_SPECS 里
# requires_water 的项前置补回，保证水城无论建筑风格都尝试放船/海怪（无水自动跳过）。
LANDMARK_WATER_ALWAYS_ENABLED = True
# requires_water 地标（破船/海怪）的 footprint 水占比下限。原判定 = is_water.any()
# （只要 1 格水就放）→ 大 footprint 蹭到边角 1 格水就坐 SEA_LEVEL，船身主体压陆地
# 搁浅。改为「≥该比例才算水域」，逼地标去真正连续水面放，否则跳过（不搁浅）。
LANDMARK_WATER_MIN_FRAC = 0.5
LANDMARK_STYLE_SPECS = {
    "steampunk": [
        {
            "path": os.path.join(COMPONENT_ROOT, "mid_steampunk",
                                 "am13_蒸汽朋克风格房屋_6842aace.npy"),
            "angle": 45, "radius": 130,
        },
        {
            "path": os.path.join(COMPONENT_ROOT, "mid_steampunk",
                                 "an14_蒸汽朋克风格房屋_482575f2.npy"),
            "angle": 225, "radius": 130,
        },
        {
            "path": os.path.join(COMPONENT_ROOT, "inner_steampunk",
                                 "as19_蒸汽朋克风格房屋_bfe79b34.npy"),
            "angle": 0, "radius": 115,
        },
        {
            "path": os.path.join(COMPONENT_ROOT, "inner_steampunk",
                                 "aq17_蒸汽朋克风格房屋_5a0e696c.npy"),
            "angle": 180, "radius": 123,
        },
        {
            "path": os.path.join(COMPONENT_ROOT, "mid_steampunk",
                                 "ap16_蒸汽朋克风格房屋_347a871c.npy"),
            "angle": 270, "radius": 125,
        },
    ],
    "western": [
        {
            "path": os.path.join(COMPONENT_ROOT, "inner_western",
                                 "aa1_银行_c23c14da.npy"),
            "angle": 45, "radius": 130,
        },
        {
            "path": os.path.join(COMPONENT_ROOT, "inner_western",
                                 "aj10_沙龙酒馆_309b590f.npy"),
            "angle": 225, "radius": 130,
        },
        {
            "path": os.path.join(COMPONENT_ROOT, "inner_western",
                                 "ag7_金矿_a412ef8c.npy"),
            "angle": 0, "radius": 115,
        },
        {
            "path": os.path.join(COMPONENT_ROOT, "inner_western",
                                 "ae5_教堂_1f9afe40.npy"),
            "angle": 180, "radius": 123,
        },
        {
            "path": os.path.join(COMPONENT_ROOT, "inner_western",
                                 "ak11_警长办公室_d1b42461.npy"),
            "angle": 270, "radius": 125,
        },
    ],
    "medieval": [
        {
            "path": os.path.join(COMPONENT_ROOT, "inner_medieval",
                                 "中世纪构建包3_b10公会大厅_be89a463.npy"),
            "angle": 45, "radius": 130,
        },
        {
            "path": os.path.join(COMPONENT_ROOT, "inner_medieval",
                                 "中世纪构建包1_中世纪小屋17_教堂_56d47ad4.npy"),
            "angle": 225, "radius": 130,
        },
        {
            "path": os.path.join(COMPONENT_ROOT, "inner_medieval",
                                 "中世纪构建包3_a2钟楼_ec1ce8b2.npy"),
            "angle": 0, "radius": 115,
        },
        {
            "path": os.path.join(COMPONENT_ROOT, "inner_medieval",
                                 "中世纪构建包2_ao集市大楼_f0459bfe.npy"),
            "angle": 180, "radius": 123,
        },
        {
            "path": os.path.join(COMPONENT_ROOT, "inner_medieval",
                                 "中世纪构建包2_an公馆_4c2bea4c.npy"),
            "angle": 270, "radius": 125,
        },
    ],
}

# ── 自适应地图尺寸（Priority 3 卡 10.1+） ──────────────────────────
# city.dimensions.compute_city_dims 把 build area 尺寸映射到上方所有圈层/墙/广场半径。
# =False：CityDims 逐字段返回上方写死值（等价旧行为，baseline 兜底）。
# =True：R=min(build_w,build_h)//2，半径按 SIZE_RATIO 比例派生（卡 10.5 末尾才默认开）。
ADAPTIVE_SIZE_ENABLED = True
# 各半径相对 R 的比例。校准基准：512 图(R≈256)的当前绝对值（见右侧注释）。
# 注意当前 outer 末=260>256 已越界，比例 0.90 顺手修掉。BLOCK_SIZE 不在此表（不缩）。
SIZE_RATIO = {
    # 校准基准：512 图 R=256，比例 = 原固定值 / 256（outer 末压到 0.90 修越界）。
    # 关键约束：outer 起 > mid 末，否则两圈重叠（旧值 outer起0.62<mid末0.70 会叠）。
    "wall":       0.74,          # WALL_RADIUS      190/256≈0.742
    "outer":      (0.78, 0.90),  # outer 圈 (起,末) 200/256≈0.781；末 0.90 防越界
    "mid":        (0.39, 0.70),  # mid 圈   (起,末) 100/256≈0.391, 180/256≈0.703
    "inner":      (0.08, 0.31),  # inner 圈 (起,末) 20/256≈0.078, 80/256≈0.3125
    "mid_start":  0.35,          # MID_RING_START_R 90/256≈0.352
    "forecourt":  0.28,          # FORECOURT_RADIUS 72/256≈0.281
    "max_extent": 0.90,          # city 最大外延    (= outer r_max)
}
SIZE_EDGE_BUFFER = 8             # 自适应时 edge_margin = max_extent*R + 此值

# ── 组件清单（building_scan / scan_gui 用） ────────────────────
# 4 公会物理结构不动，只换叙事皮（见 worldview 决策）。building_scan --guild
# 的合法值就是这四个。scan_gui 的 Tab2 用 EXPECTED_COMPONENTS 算"已扫/期望"。
GUILD_NAMES = ("scholars", "engineers", "merchants", "adventurers")
RING_NAMES  = ("inner", "mid", "outer")
# 每 ring×guild 期望几栋（docstring：inner×1 主殿、mid/outer 2-4 取中=3）
EXPECTED_COMPONENTS = {
    "inner": dict.fromkeys(GUILD_NAMES, 1),
    "mid":   dict.fromkeys(GUILD_NAMES, 3),
    "outer": dict.fromkeys(GUILD_NAMES, 3),
}
# 朝向枚举：N=-Z, S=+Z, E=+X, W=-X（MC 默认）
FRONT_DIRS = ("N", "S", "E", "W")
