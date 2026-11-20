"""Modrinth API 客户端 —— 纯标准库实现，无需 pip 安装任何依赖。

文档: https://docs.modrinth.com/api/
"""
from __future__ import annotations

import gzip
import json
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.modrinth.com/v2"
USER_AGENT = "mc-mod-downloader/1.0 (local desktop tool)"

# 支持的加载器（Modrinth 的 categories facet 名称）
LOADERS = ("fabric", "forge", "neoforge", "quilt", "liteloader", "modloader", "rift")
LOADER_LABEL = {
    "fabric": "Fabric",
    "forge": "Forge",
    "neoforge": "NeoForge",
    "quilt": "Quilt",
    "liteloader": "LiteLoader",
    "modloader": "ModLoader",
    "rift": "Rift",
}

# 加载器兼容回退：前者缺失时依次尝试后者（Quilt 可直接加载 Fabric 模组）
LOADER_COMPAT = {
    "quilt": ("fabric",),
}

SIDE_CLIENT = "client"
SIDE_SERVER = "server"


class ApiError(Exception):
    """接口调用失败。"""


class NotFound(ApiError):
    """项目或版本不存在。"""


def _request(url: str, retries: int = 4, timeout: int = 30):
    """带重试 / 429 限流处理的 GET JSON。"""
    last = None
    for attempt in range(retries):
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                if (resp.headers.get("Content-Encoding") or "").lower() == "gzip":
                    raw = gzip.decompress(raw)
                return json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise NotFound(f"未找到: {url}") from e
            if e.code == 429:
                # Modrinth 会给出应等待的秒数
                wait = 5
                try:
                    wait = int(e.headers.get("X-Ratelimit-Reset") or 5)
                except (TypeError, ValueError):
                    pass
                time.sleep(min(max(wait, 1), 30))
                last = e
                continue
            if 500 <= e.code < 600:
                time.sleep(1.5 * (attempt + 1))
                last = e
                continue
            raise ApiError(f"HTTP {e.code}: {e.reason}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = e
            if attempt == retries - 1:
                break
            time.sleep(1.5 * (attempt + 1))
    raise ApiError(f"请求失败（{url}）: {last}")


def _q(params: dict) -> str:
    clean = {k: v for k, v in params.items() if v not in (None, "", [])}
    return urllib.parse.urlencode(clean)


# ---------------------------------------------------------------- 搜索 / 项目

def search(
    query: str = "",
    game_version: str = "",
    loader: str = "",
    project_type: str = "mod",
    limit: int = 20,
    offset: int = 0,
    index: str = "relevance",
    use_zh: bool = True,
) -> dict:
    """搜索项目。返回 {hits: [...], total_hits, offset, limit}。

    中文关键词会先查本地译名库（PCL2 的社区译名，来自 MC 百科），把中文名命中的项目
    置顶，再合并 Modrinth 自己的搜索结果 —— 只靠 API 是搜不到 "钠" -> Sodium 的。
    """
    result = search_remote(query, game_version, loader, project_type, limit, offset, index)
    hits = result.get("hits") or []
    try:
        from . import zh_search
        zh = zh_search.ZhIndex.get()
    except Exception:  # noqa: BLE001 - 译名库缺失不应影响搜索
        zh = None
    if zh is None:
        return result

    zh.annotate(hits)
    if use_zh and offset == 0 and project_type == "mod" and query and zh_search.has_cjk(query):
        extra = _zh_extra_hits(zh.lookup(query, limit=max(limit, 15)), hits,
                               game_version, loader, limit)
        if extra:
            result["hits"] = (extra + hits)[:limit]
            result["total_hits"] = int(result.get("total_hits") or 0) + len(extra)
            result["zh_matched"] = len(extra)
    return result


def search_remote(
    query: str = "",
    game_version: str = "",
    loader: str = "",
    project_type: str = "mod",
    limit: int = 20,
    offset: int = 0,
    index: str = "relevance",
) -> dict:
    """纯 Modrinth API 搜索（不做中文补强）。"""
    facets = []
    if project_type and project_type != "any":
        facets.append([f"project_type:{project_type}"])
    if loader:
        facets.append([f"categories:{loader}"])
    if game_version:
        facets.append([f"versions:{game_version}"])
    params = {
        "query": query,
        "limit": max(1, min(int(limit), 100)),
        "offset": max(0, int(offset)),
        "index": index,
    }
    if facets:
        params["facets"] = json.dumps(facets, separators=(",", ":"))
    return _request(f"{API}/search?{_q(params)}")


def _match_filters(project: dict, game_version: str, loader: str) -> bool:
    """项目是否适配目标游戏版本与加载器（含 Quilt→Fabric 回退）。"""
    if loader:
        allowed = (loader,) + tuple(LOADER_COMPAT.get(loader, ()))
        loaders = project.get("loaders") or []
        if not any(l in loaders for l in allowed):
            return False
    if game_version and game_version not in (project.get("game_versions") or []):
        return False
    return True


def _zh_extra_hits(slugs: list[str], existing: list[dict], game_version: str,
                   loader: str, limit: int) -> list[dict]:
    """把译名库命中的 slug 换成搜索结果条目（带校验和排序）。"""
    from . import zh_search

    have = {h.get("slug") for h in existing}
    wanted = [s for s in slugs if s not in have][:limit]
    if not wanted:
        return []
    try:
        projects = get_projects(wanted)
    except ApiError:
        return []
    order = {s: i for i, s in enumerate(wanted)}
    projects = [p for p in projects if _match_filters(p, game_version, loader)]
    projects.sort(key=lambda p: order.get(p.get("slug") or "", 999))
    zh = zh_search.ZhIndex.get()
    out = []
    for p in projects:
        out.append({
            "project_id": p.get("id"),
            "slug": p.get("slug"),
            "title": p.get("title"),
            "description": p.get("description") or "",
            "downloads": p.get("downloads") or 0,
            "client_side": p.get("client_side"),
            "server_side": p.get("server_side"),
            "icon_url": p.get("icon_url"),
            "zh": zh.name_of(p.get("slug") or ""),
            "zh_matched": True,
        })
    return out


def get_project(project_id: str) -> dict:
    """按 slug 或 ID 取单个项目。"""
    return _request(f"{API}/project/{urllib.parse.quote(project_id)}")


def get_projects(ids) -> list[dict]:
    """批量取项目（一次最多 1000 个）。"""
    ids = [i for i in ids if i]
    if not ids:
        return []
    out = []
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        out.extend(_request(f"{API}/projects?{_q({'ids': json.dumps(chunk, separators=(',', ':'))})}"))
    return out


# ---------------------------------------------------------------- 版本

def get_versions(project_id: str, game_version: str = "", loader: str = "") -> list[dict]:
    """取项目的版本列表（新的在前；可按游戏版本和加载器过滤）。"""
    params = {}
    if game_version:
        params["game_versions"] = json.dumps([game_version], separators=(",", ":"))
    if loader:
        params["loaders"] = json.dumps([loader], separators=(",", ":"))
    suffix = f"?{_q(params)}" if params else ""
    return _request(f"{API}/project/{urllib.parse.quote(project_id)}/version{suffix}") or []


def get_version(version_id: str) -> dict:
    return _request(f"{API}/version/{urllib.parse.quote(version_id)}")


def get_game_versions() -> list[str]:
    """所有正式版游戏版本，新版本在前。"""
    tags = _request(f"{API}/tag/game_version") or []
    return [t["version"] for t in tags if t.get("version_type") == "release"]


# ---------------------------------------------------------------- 端判定

def side_of(version: dict, project: dict | None = None) -> tuple[bool, bool]:
    """返回 (客户端可用, 服务端可用)。

    version.environment: client_only / server_only / client_or_server_prefers_both 等，
    字符串里含 "client"/"server" 即代表该端可用。
    project.client_side / server_side: required / optional / unsupported，作为补充判断。
    """
    env = (version.get("environment") or "").lower()
    client_ok = "client" in env
    server_ok = "server" in env
    if project:
        if (project.get("client_side") or "") == "unsupported":
            client_ok = False
        if (project.get("server_side") or "") == "unsupported":
            server_ok = False
    return client_ok, server_ok


def supports_side(version: dict, side: str, project: dict | None = None) -> bool:
    client_ok, server_ok = side_of(version, project)
    return client_ok if side == SIDE_CLIENT else server_ok


def primary_file(version: dict) -> dict | None:
    """挑主文件（primary 优先，否则第一个）。"""
    files = version.get("files") or []
    if not files:
        return None
    for f in files:
        if f.get("primary"):
            return f
    return files[0]
