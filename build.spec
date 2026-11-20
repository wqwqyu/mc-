# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（目录模式，启动最快）。

用法：
    python -m PyInstaller build.spec --noconfirm --clean

产物：
    dist/MCModDownloader/MCModDownloader.exe     ← 双击即用
    dist/MCModDownloader/_internal/              ← 必须和 exe 放在一起

想要单文件 exe：把文件末尾的 EXE + COLLECT 换成下面这段（启动会慢 2~3 秒，
因为每次运行都要把内置文件解压到临时目录）：

    exe = EXE(
        pyz, a.scripts, a.binaries, a.datas, [],
        name="MCModDownloader",
        debug=False, strip=False, upx=False,
        console=False, onefile=True,   # 关键：onefile
    )
"""

import os

# 中文译名库必须一起打包，否则 exe 运行起来搜中文会没反应
DATAS = [
    ("mcmoddl/data/zh_names.json", "mcmoddl/data"),
    ("mcmoddl/data/zh_supplement.json", "mcmoddl/data"),
]

# 这些模块是在函数内部 import 的，显式列出来防止被漏掉
HIDDEN = [
    "mcmoddl.zh_search",
    "mcmoddl.resolver",
    "mcmoddl.downloader",
    "mcmoddl.api",
    "mcmoddl.cli",
    "tkinter",
    "tkinter.ttk",
    "tkinter.filedialog",
    "tkinter.messagebox",
]

a = Analysis(
    ["run_gui.pyw"],
    pathex=[os.path.abspath(".")],
    binaries=[],
    datas=DATAS,
    hiddenimports=HIDDEN,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # 明确排除掉这些大块头，能让产物小一截
    excludes=["pytest", "setuptools", "pip", "numpy", "PIL", "matplotlib", "scipy", "pandas"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MCModDownloader",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # GUI 程序：不要黑色控制台窗口
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

#coll = COLLECT(
#    exe,
#    a.binaries,
#    a.datas,
#    strip=False,
#    upx=False,
#    upx_exclude=[],
#    name="MCModDownloader",
#)
