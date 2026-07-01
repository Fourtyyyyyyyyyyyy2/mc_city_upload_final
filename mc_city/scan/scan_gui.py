"""tkinter GUI：building_scan 的可视化壳子。

跑：  python -m mc_city.scan.scan_gui
依赖：纯 tkinter（stdlib），不引入任何新包。

两个 tab：
  扫描 — 6 个 Box 输入框 + ring/guild/name + front/door 元数据 + 4 个按钮 + 日志
  清单 — Treeview 展示 components/ 已扫文件 vs EXPECTED_COMPONENTS 期望数量

扫描是耗时 HTTP 调用，丢后台线程跑，stdout 用 contextlib.redirect_stdout 接到日志区。
"""
from __future__ import annotations

import contextlib
import io
import os
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from ..config import (COMPONENT_ROOT, EXPECTED_COMPONENTS, FRONT_DIRS,
                      GUILD_NAMES, RING_NAMES)
from . import building_scan as bs


class ScanGUI(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("mc_city 建筑扫描器")
        self.geometry("960x680")
        self._busy = False
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=8, pady=8)
        self._build_scan_tab(nb)
        self._build_list_tab(nb)
        self._refresh_list()

    # ───────── Tab 1: 扫描 ─────────
    def _build_scan_tab(self, nb: ttk.Notebook) -> None:
        frame = ttk.Frame(nb)
        nb.add(frame, text="扫描")

        box_frame = ttk.LabelFrame(frame, text="Box (F3 闭区间，两个对角)")
        box_frame.pack(fill="x", padx=4, pady=4)
        self.box_vars: dict[str, tk.StringVar] = {}
        for i, key in enumerate(("X1", "Y1", "Z1", "X2", "Y2", "Z2")):
            ttk.Label(box_frame, text=key).grid(row=0, column=i * 2, padx=2, pady=6)
            v = tk.StringVar(value="0")
            self.box_vars[key] = v
            ttk.Entry(box_frame, textvariable=v, width=8).grid(row=0, column=i * 2 + 1, padx=2)

        target = ttk.LabelFrame(frame, text="目标 (ring × guild → components/<ring>_<guild>/<name>.npy)")
        target.pack(fill="x", padx=4, pady=4)
        ttk.Label(target, text="ring").grid(row=0, column=0, padx=4, pady=6)
        self.ring_var = tk.StringVar(value="mid")
        ttk.Combobox(target, textvariable=self.ring_var, values=RING_NAMES,
                     state="readonly", width=8).grid(row=0, column=1)
        ttk.Label(target, text="guild").grid(row=0, column=2, padx=4)
        self.guild_var = tk.StringVar(value=GUILD_NAMES[0])
        ttk.Combobox(target, textvariable=self.guild_var, values=GUILD_NAMES,
                     state="readonly", width=14).grid(row=0, column=3)
        ttk.Label(target, text="name").grid(row=0, column=4, padx=4)
        self.name_var = tk.StringVar(value="building_01")
        ttk.Entry(target, textvariable=self.name_var, width=20).grid(row=0, column=5)

        meta = ttk.LabelFrame(frame, text="元数据 (写 .json sidecar，paste_volume 当前不读，先存数据)")
        meta.pack(fill="x", padx=4, pady=4)
        ttk.Label(meta, text="front").grid(row=0, column=0, padx=4, pady=6)
        self.front_var = tk.StringVar(value="N")
        ttk.Combobox(meta, textvariable=self.front_var, values=FRONT_DIRS,
                     state="readonly", width=4).grid(row=0, column=1)
        ttk.Label(meta, text="door (dx,dy,dz 体素 local)").grid(row=0, column=2, padx=8)
        self.door_vars: dict[str, tk.StringVar] = {}
        for i, k in enumerate(("dx", "dy", "dz")):
            v = tk.StringVar(value="")
            self.door_vars[k] = v
            ttk.Entry(meta, textvariable=v, width=5).grid(row=0, column=3 + i, padx=2)
        ttk.Label(meta, text="(三格留空=不录入门)").grid(row=0, column=6, padx=8)

        btns = ttk.Frame(frame)
        btns.pack(fill="x", padx=4, pady=4)
        ttk.Button(btns, text="🟦 画玻璃框", command=self._on_mark).pack(side="left", padx=2)
        ttk.Button(btns, text="🗑 清框", command=self._on_unmark).pack(side="left", padx=2)
        ttk.Button(btns, text="💾 扫描存盘", command=self._on_scan).pack(side="left", padx=2)
        ttk.Button(btns, text="📂 打开 components/", command=self._open_folder).pack(side="left", padx=2)

        log_frame = ttk.LabelFrame(frame, text="日志")
        log_frame.pack(fill="both", expand=True, padx=4, pady=4)
        self.log = ScrolledText(log_frame, height=15, wrap="word")
        self.log.pack(fill="both", expand=True)
        self._log("准备就绪。GDMC HTTP 默认连 127.0.0.1:9000，进游戏开 mod 再点扫描。")

    # ───────── Tab 2: 清单 ─────────
    def _build_list_tab(self, nb: ttk.Notebook) -> None:
        frame = ttk.Frame(nb)
        nb.add(frame, text="清单")

        top = ttk.Frame(frame)
        top.pack(fill="x", padx=4, pady=4)
        ttk.Button(top, text="🔄 刷新", command=self._refresh_list).pack(side="left")
        ttk.Label(top, text="✅ 已达期望  🟡 部分完成  ⬜ 未扫  📦 terrain 旧池  "
                            "（双击行 → 把 ring/guild/name 填到扫描 tab）"
                  ).pack(side="left", padx=8)

        cols = ("ring", "guild", "status", "count", "name", "footprint", "height", "front")
        widths = (60, 110, 60, 70, 220, 90, 70, 60)
        self.tree = ttk.Treeview(frame, columns=cols, show="headings")
        for c, w in zip(cols, widths):
            self.tree.heading(c, text=c)
            anchor = "w" if c == "name" else "center"
            self.tree.column(c, width=w, anchor=anchor)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True, padx=(4, 0), pady=4)
        scroll.pack(side="right", fill="y", pady=4, padx=(0, 4))
        self.tree.bind("<Double-1>", self._on_tree_dbl)

    # ───────── 工具 ─────────
    def _log(self, text: str) -> None:
        self.log.insert("end", text + "\n")
        self.log.see("end")

    def _get_box(self) -> tuple | None:
        try:
            return tuple(int(self.box_vars[k].get())
                         for k in ("X1", "Y1", "Z1", "X2", "Y2", "Z2"))
        except ValueError:
            messagebox.showerror("Box 坐标错", "6 个坐标必须是整数")
            return None

    def _get_door(self) -> tuple | None | str:
        """返回 (dx,dy,dz) / None=三格全空 / 'err'=有非空但解析失败"""
        vals = [self.door_vars[k].get().strip() for k in ("dx", "dy", "dz")]
        if not any(vals):
            return None
        if not all(vals):
            messagebox.showerror("Door 坐标错", "dx/dy/dz 要么三格都填，要么三格都空")
            return "err"
        try:
            return tuple(int(v) for v in vals)
        except ValueError:
            messagebox.showerror("Door 坐标错", "dx/dy/dz 必须是整数")
            return "err"

    def _run_async(self, fn, *args, **kwargs) -> None:
        """后台跑 fn，stdout 捕获到日志区。完成后自动刷清单。"""
        if self._busy:
            self._log("⚠️ 上一次还在跑，等完了再点。")
            return
        self._busy = True

        def worker() -> None:
            buf = io.StringIO()
            err: str | None = None
            try:
                with contextlib.redirect_stdout(buf):
                    fn(*args, **kwargs)
            except Exception as e:
                err = f"\n❌ {type(e).__name__}: {e}"
            output = buf.getvalue() + (err or "")
            self.after(0, lambda: self._finish_async(output))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_async(self, output: str) -> None:
        for line in output.splitlines():
            self._log(line)
        self._busy = False
        self._refresh_list()

    # ───────── 按钮回调 ─────────
    def _on_mark(self) -> None:
        box = self._get_box()
        if box is None:
            return
        self._log(f"→ 画框 {box}")
        self._run_async(bs.mark_box, box)

    def _on_unmark(self) -> None:
        box = self._get_box()
        if box is None:
            return
        self._log(f"→ 清框 {box}")
        self._run_async(bs.mark_box, box, block_id="minecraft:air")

    def _on_scan(self) -> None:
        box = self._get_box()
        if box is None:
            return
        door = self._get_door()
        if door == "err":
            return
        name = self.name_var.get().strip()
        if not name:
            messagebox.showerror("name 错", "请填写 name（不要留空）")
            return
        ring = self.ring_var.get()
        guild = self.guild_var.get()
        front = self.front_var.get() or None
        door_t = door if isinstance(door, tuple) else None
        self._log(f"→ 扫描 box={box} → {ring}_{guild}/{name}  front={front}  door={door_t}")

        def do_scan() -> None:
            vol = bs.scan_to_volume(box)
            vol = bs.trim_to_solid(vol)
            bs.save_component(vol, ring, guild, name, front=front, door=door_t)

        self._run_async(do_scan)

    def _open_folder(self) -> None:
        path = COMPONENT_ROOT
        os.makedirs(path, exist_ok=True)
        try:
            if sys.platform == "win32":
                os.startfile(path)
            elif sys.platform == "darwin":
                subprocess.run(["open", path], check=False)
            else:
                subprocess.run(["xdg-open", path], check=False)
        except OSError as e:
            self._log(f"打开目录失败：{e}")

    # ───────── 清单刷新 ─────────
    def _refresh_list(self) -> None:
        for row in self.tree.get_children():
            self.tree.delete(row)
        try:
            scanned = bs.list_components()
        except OSError as e:
            self._log(f"扫 components/ 失败：{e}")
            return

        for ring in RING_NAMES:
            for guild in GUILD_NAMES:
                expected = EXPECTED_COMPONENTS.get(ring, {}).get(guild, 0)
                entries = scanned.get((ring, guild), [])
                count = len(entries)
                status = "✅" if count >= expected else ("🟡" if count > 0 else "⬜")
                count_str = f"{count}/{expected}"
                if not entries:
                    self.tree.insert("", "end", values=(
                        ring, guild, status, count_str, "—", "—", "—", "—"))
                    continue
                for e in entries:
                    shape = e["shape"]
                    fp = f"{shape[2]}×{shape[1]}" if shape else "?"
                    h = str(shape[0]) if shape else "?"
                    front = e["meta"].get("front", "—")
                    self.tree.insert("", "end", values=(
                        ring, guild, status, count_str, e["name"], fp, h, front))

        for (ring, group), entries in scanned.items():
            if group in GUILD_NAMES:
                continue
            for e in entries:
                shape = e["shape"]
                fp = f"{shape[2]}×{shape[1]}" if shape else "?"
                h = str(shape[0]) if shape else "?"
                front = e["meta"].get("front", "—")
                self.tree.insert("", "end", values=(
                    ring, group, "📦", "(terrain)", e["name"], fp, h, front))

    def _on_tree_dbl(self, _event) -> None:
        sel = self.tree.focus()
        if not sel:
            return
        vals = self.tree.item(sel, "values")
        if not vals or vals[4] == "—":
            return
        ring, guild, _s, _c, name = vals[:5]
        if ring in RING_NAMES:
            self.ring_var.set(ring)
        if guild in GUILD_NAMES:
            self.guild_var.set(guild)
        self.name_var.set(name)
        self._log(f"← 已预填 ring={ring} guild={guild} name={name}")


def main() -> None:
    ScanGUI().mainloop()


if __name__ == "__main__":
    main()
