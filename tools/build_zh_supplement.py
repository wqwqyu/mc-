"""生成译名补充表：PCL 的 WikiEntries 缺了一些老牌/热门模组，这里用 Modrinth API
逐个核实 slug 是否存在，只把验证通过的写进 `data/zh_supplement.json`。

用法：python tools/build_zh_supplement.py
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcmoddl import api  # noqa: E402

# (slug, 中文名, 期望标题里的关键词) —— 关键词用于防呆校验，防止 slug 变更后张冠李戴
CANDIDATES = [
    ("fabric-api", "Fabric API", "fabric api"),
    ("iris", "Iris 光影 (Iris Shaders)", "iris"),
    ("immersiveengineering", "沉浸工程 (Immersive Engineering)", "immersive"),
    ("tinkers-construct", "匠魂 (Tinkers' Construct)", "tinker"),
    ("farmers-delight", "农夫乐事 (Farmer's Delight)", "farmer"),
    ("waystones", "传送石碑 (Waystones)", "waystone"),
    ("journeymap", "旅行地图 (JourneyMap)", "journeymap"),
    ("curios", "饰品栏 (Curios API)", "curios"),
    ("architectury-api", "架构 API (Architectury API)", "architectury"),
    ("cloth-config", "布料配置 (Cloth Config API)", "cloth config"),
    ("rei", "REI 物品管理器 (Roughly Enough Items)", "roughly enough"),
    ("epic-fight", "史诗战斗 (Epic Fight)", "epic fight"),
    ("ars-nouveau", "新生魔艺 (Ars Nouveau)", "ars nouveau"),
    ("ae2", "应用能源2 (Applied Energistics 2)", "applied energistic"),
    ("refined-storage", "精致存储 (Refined Storage)", "refined storage"),
    ("storagedrawers", "抽屉 (Storage Drawers)", "storage drawers"),
    ("quark", "夸克 (Quark)", "quark"),
    ("supplementaries", "附属装饰 (Supplementaries)", "supplementaries"),
    ("jade", "Jade 提示 (Jade)", "jade"),
    ("embeddium", "嵌入 (Embeddium)", "embeddium"),
    ("rubidium", "铷 (Rubidium)", "rubidium"),
    ("oculus", "光影 (Oculus)", "oculus"),
    ("biomesyougo", "你将去的生物群系 (Oh The Biomes You'll Go)", "biomes"),
    ("biomes-o-plenty", "超多生物群系 (Biomes O' Plenty)", "biomes o"),
    ("blood-magic", "血魔法 (Blood Magic)", "blood magic"),
    ("thermal-expansion", "热力膨胀 (Thermal Expansion)", "thermal"),
    ("industrial-foregoing", "工业先锋 (Industrial Foregoing)", "industrial foregoing"),
    ("createaddition", "机械动力：电力附加 (Create Crafts & Additions)", "create crafts"),
    ("create-fabric", "机械动力 (Create Fabric)", "create"),
    ("sodium-extra", "钠：附加 (Sodium Extra)", "sodium extra"),
    ("reeses-sodium-options", "Reese 的钠设置 (Reese's Sodium Options)", "sodium options"),
    ("lithium", "锂 (Lithium)", "lithium"),
    ("starlight", "星光 (Starlight)", "starlight"),
    ("ferrite-core", "铁氧核心 (FerriteCore)", "ferritecore"),
    ("memoryleakfix", "内存泄漏修复 (Memory Leak Fix)", "memory"),
    ("entityculling", "实体剔除 (Entity Culling)", "entity culling"),
    ("immediatelyfast", "即时加速 (ImmediatelyFast)", "immediatelyfast"),
    ("simple-voice-chat", "简易语音聊天 (Simple Voice Chat)", "voice chat"),
    ("distanthorizons", "远处的地平线 (Distant Horizons)", "distant horizon"),
    ("xaeros-world-map", "Xaero的世界地图 (Xaero's World Map)", "xaero"),
    ("alexs-mobs", "Alex的生物 (Alex's Mobs)", "alex"),
    ("mowzies-mobs", "Mowzie的生物 (Mowzie's Mobs)", "mowzie"),
    ("pams-harvestcraft-2-food-core", "潘马斯农场 (Pam's HarvestCraft)", "pam"),
]


def main() -> int:
    slugs = [c[0] for c in CANDIDATES]
    found = {}
    for i in range(0, len(slugs), 50):
        for p in api.get_projects(slugs[i:i + 50]):
            found[p["slug"]] = p

    out, missing, suspicious = [], [], []
    for slug, zh, expect in CANDIDATES:
        p = found.get(slug)
        if not p:
            missing.append(slug)
            continue
        title = (p.get("title") or "").lower()
        if expect.lower() not in title:
            suspicious.append((slug, p.get("title"), expect))
            continue
        out.append({"slug": slug, "zh": zh, "en": p.get("title")})

    dst = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "mcmoddl", "data", "zh_supplement.json")
    with open(dst, "w", encoding="utf-8") as f:
        json.dump({"_note": "手工补充的常用模组译名（slug 已用 Modrinth API 核实）",
                   "entries": out}, f, ensure_ascii=False, indent=1)

    print(f"候选 {len(CANDIDATES)} 个 → 通过 {len(out)}，不存在 {len(missing)}，标题不匹配 {len(suspicious)}")
    if missing:
        print("  slug 不存在:", ", ".join(missing))
    for slug, title, expect in suspicious:
        print(f"  跳过 {slug}: 标题「{title}」不含「{expect}」")
    print(f"输出 {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
