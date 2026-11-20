"""命令行入口：适合脚本化 / 开服时批量拉取。

示例
----
    # 看有哪些模组可装（只搜索，不下载）
    python run_cli.py --mc 1.21.1 --loader fabric --search sodium

    # 客户端：装 Create 系列及其全部前置
    python run_cli.py --side client --mc 1.20.1 --loader forge --add create --add jei

    # 服务端：只解析看看会装什么，不真下载
    python run_cli.py --side server --mc 1.20.1 --loader forge --add create-steam-n-rails --dry-run
"""
from __future__ import annotations

import argparse
import os
import sys

from . import api, downloader, resolver


def _fix_console() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass


def _default_dir(side: str) -> str:
    if side == "client":
        appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(appdata, ".minecraft", "mods")
    return os.path.join(os.getcwd(), "server", "mods")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="run_cli.py",
        description="Minecraft Java 模组下载器（Modrinth）· 自动补齐前置依赖",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--mc", default="1.21.1", help="Minecraft 版本，默认 1.21.1")
    p.add_argument("--loader", default="fabric", choices=list(api.LOADERS), help="模组加载器")
    p.add_argument("--side", default="client", choices=["client", "server"],
                   help="下载目标端：client（客户端）或 server（服务端）")
    p.add_argument("--dir", default=None, help="mods 输出目录（默认按目标端推断）")
    p.add_argument("--add", action="append", default=[], metavar="SLUG",
                   help="要安装的项目 slug 或 ID，可重复")
    p.add_argument("--search", default=None, metavar="关键词", help="搜索并列出结果")
    p.add_argument("--pick", default=None, metavar="序号", help="配合 --search，取第几个结果（从 1 起，逗号分隔）")
    p.add_argument("--limit", type=int, default=10, help="搜索结果数量上限，默认 10")
    p.add_argument("--optional", action="store_true", help="同时下载可选前置（体积更大）")
    p.add_argument("--dry-run", action="store_true", help="只解析并列出，不下载")
    p.add_argument("--jobs", type=int, default=3, help="同时下载几个文件，默认 3")
    p.add_argument("--threads", type=int, default=4, help="单个大文件切成几块并行，默认 4")
    p.add_argument("--no-verify", action="store_true", help="跳过 SHA512 校验（不建议）")
    p.add_argument("--list-versions", action="store_true", help="列出可用游戏版本后退出")
    p.add_argument("--no-zh", action="store_true", help="关闭中文译名搜索")
    return p


def print_tree(res: resolver.Resolution) -> None:
    side = "客户端" if res.side == "client" else "服务端"
    print(f"\n目标：{res.game_version} / {res.loader} / {side}")
    print(f"共 {len(res.items)} 项，合计 {res.total_size / 1048576:.1f} MB\n")
    for it in res.items:
        mark = "  " if it.is_root else "  └ "
        flag = "" if it.side_ok else "   [该端不支持，跳过]"
        zh = f"{it.zh}" if it.zh else ""
        print(f"{mark}{it.title:<32}{zh:<26}{it.version_number:<24}{it.size_text:>10}  {it.filename}{flag}")
        if it.required_by and not it.is_root:
            print(f"      被需要：{', '.join(it.required_by)}")
    if res.warnings:
        print("\n提示：")
        for w in res.warnings:
            print(f"  · {w}")


def main(argv: list[str] | None = None) -> int:
    _fix_console()
    args = build_parser().parse_args(argv)

    if args.list_versions:
        for v in api.get_game_versions()[:40]:
            print(v)
        return 0

    roots: list[dict] = []

    if args.search:
        print(f"搜索「{args.search}」({args.mc} / {args.loader}) …")
        hits = api.search(args.search, args.mc, args.loader,
                          limit=args.limit, use_zh=not args.no_zh).get("hits") or []
        if not hits:
            print("没有结果。中文名可以试试英文原名。")
            return 1
        for i, h in enumerate(hits, 1):
            zh = h.get("zh") or ""
            print(f"  {i:>2}. {h.get('title'):<34} {zh:<30} {h.get('slug'):<24} "
                  f"[{side_word(h)}]  {h.get('downloads', 0):,}")
        if not args.pick:
            print("\n用 --pick 1 或 --pick 1,3 选定要安装的模组。")
            return 0
        for token in args.pick.replace(" ", "").split(","):
            try:
                roots.append(hits[int(token) - 1])
            except (ValueError, IndexError):
                print(f"忽略无效序号：{token}")
    for slug in args.add:
        try:
            roots.append(api.get_project(slug))
        except api.NotFound:
            print(f"找不到项目：{slug}")
            return 1

    if not roots:
        build_parser().print_help()
        return 1

    res = resolver.Resolver(
        args.mc, args.loader, args.side, include_optional=args.optional,
        on_log=lambda m: print(m),
    ).resolve(roots)
    print_tree(res)

    if args.dry_run:
        return 0

    target = os.path.normpath(args.dir or _default_dir(args.side))
    dl = downloader.Downloader(target, concurrency=args.jobs, file_threads=args.threads,
                               verify=not args.no_verify,
                               on_log=lambda m: print("  " + m))
    results = dl.run(res.items)
    bad = [r for r in results if not r.ok]
    print(f"\n完成：{sum(1 for r in results if r.ok and not r.skipped)} 个新下载，"
          f"{sum(1 for r in results if r.skipped)} 个跳过，{len(bad)} 个失败 → {target}")
    return 0 if not bad else 2


def side_word(h: dict) -> str:
    c = (h.get("client_side") or "").lower()
    s = (h.get("server_side") or "").lower()
    if s == "unsupported":
        return "仅客户端"
    if c == "unsupported":
        return "仅服务端"
    return "双端"


if __name__ == "__main__":
    sys.exit(main())
