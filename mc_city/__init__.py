"""Minecraft 城市生成系统（重构版）。

模块划分：
    mc/       - Minecraft HTTP 接口（命令、方块编码、放置）
    scan/     - 世界扫描、坐标系、高度图
    city/     - 城市生成（核心入口 build_city）
    roads/    - 道路网络、A*、渲染、碰撞
    modular/  - 模块化建筑（部件 + 装配）
"""