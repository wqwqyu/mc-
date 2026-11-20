"""中文名搜索：借助 PCL2 的社区译名库把中文关键词映射到 Modrinth slug。

数据来自 PCL2 (Meloong-Git/PCL) 的 `WikiEntries.txt`，译名源自 MC 百科 (mcmod.cn)。
`tools/build_zh_names.py` 把它整理成 `data/zh_names.json`（约 5300 个模组）。

搜索策略（比 PCL 简化，因为我们只需要 Modrinth）：
    归一化（繁转简 / 全角转半角 / 去标点空格）
      -> 译名主名精确匹配 > 别名精确 > slug 精确 > 主名前缀 > 主名包含 > 英文名包含
      -> 得分相同时名字越短越靠前（"机械动力" 排在 "机械动力：航空发动机" 之前）
"""
from __future__ import annotations

import json
import os
import re
import threading
import unicodedata

CJK_RE = re.compile(r"[\u4e00-\u9fff]")

_PUNCT = re.compile(r"[\s\-_/·・:：,，.。()（）\[\]【】!！?？'\"“”‘’*+&@#]")

_T2S_PAIRS = (
    "鈉=钠 機=机 動=动 業=业 產=产 礦=矿 電=电 鐵=铁 劍=剑 龍=龙 馬=马 鳥=鸟 魚=鱼 蟲=虫 "
    "風=风 雲=云 車=车 輪=轮 飛=飞 關=关 開=开 門=门 場=场 區=区 圖=图 書=书 網=网 線=线 "
    "級=级 種=种 樹=树 點=点 無=无 雙=双 單=单 實=实 體=体 驗=验 據=据 庫=库 構=构 築=筑 "
    "裝=装 備=备 護=护 飾=饰 藥=药 補=补 強=强 敵=敌 戰=战 鬥=斗 擊=击 傷=伤 復=复 蘇=苏 "
    "覺=觉 學=学 術=术 藝=艺 靈=灵 魂=魂 農=农 養=养 釣=钓 鍛=锻 鍊=炼 燒=烧 煉=炼 鋼=钢 "
    "銅=铜 銀=银 鉛=铅 鋅=锌 鎢=钨 鎳=镍 鈦=钛 鋁=铝 矽=硅 鹽=盐 鹼=碱 溫=温 濕=湿 壓=压 "
    "縮=缩 擴=扩 張=张 積=积 載=载 貨=货 運=运 輸=输 倉=仓 儲=储 發=发 終=终 極=极 隨=随 "
    "選=选 擇=择 設=设 計=计 試=试 證=证 紀=纪 錄=录 記=记 憶=忆 檔=档 夾=夹 層=层 階=阶 "
    "別=别 類=类 態=态 勢=势 變=变 轉=转 換=换 製=制 銷=销 毀=毁 創=创 險=险 導=导 傳=传 "
    "遞=递 檻=槛 壘=垒 氣=气 財=财 錢=钱 買=买 賣=卖 價=价 獲=获 遺=遗 進=进 應=应 歡=欢 "
    "樂=乐 顏=颜 繪=绘 畫=画 聲=声 語=语 對=对 話=话 訊=讯 錯=错 誤=误 確=确 結=结 繼=继 "
    "續=续 暫=暂 帶=带 幫=帮 協=协 團=团 隊=队 組=组 員=员 權=权 醫=医 療=疗 飲=饮 織=织 "
    "樓=楼 牆=墙 磚=砖 標=标 籤=签 號=号 這=这 個=个 們=们 麼=么 為=为 與=与 時=时 現=现 "
    "過=过 來=来 後=后 當=当 說=说 請=请 誰=谁 問=问 間=间 題=题 麼=么 顯=显 處=处 麼=么 "
)
T2S_MAP = {ord(k): v for k, v in (pair.split("=") for pair in _T2S_PAIRS.split() if "=" in pair)}


def normalize(text: str) -> str:
    """归一化：NFKC（全角→半角）、繁转简、小写、去标点空格。"""
    if not text:
        return ""
    s = unicodedata.normalize("NFKC", text).strip().lower()
    s = s.translate(T2S_MAP)
    return _PUNCT.sub("", s)


def has_cjk(text: str) -> bool:
    return bool(CJK_RE.search(text or ""))


class ZhIndex:
    """中文译名索引（单例，懒加载）。"""

    _instance: "ZhIndex | None" = None
    _lock = threading.Lock()

    @classmethod
    def get(cls) -> "ZhIndex":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def __init__(self, path: str | None = None) -> None:
        if path is None:
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "zh_names.json")
        self.path = path
        self.entries: list[dict] = []
        self.by_slug: dict[str, str] = {}
        self._rows: list[tuple[str, str, str, str, str]] = []   # (主名, 别名, 全名, slug, slug归一)
        self._load()

    def _load(self) -> None:
        entries: list[dict] = []
        for fname in ("zh_names.json", "zh_supplement.json"):
            path = os.path.join(os.path.dirname(self.path), fname)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    entries.extend(json.load(f).get("entries") or [])
            except (OSError, ValueError):
                continue
        # 主库优先，补充表只填空缺
        merged: dict[str, dict] = {}
        for e in entries:
            slug = e.get("slug") or ""
            if slug and slug not in merged:
                merged[slug] = e
        self.entries = list(merged.values())
        rows = []
        for e in self.entries:
            slug = e.get("slug") or ""
            zh = e.get("zh") or ""
            if not slug or not zh:
                continue
            self.by_slug[slug] = zh
            # "钠 (Sodium)" -> 主名 "钠"；"资源包惰性重载/抑验怠载 (...)" -> 别名
            head = zh.split(" (")[0]
            parts = [p.strip() for p in head.split("/") if p.strip()]
            main = normalize(parts[0]) if parts else ""
            alias = normalize(parts[1]) if len(parts) > 1 else ""
            rows.append((main, alias, normalize(zh), slug, normalize(slug)))
        self._rows = rows

    def name_of(self, slug: str) -> str | None:
        return self.by_slug.get(slug)

    def lookup(self, query: str, limit: int = 15) -> list[str]:
        """按相关度返回 slug 列表（去重，得分降序）。"""
        q = normalize(query)
        if not q or not self._rows:
            return []
        scored: dict[str, int] = {}
        for main, alias, full, slug, slug_n in self._rows:
            score = 0
            if main == q or alias == q:
                score = 1000
            elif slug_n == q:
                score = 900
            elif main.startswith(q):
                score = 700 - min(len(main), 60)
            elif q in main:
                score = 520 - min(len(main), 60)
            elif alias and q in alias:
                score = 460 - min(len(alias), 60)
            elif q in full:
                score = 320 - min(len(full), 80)
            if score:
                if score > scored.get(slug, -1):
                    scored[slug] = score
        ordered = sorted(scored.items(), key=lambda kv: (-kv[1], len(kv[0])))
        return [slug for slug, _ in ordered[:limit]]

    def annotate(self, hits: list[dict]) -> list[dict]:
        """给搜索结果补上 zh 字段（没有译名则不变）。"""
        for h in hits:
            if h.get("zh"):
                continue
            name = self.by_slug.get(h.get("slug") or "")
            if name:
                h["zh"] = name
        return hits
