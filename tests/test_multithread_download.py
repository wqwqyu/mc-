"""多线程下载实测：同一大文件分别用单线程 / 4 线程分块下载，比较耗时并校验结果一致。

    python tests/test_multithread_download.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcmoddl import api, downloader  # noqa: E402
from mcmoddl.resolver import ResolvedItem  # noqa: E402


def make_item(slug: str, gv: str, loader: str) -> ResolvedItem:
    versions = api.get_versions(slug, gv, loader)
    if not versions:
        raise SystemExit(f"{slug} 在 {gv}/{loader} 下没有版本")
    v = versions[0]
    f = api.primary_file(v)
    return ResolvedItem(
        project_id=slug, slug=slug, title=v.get("name") or slug,
        version_id=v["id"], version_number=v.get("version_number") or "",
        filename=f["filename"], url=f["url"], size=int(f["size"]),
        sha512=(f.get("hashes") or {}).get("sha512") or "",
    )


def run_once(item: ResolvedItem, threads: int, label: str) -> tuple[float, str, int]:
    tmp = tempfile.mkdtemp(prefix=f"mcdl_{label}_")
    dl = downloader.Downloader(
        tmp, concurrency=1, file_threads=threads,
        on_log=lambda m: None,
        on_progress=lambda *_a: None,
    )
    t0 = time.perf_counter()
    results = dl.run([item])
    elapsed = time.perf_counter() - t0
    r = results[0]
    if not r.ok:
        raise SystemExit(f"{label} 下载失败: {r.message}")
    path = r.path
    digest = downloader.sha512_of(path)
    return elapsed, digest, r.pieces


def main() -> int:
    item = make_item("create", "1.20.1", "forge")
    print(f"测试文件: {item.filename}  {item.size / 1048576:.1f} MB\n")

    print("--- 单线程 ---")
    t1, h1, p1 = run_once(item, 1, "single")
    print(f"  耗时 {t1:.2f}s  分块数 {p1}  SHA512 {h1[:16]}…")

    print("--- 4 线程分块 ---")
    t4, h4, p4 = run_once(item, 4, "quad")
    print(f"  耗时 {t4:.2f}s  分块数 {p4}  SHA512 {h4[:16]}…")

    print("--- 8 线程分块 ---")
    t8, h8, p8 = run_once(item, 8, "oct")
    print(f"  耗时 {t8:.2f}s  分块数 {p8}  SHA512 {h8[:16]}…")

    print()
    ok = True
    if p4 < 2:
        print("❌ 4 线程没有真正分块")
        ok = False
    if not (h1 == h4 == h8):
        print("❌ 三次下载的 SHA512 不一致，分块合并有问题")
        ok = False
    else:
        print("✅ 三种方式下载的文件 SHA512 完全一致")
    if h1 != item.sha512:
        print("❌ 与 Modrinth 记录的 SHA512 不符")
        ok = False
    else:
        print("✅ 与 Modrinth 官方 SHA512 一致")
    print(f"加速比: 4 线程 {t1 / max(t4, 0.01):.2f}x, 8 线程 {t1 / max(t8, 0.01):.2f}x")

    # 文件级并发：三个模组同时下
    print("\n--- 文件级并发（3 个模组同时，每个 4 块）---")
    items = [make_item(s, "1.20.1", "forge")
             for s in ("create", "jei", "waystones")]
    tmp = tempfile.mkdtemp(prefix="mcdl_multi_")
    dl = downloader.Downloader(tmp, concurrency=3, file_threads=4)
    t0 = time.perf_counter()
    results = dl.run(items)
    elapsed = time.perf_counter() - t0
    total = sum(i.size for i in items)
    print(f"  3 个文件 / {total / 1048576:.1f} MB 用时 {elapsed:.2f}s"
          f"（{total / 1048576 / elapsed:.2f} MB/s）")
    for r in results:
        print(f"  {'OK ' if r.ok else 'FAIL'} {r.title[:34]:<36} {r.pieces} 块  {r.message}")
    if not all(r.ok for r in results):
        ok = False

    print("\n" + ("全部通过 ✔" if ok else "存在问题 ✘"))
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
