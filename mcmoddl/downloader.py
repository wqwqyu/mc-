"""下载：文件级并发 + 单文件分块多线程（HTTP Range）、SHA512 校验、已存在跳过。

分块策略借鉴 PCL2 的 `ModNet.vb` 多线程下载引擎：
  · 先探测服务器是否支持 Range（返回 206 才算），不支持就老实单线程 —— 硬上会拿到
    重复内容或 200 全量响应，PCL 里对应 `NotSupportRange` 的降级分支；
  · 碎片要够大才切（PCL 的 `FilePieceLimit`），小文件切块的开销比收益大；
  · 任何一块失败就整文件回退单线程重下，而不是拼出一个损坏的 jar（PCL 用链表管理
    线程区间并在失败点续接，这里用更简单的"整块重试 + 整体降级"达到同样效果）。

两个层级并发：
  concurrency   同时下载多少个文件
  file_threads  单个大文件切成几块并行
总连接数由信号量限制，避免 20 个文件 × 4 块把家用宽带打死。
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from . import api

UA = api.USER_AGENT
_ILLEGAL = re.compile(r'[\\/:*?"<>|\r\n\t]')

MIN_SPLIT_BYTES = 4 * 1024 * 1024    # 小于 4MB 不切块
MIN_PIECE_BYTES = 1024 * 1024        # 每块至少 1MB
MAX_CONNECTIONS = 16                 # 总连接数上限
REPORT_INTERVAL = 0.15               # 进度上报节流（秒）


def safe_filename(name: str) -> str:
    name = _ILLEGAL.sub("_", (name or "").strip()).strip(" .")
    return name or "download.jar"


def sha512_of(path: str) -> str:
    h = hashlib.sha512()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class DownloadResult:
    item: object
    ok: bool
    skipped: bool = False
    path: str = ""
    message: str = ""
    pieces: int = 1

    @property
    def title(self) -> str:
        return getattr(self.item, "title", "?")


class Cancelled(Exception):
    pass


class Downloader:
    """并发下载一组 ResolvedItem。所有回调都在工作线程里触发，UI 需自行转发。"""

    def __init__(self, target_dir: str, concurrency: int = 3, file_threads: int = 4,
                 verify: bool = True, skip_unsupported: bool = True, overwrite: bool = False,
                 on_progress=None, on_log=None, on_item=None):
        self.target_dir = target_dir
        self.concurrency = max(1, int(concurrency))
        self.file_threads = max(1, int(file_threads))
        self.verify = verify
        self.skip_unsupported = skip_unsupported
        self.overwrite = overwrite
        self._on_progress = on_progress or (lambda *_a: None)
        self._on_log = on_log or (lambda *_a: None)
        self._on_item = on_item or (lambda *_a: None)
        self._cancel = threading.Event()
        self._lock = threading.Lock()
        self._done_bytes = 0
        self._last_report = 0.0
        self._conns = threading.Semaphore(
            min(self.concurrency * self.file_threads, MAX_CONNECTIONS)
        )

    def cancel(self) -> None:
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    # ------------------------------------------------------------ 进度

    def _bump(self, n: int, total: int, filename: str, force: bool = False) -> None:
        with self._lock:
            self._done_bytes += n
            done = self._done_bytes
        now = time.monotonic()
        if force or now - self._last_report >= REPORT_INTERVAL:
            self._last_report = now
            self._on_progress(min(done, total), total, filename)

    # ------------------------------------------------------------ Range 探测

    def _supports_range(self, url: str) -> bool:
        """发一个 96 字节的 Range 请求，206 才认为支持分块。"""
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Range": "bytes=0-95"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status == 206
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError):
            return False

    # ------------------------------------------------------------ 单块下载

    def _fetch_piece(self, url: str, path: str, start: int, end: int,
                     total: int, filename: str, label: str) -> None:
        last_err: Exception | None = None
        for attempt in range(3):
            if self._cancel.is_set():
                raise Cancelled()
            # 重试时从已写入的位置续传，避免重复写同一段
            written = os.path.getsize(path) if os.path.exists(path) else 0
            try:
                headers = {"User-Agent": UA, "Range": f"bytes={start + written}-{end}"}
                req = urllib.request.Request(url, headers=headers)
                with self._conns:
                    with urllib.request.urlopen(req, timeout=90) as resp:
                        if resp.status != 206:
                            raise RuntimeError(f"服务器未按 Range 返回（{resp.status}）")
                        with open(path, "ab") as f:
                            while True:
                                if self._cancel.is_set():
                                    raise Cancelled()
                                chunk = resp.read(1 << 18)
                                if not chunk:
                                    break
                                f.write(chunk)
                                written += len(chunk)
                                self._bump(len(chunk), total, filename)
                got = os.path.getsize(path) if os.path.exists(path) else 0
                if got == end - start + 1:
                    return
                # 少读了，从断点再要一次
                raise RuntimeError(f"{label} 数据不完整（{got}/{end - start + 1}）")
            except Cancelled:
                raise
            except Exception as exc:  # noqa: BLE001 - 网络抖动重试
                last_err = exc
                if attempt < 2:
                    time.sleep(0.8 * (attempt + 1))
        raise RuntimeError(f"{label} 下载失败: {last_err}")

    # ------------------------------------------------------------ 分块下载

    def _download_split(self, item, tmp: str, total: int) -> int:
        """分块并行下载。成功返回块数，失败抛异常（由上层降级）。"""
        size = int(item.size or 0)
        if size < MIN_SPLIT_BYTES or self.file_threads < 2:
            return 0
        pieces = min(self.file_threads, max(2, size // MIN_PIECE_BYTES))
        if pieces < 2:
            return 0
        if not self._supports_range(item.url):
            self._on_log(f"{item.filename}: 服务器不支持 Range，改用单线程")
            return 0

        piece_size = size // pieces
        parts: list[str] = []
        threads: list[threading.Thread] = []
        errors: list[Exception] = []
        err_lock = threading.Lock()

        def run(idx: int, start: int, end: int) -> None:
            part = f"{tmp}.p{idx}"
            try:
                self._fetch_piece(item.url, part, start, end, total,
                                  item.filename, f"分块 {idx + 1}/{pieces}")
            except Cancelled:
                return
            except Exception as exc:  # noqa: BLE001
                with err_lock:
                    errors.append(exc)

        for i in range(pieces):
            start = i * piece_size
            end = size - 1 if i == pieces - 1 else (i + 1) * piece_size - 1
            part = f"{tmp}.p{i}"
            parts.append(part)
            open(part, "wb").close()
            t = threading.Thread(target=run, args=(i, start, end), daemon=True)
            t.start()
            threads.append(t)
        for t in threads:
            t.join()

        if self._cancel.is_set():
            raise Cancelled()
        if errors:
            for p in parts:
                if os.path.exists(p):
                    os.remove(p)
            raise RuntimeError(str(errors[0]))

        with open(tmp, "wb") as out:
            for p in parts:
                with open(p, "rb") as f:
                    shutil.copyfileobj(f, out, 1 << 20)
                os.remove(p)
        if os.path.getsize(tmp) != size:
            raise RuntimeError(f"合并后大小不符（{os.path.getsize(tmp)} != {size}）")
        return pieces

    # ------------------------------------------------------------ 单线程下载

    def _download_plain(self, item, tmp: str, total: int) -> None:
        req = urllib.request.Request(item.url, headers={"User-Agent": UA})
        with self._conns:
            with urllib.request.urlopen(req, timeout=90) as resp, open(tmp, "wb") as f:
                while True:
                    if self._cancel.is_set():
                        raise Cancelled()
                    chunk = resp.read(1 << 18)
                    if not chunk:
                        break
                    f.write(chunk)
                    self._bump(len(chunk), total, item.filename)

    # ------------------------------------------------------------ 单个文件

    def _download_one(self, item, total: int) -> DownloadResult:
        filename = safe_filename(item.filename)
        dest = os.path.join(self.target_dir, filename)
        tmp = dest + ".part"

        def cleanup() -> None:
            for p in [tmp] + [f"{tmp}.p{i}" for i in range(8)]:
                if os.path.exists(p):
                    try:
                        os.remove(p)
                    except OSError:
                        pass

        try:
            if self._cancel.is_set():
                raise Cancelled()

            # 已存在且校验通过 —— 直接跳过
            if os.path.exists(dest) and not self.overwrite:
                if not self.verify or not item.sha512:
                    self._on_log(f"跳过（已存在）: {filename}")
                    return DownloadResult(item, True, True, dest, "已存在")
                if os.path.getsize(dest) == item.size and sha512_of(dest) == item.sha512:
                    self._on_log(f"跳过（已存在且校验通过）: {filename}")
                    self._bump(int(item.size or 0), total, filename, force=True)
                    return DownloadResult(item, True, True, dest, "已存在")

            self._on_item(item, "下载中")
            pieces = 0
            try:
                pieces = self._download_split(item, tmp, total)
            except Cancelled:
                raise
            except Exception as exc:  # noqa: BLE001 - 分块失败降级单线程
                self._on_log(f"{filename}: 分块下载失败（{exc}），改用单线程重试")
                cleanup()

            if pieces == 0:
                self._download_plain(item, tmp, total)
                pieces = 1
            else:
                self._on_log(f"{filename}: {pieces} 线程分块下载完成")

            if self.verify and item.sha512:
                got = sha512_of(tmp)
                if got != item.sha512:
                    cleanup()
                    msg = f"SHA512 校验失败: {filename}"
                    self._on_log(msg)
                    self._on_item(item, "校验失败")
                    return DownloadResult(item, False, False, "", msg, pieces)

            os.replace(tmp, dest)
            self._on_item(item, "完成")
            self._on_log(f"完成: {filename} ({item.size_text})")
            return DownloadResult(item, True, False, dest, "完成", pieces)

        except Cancelled:
            cleanup()
            self._on_item(item, "已取消")
            return DownloadResult(item, False, False, "", "已取消")
        except (urllib.error.URLError, TimeoutError, OSError, RuntimeError) as exc:
            cleanup()
            msg = f"下载失败 {filename}: {exc}"
            self._on_log(msg)
            self._on_item(item, "失败")
            return DownloadResult(item, False, False, "", str(exc))

    # ------------------------------------------------------------ 批量

    def run(self, items) -> list[DownloadResult]:
        items = list(items)
        if self.skip_unsupported:
            skipped = [i for i in items if not getattr(i, "side_ok", True)]
            items = [i for i in items if getattr(i, "side_ok", True)]
            for i in skipped:
                self._on_log(f"跳过（不支持目标端）: {i.title}")
                self._on_item(i, "端不支持")
        if not items:
            return []

        os.makedirs(self.target_dir, exist_ok=True)
        total = sum(max(0, int(i.size or 0)) for i in items)
        with self._lock:
            self._done_bytes = 0
        self._on_progress(0, total, "")
        threads_note = (f"{self.concurrency} 文件并发"
                        + (f" / 大文件 {self.file_threads} 线程分块" if self.file_threads > 1 else ""))
        self._on_log(f"开始下载 {len(items)} 个文件到 {self.target_dir}"
                     f"（共 {total / 1048576:.1f} MB，{threads_note}）")

        results: list[DownloadResult] = []
        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            futures = {pool.submit(self._download_one, i, total): i for i in items}
            for fut in as_completed(futures):
                try:
                    results.append(fut.result())
                except Exception as exc:  # noqa: BLE001 - 单文件异常不拖垮整批
                    self._on_log(f"异常: {exc}")
                    results.append(DownloadResult(futures[fut], False, False, "", str(exc)))

        if total:
            self._on_progress(total, total, "")
        ok = sum(1 for r in results if r.ok and not r.skipped)
        skip = sum(1 for r in results if r.skipped)
        bad = len(results) - ok - skip
        self._on_log(f"结束：成功 {ok}，跳过 {skip}，失败 {bad}")
        return results


def ensure_dir(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    return path


def free_space(path: str) -> int:
    try:
        return shutil.disk_usage(os.path.abspath(path)).free
    except OSError:
        return -1
