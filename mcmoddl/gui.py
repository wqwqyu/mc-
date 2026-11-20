"""tkinter 图形界面（深色主题，全中文）。

线程模型：所有网络请求在后台线程执行，结果一律通过 self.q 队列回主线程，
由 _pump() 每 80ms 取出处理 —— tkinter 不是线程安全的，绝不能在工作线程里碰控件。
"""
from __future__ import annotations

import os
import queue
import sys
import threading
import traceback
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import __version__, api, downloader, resolver

# ---------------------------------------------------------------- 外观常量
BG = "#1e1f22"
PANEL = "#2b2d30"
CTRL = "#3a3d41"
CTRL_HOVER = "#4b4f55"
FG = "#e6e6e6"
SUB = "#9aa0a6"
ACCENT = "#5865f2"
ACCENT_HOVER = "#6f7bf7"
OK_C = "#4caf50"
BAD_C = "#ef6b6b"

SIDE_LABEL = {"client": "客户端", "server": "服务端"}
INDEX_MAP = {"相关度": "relevance", "下载量": "downloads", "最新发布": "newest", "最近更新": "updated"}
UI_LOADERS = ("fabric", "forge", "neoforge", "quilt")
BUILTIN_VERSIONS = ["1.21.4", "1.21.1", "1.21", "1.20.6", "1.20.4", "1.20.1",
                    "1.19.2", "1.18.2", "1.16.5", "1.12.2"]


def enable_dpi_awareness() -> None:
    """让界面在高分屏上不发虚。"""
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:  # noqa: BLE001
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:  # noqa: BLE001
                pass


def fmt_count(n) -> str:
    try:
        n = int(n or 0)
    except (TypeError, ValueError):
        return "0"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1000:
        return f"{n / 1000:.1f}K"
    return str(n)


