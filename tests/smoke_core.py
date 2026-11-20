"""核心层冒烟测试：真实调用 Modrinth，验证搜索 / 依赖解析 / 端过滤 / 下载校验。"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcmoddl import api, downloader, resolver  # noqa: E402


def show(res: resolver.Resolution, title: str) -> None:
    print(f"\n=== {title} ===")
    print(f"目标: {res.game_version} / {res.loader} / {res.side}   共 {len(res.items)} 项, "
          f"{res.total_size / 1048576:.1f} MB")
    for it in res.items:
        flag = "" if it.side_ok else "  [端不支持]"
        print(f"  {'本体' if it.is_root else '└ 依赖'} {it.title:<32} {it.version_number:<26} "
              f"{it.size_text:>9}  {it.filename}{flag}")
        if it.required_by:
            print(f"        被需要: {', '.join(it.required_by)}")
    if res.warnings:
        print("  警告:")
        for w in res.warnings:
            print(f"   - {w}")


def main() -> int:
    # 1) 搜索
    s = api.search("jei", game_version="1.21.1", loader="fabric", limit=3)
    print(f"搜索 'jei' 1.21.1/fabric -> {s['total_hits']} 条, 前 3:",
          [h["slug"] for h in s["hits"]])
    assert s["hits"]

    # 2) 客户端解析：Mod Menu + Sodium Extra 应自动带出前置
    roots = [api.get_project("modmenu"), api.get_project("sodium-extra")]
    r_cli = resolver.Resolver("1.21.1", "fabric", "client", on_log=lambda m: None).resolve(roots)
    show(r_cli, "客户端解析")
    by_slug = {i.slug: i for i in r_cli.items}
    assert "fabric-api" in by_slug, f"应自动带上 Fabric API 前置, 实际={sorted(by_slug)}"
    assert "placeholder-api" in by_slug, "应自动带上 Text Placeholder API"
    assert by_slug["modmenu"].version_number.startswith("11."), \
        f"Mod Menu 版本应回退到 1.21.1 兼容版, 实际={by_slug['modmenu'].version_number}"
    assert by_slug["placeholder-api"].required_by, "前置应记录被谁需要"

    # 3) 服务端解析：纯客户端模组不能被当成服务端可用
    r_srv = resolver.Resolver("1.21.1", "fabric", "server", on_log=lambda m: None).resolve(roots)
    show(r_srv, "服务端解析")
    bad = [i.slug for i in r_srv.items if i.side_ok and i.slug in ("sodium", "sodium-extra", "modmenu")]
    assert not bad, f"纯客户端模组在服务端解析中不应标记为可用: {bad}"
    assert any("仅另一端" in w or "不支持服务端端" in w or "不支持服务端" in w for w in r_srv.warnings), \
        "应给出端不支持警告"

    # 4) Forge 1.20.1 递归依赖（二级前置）
    r_forge = resolver.Resolver("1.20.1", "forge", "server", on_log=lambda m: None).resolve(
        [api.get_project("create-steam-n-rails")]
    )
    show(r_forge, "Forge 1.20.1 服务端解析")
    assert "create" in {i.slug for i in r_forge.items}, "应递归解析出 Create 本体"

    # 5) 真实下载（客户端解析里体积最小的两个，快速验证）
    targets = sorted([i for i in r_cli.items if i.side_ok], key=lambda i: i.size)[:2]
    tmp = tempfile.mkdtemp(prefix="mcmoddl_test_")
    print(f"\n=== 真实下载 {len(targets)} 个文件到 {tmp} ===")
    dl = downloader.Downloader(tmp, on_log=lambda m: print("  " + m))
    results = dl.run(targets)
    for r in results:
        print(f"  {r.title}: ok={r.ok} skipped={r.skipped} {r.message}")
    assert all(r.ok for r in results), "下载失败"
    for r in results:
        assert os.path.exists(r.path), f"文件不存在: {r.path}"
        print(f"  ✓ {os.path.basename(r.path)}  {os.path.getsize(r.path):,} 字节  sha512 已校验")

    # 6) 重复下载应全部跳过（幂等）
    print("\n=== 二次下载（应全部跳过） ===")
    again = downloader.Downloader(tmp, on_log=lambda m: print("  " + m)).run(targets)
    assert all(r.skipped for r in again), "二次下载应当跳过"
    print("\n全部断言通过 ✔")
    return 0


if __name__ == "__main__":
    sys.exit(main())
