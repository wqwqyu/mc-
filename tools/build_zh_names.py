"""从 PCL2 的 WikiEntries.txt 生成精简的中文译名库。

PCL2 (Meloong-Git/PCL) 的 `PCLCS/Resource/WikiEntries.txt` 是社区维护的 MC 百科译名表，
格式：

    slug|中文名                  纯 CurseForge 条目
    slug@|中文名                 CurseForge + Modrinth 同 slug
    @slug|中文名                 仅 Modrinth
    cfslug@modrinslug|中文名     两平台 slug 不同
    a|名1¨b|名2                  ¨ 分隔同一行的多个条目

本脚本只保留**有 Modrinth slug** 的条目（本下载器只用 Modrinth），输出：

    mcmoddl/data/zh_names.json  ->  {"entries": [{"slug": "...", "zh": "...", "en": "..."}]}

用法：
    python tools/build_zh_names.py <WikiEntries.txt 路径> [输出路径]
"""
from __future__ import annotations

import json
import os
import re
import sys

CJK = re.compile(r"[\u4e00-\u9fff]")
SLUG_OK = re.compile(r"^[a-z0-9][a-z0-9\-_.]*$")


def title_from_slug(slug: str) -> str:
    return " ".join(w.capitalize() for w in slug.replace("_", "-").split("-") if w)


def parse_slugs(raw: str) -> tuple[str | None, str | None]:
    """返回 (curseforge_slug, modrinth_slug)。"""
    raw = raw.strip()
    if not raw:
        return None, None
    if raw.startswith("@"):
        return None, raw[1:]
    if raw.endswith("@"):
        s = raw.rstrip("@")
        return s, s
    if "@" in raw:
        cf, _, mr = raw.partition("@")
        return cf or None, mr or None
    return raw, None


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 1
    src = argv[0]
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    dst = argv[1] if len(argv) > 1 else os.path.join(here, "mcmoddl", "data", "zh_names.json")

    with open(src, "r", encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()

    seen: dict[str, dict] = {}
    total_rows = skipped = 0

    for line in lines:
        line = line.strip()
        if not line:
            continue
        # popularity 行是 base86 编码的纯字符，没有 "|"，会被下面挡掉
        if "|" not in line:
            skipped += 1
            continue
        for chunk in line.split("¨"):
            if "|" not in chunk:
                continue
            parts = chunk.split("|")
            cf_slug, mr_slug = parse_slugs(parts[0])
            if not mr_slug:
                total_rows += 1
                continue          # 只在 CurseForge 上，本工具用不到
            if not SLUG_OK.match(mr_slug):
                continue
            zh = parts[-1].strip() if len(parts) >= 2 else ""
            if not zh:
                continue
            total_rows += 1
            if "*" in zh:         # * 代表"用英文名占位"
                zh = zh.replace("*", f" ({title_from_slug(mr_slug)})")
            if mr_slug in seen:
                # 同一 slug 多个条目时保留信息量更大的那个
                if len(zh) > len(seen[mr_slug]["zh"]):
                    seen[mr_slug]["zh"] = zh
                continue
            seen[mr_slug] = {"slug": mr_slug, "zh": zh, "en": title_from_slug(mr_slug)}

    entries = sorted(seen.values(), key=lambda e: e["slug"])
    with_cjk = [e for e in entries if CJK.search(e["zh"])]

    os.makedirs(os.path.dirname(dst), exist_ok=True)
    payload = {
        "_source": "PCL2 (Meloong-Git/PCL) WikiEntries.txt, 译名源自 MC 百科 mcmod.cn",
        "_note": "仅保留有 Modrinth slug 的条目；zh 字段可能形如 '中文名 (English Name)'",
        "count": len(entries),
        "entries": with_cjk,
    }
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))

    print(f"原始数据行: {len(lines)}（跳过非条目行 {skipped}）")
    print(f"有 Modrinth slug 的条目: {total_rows}  去重后: {len(entries)}")
    print(f"含中文名的条目: {len(with_cjk)}")
    print(f"输出: {dst}  ({os.path.getsize(dst):,} 字节)")
    for e in with_cjk[:8]:
        print(f"   {e['slug']:<28} {e['zh']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
