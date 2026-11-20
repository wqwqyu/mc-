"""图形界面入口（双击 .pyw 时不显示控制台窗口）。

打包后自查用：
    MCModDownloader.exe --selftest
结果写到 exe 同目录的 selftest.txt —— 用来确认打包时译名库等数据文件有没有带进去。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _selftest() -> int:
    """打印/写入运行环境自检结果，供打包后验证。"""
    lines = []
    try:
        base = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
    except Exception:  # noqa: BLE001
        base = os.getcwd()
    lines.append(f"frozen={getattr(sys, 'frozen', False)}")
    lines.append(f"exe/脚本目录={base}")
    lines.append(f"_MEIPASS={getattr(sys, '_MEIPASS', '')}")
    try:
        from mcmoddl import zh_search
        idx = zh_search.ZhIndex.get()
        lines.append(f"译名库条目={len(idx.entries)}")
        lines.append(f"数据文件={idx.path}")
        lines.append(f"lookup('钠')={idx.lookup('钠')[:3]}")
        lines.append(f"name_of('sodium')={idx.name_of('sodium')}")
    except Exception as exc:  # noqa: BLE001
        lines.append(f"译名库加载失败: {exc!r}")
    try:
        from mcmoddl import api
        hits = api.search("机械动力", "1.20.1", "forge", limit=3).get("hits") or []
        lines.append("搜索('机械动力')=" + str([h.get("slug") for h in hits]))
    except Exception as exc:  # noqa: BLE001
        lines.append(f"搜索失败: {exc!r}")

    text = "\n".join(lines)
    try:
        with open(os.path.join(base, "selftest.txt"), "w", encoding="utf-8") as f:
            f.write(text + "\n")
    except OSError:
        pass
    try:
        print(text)          # 有控制台时直接看
    except Exception:  # noqa: BLE001
        pass
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    from mcmoddl.gui import main

    sys.exit(main())