def side_text(project: dict) -> str:
    c = (project.get("client_side") or "").lower()
    s = (project.get("server_side") or "").lower()
    if c == "unsupported" and s == "unsupported":
        return "无端"
    if s == "unsupported":
        return "仅客户端"
    if c == "unsupported":
        return "仅服务端"
    return "双端"


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"Minecraft 模组下载器 v{__version__}   ·   数据源 Modrinth")
        self.geometry("1240x870")
        self.minsize(1000, 730)
        self.configure(bg=BG)
        self._center_window(1240, 870)

        self.q: queue.Queue = queue.Queue()
        self.search_hits: list[dict] = []
        self.queue_projects: list[dict] = []   # 用户加入的本体项目
        self.resolution: resolver.Resolution | None = None
        self.downloader: downloader.Downloader | None = None
        self._busy = False
        self._dir_is_auto = True
        self._download_after_resolve = False
        self._progress_total = 0
        self._progress_done = 0

        self.var_ver = tk.StringVar(value="1.21.1")
        self.var_loader = tk.StringVar(value="Fabric")
        self.var_side = tk.StringVar(value="客户端")
        self.var_dir = tk.StringVar()
        self.var_query = tk.StringVar()
        self.var_index = tk.StringVar(value="相关度")
        self.var_conc = tk.IntVar(value=3)
        self.var_chunks = tk.IntVar(value=4)
        self.var_status = tk.StringVar(value="就绪")
        self.var_summary = tk.StringVar(value="队列为空")

        self._init_style()
        self._build_ui()
        self._update_default_dir()
        self.after(80, self._pump)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.log(f"就绪 · 数据源 Modrinth（无需登录）· 目标端决定过滤哪些模组")
        self._load_versions_async()

    # ------------------------------------------------------------ 样式

    def _center_window(self, w: int, h: int) -> None:
        try:
            sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
            x = max(0, (sw - w) // 2)
            y = max(0, (sh - h) // 3)
            self.geometry(f"{w}x{h}+{x}+{y}")
        except tk.TclError:
            pass

    def _init_style(self) -> None:
        self.option_add("*TCombobox*Listbox.background", PANEL)
        self.option_add("*TCombobox*Listbox.foreground", FG)
        self.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.option_add("*TCombobox*Listbox.selectForeground", "white")
        self.option_add("*Font", "{Microsoft YaHei UI} 9")

        st = ttk.Style(self)
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure(".", background=BG, foreground=FG, fieldbackground=PANEL,
                     bordercolor=CTRL, lightcolor=CTRL, darkcolor=CTRL, focuscolor=ACCENT)
        st.configure("TFrame", background=BG)
        st.configure("TLabelframe", background=BG, bordercolor=CTRL, relief="solid")
        st.configure("TLabelframe.Label", background=BG, foreground=ACCENT,
                     font=("Microsoft YaHei UI", 9, "bold"))
        st.configure("TLabel", background=BG, foreground=FG)
        st.configure("Hint.TLabel", background=BG, foreground=SUB)
        st.configure("Ok.TLabel", background=BG, foreground=OK_C)
        st.configure("TButton", background=CTRL, foreground=FG, borderwidth=0,
                     padding=(12, 6), relief="flat")
        st.map("TButton",
               background=[("disabled", PANEL), ("active", CTRL_HOVER)],
               foreground=[("disabled", SUB)])
        st.configure("Accent.TButton", background=ACCENT, foreground="white",
                     borderwidth=0, padding=(14, 7), font=("Microsoft YaHei UI", 9, "bold"))
        st.map("Accent.TButton",
               background=[("disabled", CTRL), ("active", ACCENT_HOVER)],
               foreground=[("disabled", SUB)])
        st.configure("Danger.TButton", background="#5a3a3a", foreground=FG, borderwidth=0,
                     padding=(12, 6))
        st.map("Danger.TButton", background=[("active", "#7a4444")])
        st.configure("TEntry", fieldbackground=PANEL, foreground=FG, insertcolor=FG,
                     bordercolor=CTRL, padding=4)
        st.configure("TCombobox", fieldbackground=PANEL, foreground=FG, background=CTRL,
                     arrowcolor=FG, bordercolor=CTRL, padding=4)
        st.map("TCombobox", fieldbackground=[("readonly", PANEL)],
               foreground=[("readonly", FG)])
        st.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=FG,
                     bordercolor=CTRL, borderwidth=0, rowheight=25)
        st.configure("Treeview.Heading", background=CTRL, foreground=FG, relief="flat",
                     padding=(6, 5), font=("Microsoft YaHei UI", 9, "bold"))
        st.map("Treeview.Heading", background=[("active", CTRL_HOVER)])
        st.map("Treeview", background=[("selected", ACCENT)], foreground=[("selected", "white")])
        st.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor=PANEL,
                     bordercolor=CTRL, lightcolor=ACCENT, darkcolor=ACCENT, thickness=14)
        st.configure("TPanedwindow", background=BG)
        st.configure("Sash", sashthickness=6, background=BG)

    # ------------------------------------------------------------ 布局

    def _build_ui(self) -> None:
        outer = ttk.Frame(self, padding=12)
        outer.pack(fill="both", expand=True)

        # ---- 1. 环境设置
        env = ttk.Labelframe(outer, text=" 1 · 环境设置 ", padding=10)
        env.pack(fill="x")

        r1 = ttk.Frame(env)
        r1.pack(fill="x")
        ttk.Label(r1, text="Minecraft 版本").pack(side="left")
        self.cb_ver = ttk.Combobox(r1, textvariable=self.var_ver, width=11,
                                   values=BUILTIN_VERSIONS)
        self.cb_ver.pack(side="left", padx=(6, 18))
        ttk.Label(r1, text="加载器").pack(side="left")
        ttk.Combobox(r1, textvariable=self.var_loader, width=10, state="readonly",
                     values=[api.LOADER_LABEL[l] for l in UI_LOADERS]).pack(side="left", padx=(6, 18))
        ttk.Label(r1, text="下载目标").pack(side="left")
        self.cb_side = ttk.Combobox(r1, textvariable=self.var_side, width=8, state="readonly",
                                    values=["客户端", "服务端"])
        self.cb_side.pack(side="left", padx=(6, 6))
        self.cb_side.bind("<<ComboboxSelected>>", lambda _e: self._on_side_change())
        ttk.Label(r1, text="客户端 = 你自己玩的整合包     服务端 = 开服用的 mods",
                  style="Hint.TLabel").pack(side="left", padx=10)

        r2 = ttk.Frame(env)
        r2.pack(fill="x", pady=(9, 0))
        ttk.Label(r2, text="模组目录").pack(side="left")
        ttk.Entry(r2, textvariable=self.var_dir).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(r2, text="浏览…", command=self.pick_dir).pack(side="left")
        ttk.Button(r2, text="自动检测", command=self.autodetect_dir).pack(side="left", padx=4)
        ttk.Button(r2, text="打开", command=self.open_dir).pack(side="left")

        r3 = ttk.Frame(env)
        r3.pack(fill="x", pady=(9, 0))
        ttk.Label(r3, text="下载线程").pack(side="left")
        ttk.Spinbox(r3, from_=1, to=8, width=4, textvariable=self.var_conc).pack(side="left", padx=(6, 3))
        ttk.Label(r3, text="个文件并发  ×").pack(side="left")
        ttk.Spinbox(r3, from_=1, to=8, width=4, textvariable=self.var_chunks).pack(side="left", padx=(3, 3))
        ttk.Label(r3, text="线程分块").pack(side="left", padx=(0, 12))
        ttk.Label(r3, text="（4MB 以上的大文件才会分块；总连接数上限 16）",
                  style="Hint.TLabel").pack(side="left")

        # ---- 主体左右分栏
        paned = ttk.Panedwindow(outer, orient="horizontal")
        paned.pack(fill="both", expand=True, pady=(10, 0))

        left = ttk.Labelframe(paned, text=" 2 · 搜索模组 ", padding=8)
        right = ttk.Labelframe(paned, text=" 3 · 下载队列（自动补齐前置） ", padding=8)
        paned.add(left, weight=5)
        paned.add(right, weight=6)

        # 左：搜索
        bar = ttk.Frame(left)
        bar.pack(fill="x")
        ent = ttk.Entry(bar, textvariable=self.var_query)
        ent.pack(side="left", fill="x", expand=True)
        ent.bind("<Return>", lambda _e: self.do_search())
        self.btn_search = ttk.Button(bar, text="搜索", style="Accent.TButton", command=self.do_search)
        self.btn_search.pack(side="left", padx=(6, 0))

        bar2 = ttk.Frame(left)
        bar2.pack(fill="x", pady=(7, 7))
        ttk.Label(bar2, text="排序").pack(side="left")
        ttk.Combobox(bar2, textvariable=self.var_index, width=8, state="readonly",
                     values=list(INDEX_MAP)).pack(side="left", padx=5)
        ttk.Label(bar2, text="中文 / 英文都能搜，如「钠」「机械动力」", style="Hint.TLabel").pack(side="left", padx=6)

        cols = ("title", "zh", "downloads", "side", "desc")
        box1 = ttk.Frame(left)
        box1.pack(fill="both", expand=True)
        box1.rowconfigure(0, weight=1)
        box1.columnconfigure(0, weight=1)
        self.tv_search = ttk.Treeview(box1, columns=cols, show="headings", selectmode="browse")
        for c, t, w, anchor in (("title", "名称", 148, "w"), ("zh", "中文名", 122, "w"),
                                ("downloads", "下载量", 56, "e"), ("side", "适用端", 66, "center"),
                                ("desc", "简介", 150, "w")):
            self.tv_search.heading(c, text=t)
            self.tv_search.column(c, width=w, anchor=anchor, stretch=(c == "desc"))
        self.tv_search.grid(row=0, column=0, sticky="nsew")
        sb1 = ttk.Scrollbar(box1, orient="vertical", command=self.tv_search.yview)
        sb1.grid(row=0, column=1, sticky="ns")
        self.tv_search.configure(yscrollcommand=sb1.set)
        self.tv_search.bind("<Double-1>", lambda _e: self.add_selected())

        lfoot = ttk.Frame(left)
        lfoot.pack(fill="x", pady=(7, 0))
        ttk.Label(lfoot, text="也可以直接输入项目 slug，如 sodium", style="Hint.TLabel").pack(side="left")
        ttk.Button(lfoot, text="加入队列  →", command=self.add_selected).pack(side="right")

        # 右：队列
        qcols = ("title", "zh", "version", "kind", "side", "size", "state")
        box2 = ttk.Frame(right)
        box2.pack(fill="both", expand=True)
        box2.rowconfigure(0, weight=1)
        box2.columnconfigure(0, weight=1)
        self.tv_queue = ttk.Treeview(box2, columns=qcols, show="headings", selectmode="extended")
        for c, t, w, anchor in (("title", "名称", 126, "w"), ("zh", "中文名", 110, "w"),
                                ("version", "版本", 94, "w"), ("kind", "类型", 52, "center"),
                                ("side", "目标端", 60, "center"), ("size", "大小", 56, "e"),
                                ("state", "状态", 56, "center")):
            self.tv_queue.heading(c, text=t)
            self.tv_queue.column(c, width=w, anchor=anchor, stretch=(c in ("title", "title")))
        self.tv_queue.grid(row=0, column=0, sticky="nsew")
        sb2 = ttk.Scrollbar(box2, orient="vertical", command=self.tv_queue.yview)
        sb2.grid(row=0, column=1, sticky="ns")
        self.tv_queue.configure(yscrollcommand=sb2.set)
        self.tv_queue.tag_configure("unavail", foreground=BAD_C)

        qfoot = ttk.Frame(right)
        qfoot.pack(fill="x", pady=(7, 0))
        ttk.Button(qfoot, text="移除选中", command=self.remove_selected).pack(side="left")
        ttk.Button(qfoot, text="清空队列", style="Danger.TButton",
                   command=self.clear_queue).pack(side="left", padx=5)
        ttk.Label(qfoot, textvariable=self.var_summary, style="Hint.TLabel").pack(side="right")

        # ---- 底部操作
        act = ttk.Frame(outer)
        act.pack(fill="x", pady=(10, 0))
        self.btn_resolve = ttk.Button(act, text="① 解析依赖", command=self.do_resolve)
        self.btn_resolve.pack(side="left")
        self.btn_download = ttk.Button(act, text="② 开始下载", style="Accent.TButton",
                                       command=self.do_download)
        self.btn_download.pack(side="left", padx=8)
        self.btn_cancel = ttk.Button(act, text="取消", state="disabled", command=self.do_cancel)
        self.btn_cancel.pack(side="left")
        ttk.Label(act, textvariable=self.var_status, style="Hint.TLabel").pack(side="left", padx=14)

        self.pb = ttk.Progressbar(outer, mode="determinate", maximum=100, value=0)
        self.pb.pack(fill="x", pady=(8, 0))

        # ---- 日志
        logf = ttk.Labelframe(outer, text=" 运行日志 ", padding=6)
        logf.pack(fill="both", expand=False, pady=(10, 0))
        self.txt = tk.Text(logf, height=7, bg="#17181a", fg="#c9d1d9", relief="flat",
                           wrap="word", insertbackground=FG,
                           font=("Consolas", 9), padx=8, pady=6)
        sb3 = ttk.Scrollbar(logf, orient="vertical", command=self.txt.yview)
        self.txt.configure(yscrollcommand=sb3.set, state="disabled")
        self.txt.pack(side="left", fill="both", expand=True)
        sb3.pack(side="right", fill="y")

    # ------------------------------------------------------------ 小工具

    def game_version(self) -> str:
        return self.var_ver.get().strip()

    @staticmethod
    def _int_var(var, default: int, lo: int = 1, hi: int = 8) -> int:
        try:
            return max(lo, min(hi, int(var.get())))
        except (tk.TclError, ValueError):
            return default

    def loader(self) -> str:
        return {v: k for k, v in api.LOADER_LABEL.items()}.get(self.var_loader.get(), "fabric")

    def side(self) -> str:
        return "client" if self.var_side.get() == "客户端" else "server"

    def side_label(self) -> str:
        return SIDE_LABEL[self.side()]

    def log(self, msg: str) -> None:
        """可从任意线程调用。"""
        self.q.put(("log", msg))

    def set_status(self, msg: str) -> None:
        self.q.put(("status", msg))

    def _append_log(self, msg: str) -> None:
        self.txt.configure(state="normal")
        self.txt.insert("end", msg.rstrip() + "\n")
        lines = int(self.txt.index("end-1c").split(".")[0])
        if lines > 2000:
            self.txt.delete("1.0", "300.0")
        self.txt.see("end")
        self.txt.configure(state="disabled")

    def run_bg(self, fn, on_done=None) -> None:
        def worker() -> None:
            try:
                result = fn()
            except Exception as exc:  # noqa: BLE001
                self.q.put(("error", exc))
            else:
                self.q.put(("done", result, on_done))

        threading.Thread(target=worker, daemon=True).start()

    def _pump(self) -> None:
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == "log":
                    self._append_log(msg[1])
                elif kind == "status":
                    self.var_status.set(msg[1])
                elif kind == "progress":
                    self._on_progress(msg[1], msg[2], msg[3])
                elif kind == "item":
                    self._set_row_state(msg[1], msg[2])
                elif kind == "done":
                    _, result, cb = msg
                    if cb:
                        try:
                            cb(result)
                        except Exception as exc:  # noqa: BLE001
                            self._show_error(exc)
                elif kind == "error":
                    self._show_error(msg[1])
        except queue.Empty:
            pass
        self.after(80, self._pump)

    def _show_error(self, exc: BaseException) -> None:
        self._append_log("错误: " + "".join(traceback.format_exception_only(type(exc), exc)).strip())
        self._set_busy(False)
        messagebox.showerror("出错", str(exc))

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        state = "disabled" if busy else "normal"
        self.btn_search.configure(state=state)
        self.btn_resolve.configure(state=state)
        self.btn_download.configure(state=state)
        self.btn_cancel.configure(state="normal" if busy else "disabled")

    # ------------------------------------------------------------ 目录

    def _update_default_dir(self) -> None:
        if self.side() == "client":
            appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
            path = os.path.join(appdata, ".minecraft", "mods")
        else:
            path = os.path.join(os.getcwd(), "server", "mods")
        self.var_dir.set(path)

    def _on_side_change(self) -> None:
        if self._dir_is_auto:
            self._update_default_dir()
        if self.resolution is not None:
            self.resolution = None
            self.rebuild_queue()
            self.log(f"目标端切换为{self.side_label()}，已清空解析结果，请重新解析。")
        self.set_status(f"目标端：{self.side_label()}")

    def pick_dir(self) -> None:
        path = filedialog.askdirectory(title="选择 mods 目录", initialdir=self.var_dir.get() or None)
        if path:
            self.var_dir.set(os.path.normpath(path))
            self._dir_is_auto = False

    def autodetect_dir(self) -> None:
        if self.side() == "client":
            appdata = os.environ.get("APPDATA") or ""
            cand = os.path.join(appdata, ".minecraft", "mods")
        else:
            cands = [os.path.join(os.getcwd(), p) for p in
                     ("server/mods", "mods", "minecraft/mods")]
            cand = next((c for c in cands if os.path.isdir(c)), cands[0])
        self.var_dir.set(os.path.normpath(cand))
        self._dir_is_auto = True
        self.log(f"目录设为 {cand}" + ("" if os.path.isdir(cand) else "（尚不存在，下载时自动创建）"))

    def open_dir(self) -> None:
        path = self.var_dir.get()
        if not path:
            return
        if not os.path.isdir(path):
            if not messagebox.askyesno("目录不存在", f"{path}\n\n要创建它吗？"):
                return
            try:
                os.makedirs(path, exist_ok=True)
            except OSError as e:
                messagebox.showerror("创建失败", str(e))
                return
        try:
            if sys.platform == "win32":
                os.startfile(path)  # noqa: S606
            else:
                import subprocess

                subprocess.Popen(["xdg-open", path])
        except OSError as e:
            messagebox.showerror("打开失败", str(e))

    # ------------------------------------------------------------ 搜索

    def _load_versions_async(self) -> None:
        self.run_bg(lambda: api.get_game_versions(), self._apply_versions)

    def _apply_versions(self, versions: list[str]) -> None:
        if not versions:
            return
        self.cb_ver.configure(values=versions)
        if self.var_ver.get() not in versions:
            pick = next((v for v in versions if v.startswith("1.21")), versions[0])
            self.var_ver.set(pick)
        self.log(f"已从 Modrinth 获取 {len(versions)} 个正式版游戏版本（当前选择 {self.var_ver.get()}）")

    def do_search(self) -> None:
        if self._busy:
            return
        q = self.var_query.get().strip()
        if not q:
            messagebox.showinfo("提示", "请输入要搜索的模组名称，例如 sodium / jei / create")
            return
        gv, ld, idx = self.game_version(), self.loader(), INDEX_MAP[self.var_index.get()]
        self._set_busy(True)
        self.set_status(f"正在搜索「{q}」…")
        self.log(f"搜索「{q}」  ({gv} / {ld} / 排序 {self.var_index.get()})")
        self.run_bg(lambda: api.search(q, gv, ld, limit=40, index=idx), self._on_search_done)

    def _on_search_done(self, result: dict) -> None:
        self._set_busy(False)
        hits = result.get("hits") or []
        self.search_hits = hits
        for iid in self.tv_search.get_children():
            self.tv_search.delete(iid)
        for i, h in enumerate(hits):
            self.tv_search.insert("", "end", iid=str(i), values=(
                h.get("title") or h.get("slug"),
                h.get("zh") or "—",
                fmt_count(h.get("downloads")),
                side_text(h),
                (h.get("description") or "")[:110],
            ))
        total = result.get("total_hits", len(hits))
        zh_n = result.get("zh_matched", 0)
        suffix = f"，其中 {zh_n} 个由中文译名匹配" if zh_n else ""
        self.set_status(f"搜索到 {total} 个结果，显示前 {len(hits)} 个{suffix}")
        self.log(f"搜索完成：共 {total} 条，显示 {len(hits)} 条{suffix}")
        if not hits:
            self.log("没有匹配结果 —— 可换个关键词，或检查游戏版本 / 加载器是否选对。"
                     "中文名搜不到时可以试试英文原名。")

    def add_selected(self) -> None:
        sel = self.tv_search.selection()
        if not sel:
            messagebox.showinfo("提示", "请先在左侧搜索结果里选中一个模组")
            return
        hit = self.search_hits[int(sel[0])]
        pid = hit.get("project_id") or hit.get("id")
        if any((p.get("project_id") or p.get("id")) == pid for p in self.queue_projects):
            self.set_status(f"「{hit.get('title')}」已在队列中")
            return
        self.queue_projects.append(hit)
        self.resolution = None          # 队列变了，旧解析结果作废
        self.rebuild_queue()
        self.log(f"已加入队列：{hit.get('title')}（{hit.get('slug')}）")

    def remove_selected(self) -> None:
        sel = set(self.tv_queue.selection())
        if not sel:
            return
        roots = {p.get("project_id") or p.get("id") for p in self.queue_projects}
        removed = sel & roots
        if not removed:
            messagebox.showinfo(
                "提示",
                "选中的是自动补齐的前置依赖，不能单独移除。\n"
                "如果要换掉它，请移除对应的本体模组后重新解析。",
            )
            return
        self.queue_projects = [p for p in self.queue_projects
                               if (p.get("project_id") or p.get("id")) not in removed]
        self.resolution = None
        self.rebuild_queue()
        self.log(f"已移除 {len(removed)} 个本体模组，请重新解析。")

    def clear_queue(self) -> None:
        self.queue_projects.clear()
        self.resolution = None
        self.rebuild_queue()
        self.log("队列已清空")

    # ------------------------------------------------------------ 队列显示

    def rebuild_queue(self) -> None:
        tv = self.tv_queue
        for iid in tv.get_children():
            tv.delete(iid)
        if self.resolution:
            for it in self.resolution.items:
                tv.insert("", "end", iid=it.project_id, values=(
                    it.title, it.zh or "—", it.version_number, it.kind,
                    "✓ 可用" if it.side_ok else "✗ 不支持",
                    it.size_text,
                    "待下载" if it.side_ok else "已跳过",
                ), tags=() if it.side_ok else ("unavail",))
        else:
            for p in self.queue_projects:
                pid = p.get("project_id") or p.get("id")
                tv.insert("", "end", iid=pid, values=(
                    p.get("title") or p.get("slug"), p.get("zh") or "—", "—", "本体",
                    side_text(p), "—", "待解析"))
        self._update_summary()

    def _update_summary(self) -> None:
        if self.resolution:
            items = self.resolution.items
            roots = sum(1 for i in items if i.is_root)
            deps = len(items) - roots
            ok = sum(1 for i in items if i.side_ok)
            self.var_summary.set(f"共 {len(items)} 项：本体 {roots} / 前置 {deps}"
                                 f"   ·   可下载 {ok} 项   ·   {self.resolution.total_size / 1048576:.1f} MB")
        elif self.queue_projects:
            self.var_summary.set(f"已选 {len(self.queue_projects)} 个模组 · 待解析")
        else:
            self.var_summary.set("队列为空")

    def _set_row_state(self, project_id: str, state: str) -> None:
        if self.tv_queue.exists(project_id):
            self.tv_queue.set(project_id, "state", state)
            if state == "失败":
                self.tv_queue.item(project_id, tags=("unavail",))

    # ------------------------------------------------------------ 解析

    def do_resolve(self) -> None:
        if self._busy:
            return
        if not self.queue_projects:
            messagebox.showinfo("提示", "队列是空的 —— 先在左边搜索并「加入队列」")
            return
        gv, ld, side = self.game_version(), self.loader(), self.side()
        roots = list(self.queue_projects)
        self._set_busy(True)
        self.set_status("正在解析依赖…")
        self.log(f"开始解析：{len(roots)} 个模组 · {gv} / {ld} / {SIDE_LABEL[side]}")

        def work():
            return resolver.Resolver(gv, ld, side, on_log=lambda m: self.log(m)).resolve(roots)

        self.run_bg(work, self._on_resolved)

    def _on_resolved(self, res: resolver.Resolution) -> None:
        self._set_busy(False)
        self.resolution = res
        self.rebuild_queue()
        roots = sum(1 for i in res.items if i.is_root)
        deps = len(res.items) - roots
        self.set_status(f"解析完成：本体 {roots} 个，自动补齐前置 {deps} 个")
        self.log(f"解析完成：共 {len(res.items)} 项（本体 {roots}，前置 {deps}），"
                 f"合计 {res.total_size / 1048576:.1f} MB")
        for w in res.warnings:
            self.log("  提示: " + w)
        if self._download_after_resolve:
            self._download_after_resolve = False
            self._start_download()

    # ------------------------------------------------------------ 下载

    def do_download(self) -> None:
        if self._busy:
            return
        if not self.queue_projects:
            messagebox.showinfo("提示", "队列是空的 —— 先在左边搜索并「加入队列」")
            return
        target = self.var_dir.get().strip()
        if not target:
            messagebox.showinfo("提示", "请先设置模组目录")
            return
        if self.resolution is None:
            self._download_after_resolve = True
            self.do_resolve()
            return
        self._start_download()

    def _start_download(self) -> None:
        res = self.resolution
        if not res or not res.items:
            messagebox.showinfo("提示", "没有可下载的内容")
            return
        target = os.path.normpath(self.var_dir.get().strip())
        items = res.items
        ready = [i for i in items if i.side_ok]
        if not ready:
            messagebox.showwarning("无可下载项", f"这些模组都不支持{self.side_label()}，请切换目标端。")
            return

        self._progress_total = sum(i.size for i in ready)
        self._progress_done = 0
        self.pb.configure(maximum=max(self._progress_total, 1), value=0)
        self._set_busy(True)
        self.set_status(f"正在下载 {len(ready)} 个文件…")

        dl = downloader.Downloader(
            target,
            concurrency=self._int_var(self.var_conc, 3),
            file_threads=self._int_var(self.var_chunks, 4),
            skip_unsupported=True,
            on_log=lambda m: self.log(m),
            on_progress=lambda d, t, f: self.q.put(("progress", d, t, f)),
            on_item=lambda item, st: self.q.put(("item", item.project_id, st)),
        )
        self.downloader = dl
        self.run_bg(lambda: dl.run(items), self._on_download_done)

    def _on_progress(self, done: int, total: int, filename: str) -> None:
        if total > 0:
            self.pb.configure(maximum=total, value=done)
        pct = (done / total * 100) if total else 0
        name = f"  ·  {filename}" if filename else ""
        self.var_status.set(f"下载中 {pct:5.1f}%  ({done / 1048576:.1f}/{total / 1048576:.1f} MB){name}")

    def _on_download_done(self, results) -> None:
        self._set_busy(False)
        self.downloader = None
        ok = sum(1 for r in results if r.ok and not r.skipped)
        skip = sum(1 for r in results if r.skipped)
        bad = [r for r in results if not r.ok]
        self.pb.configure(value=self.pb.cget("maximum"))
        self.set_status(f"下载结束：新下载 {ok}，跳过 {skip}，失败 {len(bad)}")
        self.log(f"下载结束：新下载 {ok} 个，跳过 {skip} 个，失败 {len(bad)} 个 → {self.var_dir.get()}")
        if bad:
            detail = "\n".join(f"· {r.title}: {r.message}" for r in bad[:10])
            messagebox.showwarning("部分文件失败", f"{len(bad)} 个文件下载失败：\n\n{detail}")
        else:
            messagebox.showinfo("完成", f"已就绪！\n新下载 {ok} 个模组，跳过 {skip} 个已存在文件。\n\n"
                                        f"目录：{self.var_dir.get()}")
            self.log("提示：前置已自动补齐，启动游戏/服务端即可。若仍报缺依赖，检查是否漏了可选前置。")

    def do_cancel(self) -> None:
        if self.downloader:
            self.downloader.cancel()
            self.log("已请求取消下载…")
            self.set_status("正在取消…")

    # ------------------------------------------------------------ 退出

    def _on_close(self) -> None:
        if self.downloader and not self.downloader.cancelled:
            if not messagebox.askyesno("正在下载", "下载还没结束，确定退出吗？"):
                return
            self.downloader.cancel()
        self.destroy()


def main() -> int:
    enable_dpi_awareness()
    app = App()
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
