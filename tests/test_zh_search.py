"""中文搜索回归测试：真实调用 API，验证中文关键词能搜到正确的模组。"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcmoddl import api, zh_search  # noqa: E402

CASES = [
    # (中文查询, MC版本, 加载器, 期望第一个是哪个 slug)
    ("钠", "1.21.1", "fabric", "sodium"),
    ("机械动力", "1.20.1", "forge", "create"),
    ("物品管理器", "1.21.1", "fabric", None),
    ("沉浸工程", "1.20.1", "forge", "immersiveengineering"),
    ("匠魂", "1.20.1", "forge", "tinkers-construct"),
    ("苹果皮", "1.21.1", "fabric", "appleskin"),
    ("你将去的生物群系", "1.18.2", "forge", "biomesyougo"),
    ("钠 扩展", "1.21.1", "fabric", "sodium-extra"),
]

# 繁体输入应当等价于简体
T2S_CASES = [("機械動力", "机械动力"), ("鈉", "钠")]


def main() -> int:
    idx = zh_search.ZhIndex.get()
    print(f"译名库条目: {len(idx.entries)}\n")

    ok = fail = 0
    for query, gv, loader, expect in CASES:
        res = api.search(query, gv, loader, limit=8)
        hits = res.get("hits") or []
        first = hits[0]["slug"] if hits else "(空)"
        zh_matched = res.get("zh_matched", 0)
        passed = (expect is None and bool(hits)) or (first == expect)
        mark = "OK  " if passed else "FAIL"
        print(f"[{mark}] 「{query}」{gv}/{loader}  → {len(hits)} 条，中文命中 {zh_matched}，首位 {first}")
        for h in hits[:4]:
            print(f"        {h['slug']:<30} {(h.get('zh') or '—'):<36} {str(h.get('title'))[:34]}")
        if passed:
            ok += 1
        else:
            fail += 1
        print()

    print("--- 繁体等价性 ---")
    for trad, simp in T2S_CASES:
        a, b = idx.lookup(trad), idx.lookup(simp)
        same = bool(a) and a[:3] == b[:3]
        print(f"[{'OK  ' if same else 'FAIL'}] {trad} -> {a[:3]}   vs   {simp} -> {b[:3]}")
        ok, fail = (ok + 1, fail) if same else (ok, fail + 1)

    # 英文搜索仍然正常，且能补出中文名
    print("\n--- 英文搜索仍可用（附中文名）---")
    res = api.search("sodium", "1.21.1", "fabric", limit=3)
    for h in res["hits"][:3]:
        print(f"    {h['slug']:<28} {(h.get('zh') or '—')}")
    if res["hits"] and res["hits"][0]["slug"] == "sodium":
        ok += 1
    else:
        fail += 1
        print("[FAIL] 英文搜 sodium 结果异常")

    print(f"\n通过 {ok}，失败 {fail}")
    return 0 if fail == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
