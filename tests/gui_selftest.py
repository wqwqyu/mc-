"""GUI 端到端自测：驱动真实交互链（搜索 → 加队列 → 解析 → 下载），并在关键节点截图。

    python tests/gui_selftest.py

会把模组下载到临时目录，结束后不会污染你的 .minecraft。
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tkinter import messagebox  # noqa: E402

import mcmoddl.gui as G  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _silence_dialogs() -> None:
    def fake(*a, **k):
        head = a[0] if a else ""
        body = str(a[1])[:160] if len(a) > 1 else ""
        print(f"[对话框] {head} | {body}")

    for name in ("showinfo", "showwarning", "showerror"):
        setattr(messagebox, name, fake)
    messagebox.askyesno = lambda *a, **k: True


def shot(tag: str) -> None:
    path = os.path.join(ROOT, f"_shot_{tag}.png")
    ps = (
        "Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
        "$b=[System.Windows.Forms.Screen]::PrimaryScreen.Bounds;"
        "$bmp=New-Object System.Drawing.Bitmap $b.Width,$b.Height;"
        "$g=[System.Drawing.Graphics]::FromImage($bmp);"
        "$g.CopyFromScreen($b.Location,[System.Drawing.Point]::Empty,$b.Size);"
        f"$bmp.Save('{path}',[System.Drawing.Imaging.ImageFormat]::Png);"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True)
    print("截图 ->", path)


def main() -> int:
    _silence_dialogs()
    tmp = tempfile.mkdtemp(prefix="mcmoddl_gui_")
    app = G.App()
    app.attributes("-topmost", True)
    app.var_ver.set("1.20.1")
    app.var_loader.set("Forge")
    app.var_side.set("客户端")
    app.var_dir.set(tmp)
    app.var_conc.set(2)
    app.var_chunks.set(4)
    app._dir_is_auto = False

    state: dict = {}

    def wait(pred, then, label, timeout=90):
        t0 = time.time()

        def tick():
            try:
                ok = pred()
            except Exception as e:  # noqa: BLE001
                print(f"[{label}] 断言异常: {e}")
                ok = True
            if ok:
                then()
                return
            if time.time() - t0 > timeout:
                print(f"[超时] {label}")
                then()
                return
            app.after(400, tick)

        app.after(400, tick)

    def phase_search():
        print("--- 阶段 1：中文搜索「机械动力」")
        app.var_query.set("机械动力")
        app.do_search()
        wait(lambda: len(app.tv_search.get_children()) > 0, phase_pick, "搜索")

    def phase_pick():
        names = [app.tv_search.item(i)["values"][:2] for i in app.tv_search.get_children()[:5]]
        print("搜索结果前 5 [名称, 中文名]:")
        for n in names:
            print("   ", n)
        app.tv_search.selection_set("0")
        app.add_selected()
        app.after(400, phase_resolve)

    def phase_resolve():
        print("--- 阶段 2：解析依赖")
        app.do_resolve()
        wait(lambda: app.resolution is not None, phase_review, "解析")

    def phase_review():
        rows = app.tv_queue.get_children()
        print(f"解析后队列 {len(rows)} 行：")
        for r in rows:
            v = app.tv_queue.item(r)["values"]
            print("   ", " | ".join(str(x) for x in v))
        print("摘要:", app.var_summary.get())
        shot("queue")
        app.after(1500, phase_download)

    def phase_download():
        print("--- 阶段 3：下载")
        state["started"] = True
        app.do_download()
        wait(lambda: state.get("started") and app.downloader is None and not app._busy,
             phase_done, "下载", timeout=240)

    def phase_done():
        def report():
            print("最终状态:", app.var_status.get())
            print("队列各行实际内容：")
            for r in app.tv_queue.get_children():
                print("   ", " | ".join(str(x) for x in app.tv_queue.item(r)["values"]))
            files = sorted(os.listdir(tmp))
            total = sum(os.path.getsize(os.path.join(tmp, f)) for f in files)
            print(f"临时目录 {tmp} 共 {len(files)} 个文件 / {total / 1048576:.2f} MB")
            for f in files:
                print(f"   {f}  {os.path.getsize(os.path.join(tmp, f)):,} 字节")
            shot("done")
            app.after(2000, app.destroy)

        app.after(1000, report)

    app.after(1200, phase_search)
    app.mainloop()
    print("GUI 自测结束")
    return 0


if __name__ == "__main__":
    sys.exit(main())
