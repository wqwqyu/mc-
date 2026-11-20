"""依赖解析：按目标端（客户端/服务端）选版本，递归抓取必需前置。

解析策略
--------
1. 对每个用户选中的项目，挑选「目标游戏版本 + 加载器」下最新、且目标端可用的版本。
2. 读取该版本 version.dependencies，只对 dependency_type == "required" 的项自动抓取，
   递归到没有新节点为止（带深度 / 节点数保护，防止依赖环）。
3. 目标端不可用的前置（例如服务端包遇到纯客户端前置）直接跳过并给出警告 ——
   把纯客户端 mod 塞进服务端 mods 目录只会让服务端启动失败。
4. optional 依赖默认只记进提示，不下载；incompatible 报冲突警告；embedded 已内嵌不下载。
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from . import api

DEP_REQUIRED = "required"
DEP_OPTIONAL = "optional"
DEP_INCOMPATIBLE = "incompatible"
DEP_EMBEDDED = "embedded"


def _zh_index():
    """译名库（可选依赖，缺失时静默降级为空）。"""
    try:
        from .zh_search import ZhIndex
        return ZhIndex.get()
    except Exception:  # noqa: BLE001
        return None


def _zh_name(slug: str) -> str:
    idx = _zh_index()
    if idx is None:
        return ""
    return idx.name_of(slug) or ""


@dataclass
class ResolvedItem:
    project_id: str
    slug: str
    title: str
    version_id: str
    version_number: str
    filename: str
    url: str
    size: int
    sha512: str
    depth: int = 0
    is_root: bool = True
    optional: bool = False
    side_ok: bool = True
    required_by: list[str] = field(default_factory=list)
    error: str = ""
    zh: str = ""

    @property
    def kind(self) -> str:
        if self.is_root:
            return "本体"
        if self.optional:
            return "可选前置"
        return "前置依赖"

    @property
    def size_text(self) -> str:
        n = float(self.size or 0)
        for unit in ("B", "KB", "MB", "GB"):
            if n < 1024 or unit == "GB":
                return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
            n /= 1024
        return f"{n:.1f} GB"


@dataclass
class Resolution:
    items: list[ResolvedItem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    game_version: str = ""
    loader: str = ""
    side: str = ""

    @property
    def total_size(self) -> int:
        return sum(i.size or 0 for i in self.items if not i.error)


class Resolver:
    def __init__(self, game_version: str, loader: str, side: str, include_optional: bool = False,
                 max_nodes: int = 250, max_depth: int = 12, on_log=None):
        self.game_version = game_version
        self.loader = loader
        self.side = side
        self.include_optional = include_optional
        self.max_nodes = max_nodes
        self.max_depth = max_depth
        self.on_log = on_log or (lambda *_a, **_k: None)
        self._proj_cache: dict[str, dict] = {}
        self._ver_cache: dict[str, tuple[list[dict], str]] = {}
        self.loaders_ok = (self.loader,) + tuple(api.LOADER_COMPAT.get(self.loader, ()))

    # ------------------------------------------------------------ 内部工具

    def _project(self, project_id: str) -> dict | None:
        if project_id in self._proj_cache:
            return self._proj_cache[project_id]
        try:
            p = api.get_project(project_id)
        except api.NotFound:
            return None
        except api.ApiError as e:
            self.on_log(f"读取项目 {project_id} 失败: {e}")
            return None
        self._proj_cache[project_id] = p
        return p

    def _versions(self, project_id: str) -> tuple[list[dict], str]:
        """取版本列表，返回 (版本列表, 实际使用的加载器)。

        先按目标加载器查，查不到时按 LOADER_COMPAT 逐级回退（如 Quilt → Fabric）。
        """
        if project_id in self._ver_cache:
            return self._ver_cache[project_id]
        found: list[dict] = []
        used = self.loader
        for ld in self.loaders_ok:
            try:
                vs = api.get_versions(project_id, self.game_version, ld)
            except api.ApiError as e:
                self.on_log(f"读取版本 {project_id} 失败: {e}")
                vs = []
            if vs:
                found, used = vs, ld
                break
        self._ver_cache[project_id] = (found, used)
        return found, used

    @staticmethod
    def _newest(versions: list[dict], side: str, project: dict | None) -> tuple[dict | None, bool]:
        """从版本列表里挑最新版本，返回 (version, 该端是否可用)。

        优先挑目标端可用的版本；若一个都没有，退回最新版本并把 side_ok 标 False。
        """
        if not versions:
            return None, False
        ordered = sorted(versions, key=lambda v: v.get("date_published") or "", reverse=True)
        for v in ordered:
            if api.supports_side(v, side, project):
                return v, True
        return ordered[0], False

    def _pick(self, project: dict | None, project_id: str) -> tuple[dict | None, bool]:
        """按 目标游戏版本 + 加载器 + 目标端 挑一个版本。"""
        project = project or self._project(project_id)
        if project is None:
            return None, False
        vs, used = self._versions(project_id)
        if not vs:
            return None, False
        if used != self.loader:
            self.on_log(f"  「{project.get('title')}」没有 {self.loader} 版，使用 {used} 版（兼容）")
        return self._newest(vs, self.side, project)

    @staticmethod
    def _entry(project: dict) -> dict:
        pid = project.get("id") or project.get("project_id")
        return {
            "project_id": pid,
            "slug": project.get("slug") or pid,
            "title": project.get("title") or project.get("slug") or pid,
        }

    def _make_item(self, project: dict, version: dict, depth: int, is_root: bool,
                   optional: bool, side_ok: bool, required_by: list[str]) -> ResolvedItem | None:
        f = api.primary_file(version)
        if not f:
            return None
        e = self._entry(project)
        return ResolvedItem(
            project_id=e["project_id"],
            slug=e["slug"],
            title=e["title"],
            version_id=version["id"],
            version_number=version.get("version_number") or "",
            filename=f.get("filename") or "download.jar",
            url=f.get("url") or "",
            size=int(f.get("size") or 0),
            sha512=(f.get("hashes") or {}).get("sha512") or "",
            depth=depth,
            is_root=is_root,
            optional=optional,
            side_ok=side_ok,
            required_by=required_by,
            zh=_zh_name(e["slug"]),
        )

    # ------------------------------------------------------------ 主流程

    def resolve(self, roots: list[dict]) -> Resolution:
        """roots: 项目字典列表（搜索命中项 / 项目详情均可）。"""
        res = Resolution(game_version=self.game_version, loader=self.loader, side=self.side)
        side_label = "客户端" if self.side == api.SIDE_CLIENT else "服务端"
        queue: deque[tuple[dict, dict, int, list[str]]] = deque()
        seen: dict[str, ResolvedItem] = {}
        optional_seen: set[str] = set()

        # 1) 根项目先各自定位版本
        for proj in roots:
            e = self._entry(proj)
            if not e["project_id"]:
                continue
            data = self._proj_cache.setdefault(e["project_id"], proj)
            version, side_ok = self._pick(data, e["project_id"])
            if version is None:
                res.warnings.append(
                    f"「{e['title']}」在 {self.game_version} / {self.loader} 下没有可用版本，已跳过。"
                )
                continue
            if not side_ok:
                res.warnings.append(
                    f"「{e['title']}」不支持{side_label}（该版本仅另一端可用），已标记为不下载。"
                )
            item = self._make_item(data, version, 0, True, False, side_ok, [])
            if item is None:
                res.warnings.append(f"「{e['title']}」的版本没有可下载文件，已跳过。")
                continue
            seen[item.project_id] = item
            queue.append((data, version, 0, [item.title]))

        # 2) 广度优先展开必需依赖
        expanded: set[str] = set()
        while queue and len(seen) < self.max_nodes:
            project, version, depth, chain = queue.popleft()
            pid = self._entry(project)["project_id"]
            if pid in expanded:
                continue
            expanded.add(pid)
            if depth >= self.max_depth:
                res.warnings.append(f"依赖层级超过 {self.max_depth} 层，停止展开：{' → '.join(chain)}")
                continue

            deps = version.get("dependencies") or []
            if deps:
                self.on_log(f"  解析 {self._entry(project)['title']} 的 {len(deps)} 个依赖…")

            for dep in deps:
                dtype = (dep.get("dependency_type") or "").lower()
                dep_pid = dep.get("project_id")
                dep_vid = dep.get("version_id")

                # 只给 version_id 的情况，反查 project_id
                if not dep_pid and dep_vid:
                    try:
                        dv = api.get_version(dep_vid)
                        dep_pid = dv.get("project_id")
                    except api.ApiError:
                        dep_pid = None
                if not dep_pid:
                    continue

                if dtype == DEP_EMBEDDED:
                    continue  # 已内嵌，无需下载
                if dtype == DEP_INCOMPATIBLE:
                    p = self._project(dep_pid)
                    name = p.get("title") if p else dep_pid
                    res.warnings.append(f"「{self._entry(project)['title']}」声明与「{name}」不兼容，注意冲突。")
                    continue

                is_opt = dtype == DEP_OPTIONAL
                if dtype not in (DEP_REQUIRED, DEP_OPTIONAL):
                    continue
                if is_opt and not self.include_optional:
                    if dep_pid not in optional_seen:
                        optional_seen.add(dep_pid)
                        p = self._project(dep_pid)
                        if p:
                            res.warnings.append(
                                f"可选前置未下载：「{p.get('title')}」（{self._entry(project)['title']} 建议安装）"
                            )
                    continue

                if dep_pid in seen:
                    item = seen[dep_pid]
                    if project.get("title") not in item.required_by:
                        item.required_by.append(self._entry(project)["title"])
                    continue
                if len(seen) >= self.max_nodes:
                    res.warnings.append(f"依赖数量达到上限 {self.max_nodes}，停止展开。")
                    break

                dep_project = self._project(dep_pid)
                if dep_project is None:
                    res.warnings.append(f"依赖项目 {dep_pid} 读取失败，未能自动下载。")
                    continue
                dep_title = self._entry(dep_project)["title"]

                # 指定了精确版本时先校验适配性：不少老模组把依赖钉在旧版本上（例如
                # 1.21.1 的模组却指定了仅适配 1.20.1 的库），盲信会装进错版本并丢整条依赖链。
                dep_version = None
                if dep_vid:
                    try:
                        cand = api.get_version(dep_vid)
                    except api.ApiError:
                        cand = None
                    if cand is not None:
                        gv_ok = self.game_version in (cand.get("game_versions") or [])
                        ld_ok = any(l in (cand.get("loaders") or []) for l in self.loaders_ok)
                        if gv_ok and ld_ok:
                            dep_version = cand
                        else:
                            self.on_log(
                                f"  「{dep_title}」指定的版本 {cand.get('version_number')} 不适配 "
                                f"{self.game_version}/{self.loader}，改选兼容版本"
                            )
                if dep_version is None:
                    dep_version, _side_ok = self._pick(dep_project, dep_pid)

                if dep_version is None:
                    res.warnings.append(
                        f"前置「{dep_title}」在 {self.game_version} / {self.loader} 下没有可用版本"
                        f"（需要它的是 {self._entry(project)['title']}）。"
                    )
                    continue

                side_ok = api.supports_side(dep_version, self.side, dep_project)
                if not side_ok:
                    if is_opt:
                        continue
                    res.warnings.append(
                        f"前置「{dep_title}」仅另一端（{'服务端' if self.side == api.SIDE_CLIENT else '客户端'}）可用，"
                        f"已跳过，不会放进 {side_label} 的 mods 目录。"
                    )
                    continue

                item = self._make_item(dep_project, dep_version, depth + 1,
                                      False, is_opt, True, [self._entry(project)["title"]])
                if item is None:
                    res.warnings.append(f"前置「{dep_title}」没有可下载文件，已跳过。")
                    continue

                seen[item.project_id] = item
                queue.append((dep_project, dep_version, depth + 1, chain + [item.title]))

        # 3) 排序：本体优先，其次按层级
        items = sorted(seen.values(), key=lambda i: (0 if i.is_root else 1, i.depth, i.title.lower()))
        res.items = items
        return res
