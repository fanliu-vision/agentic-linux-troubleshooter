from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from sessions import TroubleshootingSession


@dataclass
class WatchedLogChunk:
    source: str
    path: str
    content: str


@dataclass
class RemoteLogStat:
    inode: str
    size: int
    mtime: int


class RemoteLogWatermarkStore:
    def __init__(
        self,
        watermarks: dict[str, dict[str, Any]] | None = None,
        on_update: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self.watermarks = watermarks if watermarks is not None else {}
        self.on_update = on_update

    def get_watermark(self, path: str) -> dict[str, Any]:
        watermark = self.watermarks.get(path) or {}
        return dict(watermark)

    def update_watermark(self, path: str, watermark: dict[str, Any]) -> None:
        self.watermarks[path] = dict(watermark)
        if self.on_update is not None:
            self.on_update(path, dict(watermark))


class LocalLogWatcher:
    """
    Incremental local log watcher.

    It remembers file offsets and returns only newly appended content.
    """

    def __init__(self, log_files: list[str], project_dir: str = "") -> None:
        self.log_files = log_files
        self.project_dir = Path(project_dir).expanduser().resolve() if project_dir else Path.cwd()
        self.offsets: dict[str, int] = {}

    def poll(self) -> list[WatchedLogChunk]:
        chunks: list[WatchedLogChunk] = []

        for item in self.log_files:
            path = Path(item).expanduser()
            if not path.is_absolute():
                path = self.project_dir / item

            if not path.exists() or not path.is_file():
                continue

            key = str(path)
            last_offset = self.offsets.get(key, 0)

            try:
                current_size = path.stat().st_size
                if current_size < last_offset:
                    last_offset = 0

                with path.open("r", encoding="utf-8", errors="ignore") as f:
                    f.seek(last_offset)
                    content = f.read()
                    self.offsets[key] = f.tell()

                if content.strip():
                    chunks.append(
                        WatchedLogChunk(
                            source="local_log",
                            path=str(path),
                            content=content,
                        )
                    )

            except Exception as exc:
                chunks.append(
                    WatchedLogChunk(
                        source="local_log_error",
                        path=str(path),
                        content=f"Failed to read local log {path}: {type(exc).__name__}: {exc}",
                    )
                )

        return chunks


class RemoteLogWatcher:
    """
    Remote log watcher based on SSH stat + byte ranges.

    It keeps per-file watermarks in memory by default. Callers can pass in an
    external watermark dict to persist the same shape in project state later.
    """

    def __init__(
        self,
        log_files: list[str],
        session: TroubleshootingSession,
        tail_lines: int = 200,
        max_bytes_per_poll: int = 20000,
        remote_log_watermarks: dict[str, dict[str, Any]] | None = None,
        watermark_store: RemoteLogWatermarkStore | None = None,
        watermark_enabled: bool = True,
        shadow_mode: bool = False,
    ) -> None:
        self.log_files = log_files
        self.session = session
        self.tail_lines = tail_lines
        self.max_bytes_per_poll = max(1, int(max_bytes_per_poll))
        self.watermark_store = watermark_store or RemoteLogWatermarkStore(
            remote_log_watermarks
        )
        self.remote_log_watermarks = self.watermark_store.watermarks
        self.watermark_enabled = bool(watermark_enabled)
        self.shadow_mode = bool(shadow_mode)
        self.last_poll_metrics: dict[str, Any] = self._empty_poll_metrics()
        self.last_poll_notices: list[dict[str, Any]] = []

    def poll(self) -> list[WatchedLogChunk]:
        self._reset_poll_observability()
        chunks: list[WatchedLogChunk] = []

        for path in self.log_files:
            if not self.session.remote_profile:
                chunks.append(
                    WatchedLogChunk(
                        source="remote_log_error",
                        path=path,
                        content="Remote profile is not configured.",
                    )
                )
                continue

            chunks.extend(self._poll_path(path))

        self.last_poll_metrics = self._poll_metrics_snapshot()
        return chunks

    def _poll_path(self, path: str) -> list[WatchedLogChunk]:
        if self.shadow_mode:
            self._poll_path_watermark(path, emit_chunks=False)
            return self._tail_only(path, strategy="tail_shadow_output")

        if not self.watermark_enabled:
            return self._tail_only(path, strategy="tail_disabled")

        return self._poll_path_watermark(path, emit_chunks=True)

    def _poll_path_watermark(
        self,
        path: str,
        emit_chunks: bool,
    ) -> list[WatchedLogChunk]:
        profile = self.session.remote_profile
        executor = self.session.remote_executor

        stat_result = executor.stat_remote_log(profile, remote_path=path)
        stat = self._parse_stat(stat_result.stdout) if stat_result.return_code == 0 else None

        if stat is None:
            self._record_watermark_error(
                path=path,
                reason="stat_failed",
                result=stat_result,
            )
            return self._tail_fallback(
                path=path,
                reason="stat_failed",
                advance_watermark=False,
                emit_chunks=emit_chunks,
            )

        watermark = self.watermark_store.get_watermark(path)

        if not watermark:
            return self._tail_fallback(
                path=path,
                reason="tail_bootstrap",
                advance_watermark=True,
                stat=stat,
                strategy="tail_bootstrap",
                emit_chunks=emit_chunks,
            )

        old_inode = str(watermark.get("inode", ""))
        old_offset = self._watermark_offset(watermark)

        if old_inode == stat.inode and stat.size >= old_offset:
            return self._read_incremental(path, stat, old_offset, emit_chunks=emit_chunks)

        if old_inode == stat.inode and stat.size < old_offset:
            return self._read_after_reset(
                path=path,
                stat=stat,
                fallback_reason="size_decreased",
                emit_chunks=emit_chunks,
            )

        return self._read_after_reset(
            path=path,
            stat=stat,
            fallback_reason="inode_changed",
            emit_chunks=emit_chunks,
        )

    def _read_incremental(
        self,
        path: str,
        stat: RemoteLogStat,
        offset: int,
        emit_chunks: bool,
    ) -> list[WatchedLogChunk]:
        delta = stat.size - offset
        if delta <= 0:
            self._record_strategy("incremental")
            self._update_watermark(
                path=path,
                stat=stat,
                offset=stat.size,
                strategy="incremental",
            )
            return []

        if delta > self.max_bytes_per_poll:
            return self._tail_fallback(
                path=path,
                reason="delta_exceeds_max_bytes_per_poll",
                advance_watermark=True,
                stat=stat,
                strategy="tail_fallback",
                skipped_bytes=delta,
                emit_chunks=emit_chunks,
            )

        return self._read_range(
            path=path,
            stat=stat,
            offset=offset,
            max_bytes=delta,
            strategy="incremental",
            emit_chunks=emit_chunks,
        )

    def _read_after_reset(
        self,
        path: str,
        stat: RemoteLogStat,
        fallback_reason: str,
        emit_chunks: bool,
    ) -> list[WatchedLogChunk]:
        if stat.size <= 0:
            self._record_strategy("rotation_reset")
            self._record_notice(
                kind="rotation_reset",
                path=path,
                reason=fallback_reason,
                strategy="rotation_reset",
            )
            self._update_watermark(
                path=path,
                stat=stat,
                offset=0,
                strategy="rotation_reset",
                fallback_reason=fallback_reason,
            )
            return []

        if stat.size > self.max_bytes_per_poll:
            return self._tail_fallback(
                path=path,
                reason=fallback_reason,
                advance_watermark=True,
                stat=stat,
                strategy="tail_fallback",
                skipped_bytes=stat.size,
                emit_chunks=emit_chunks,
            )

        return self._read_range(
            path=path,
            stat=stat,
            offset=0,
            max_bytes=stat.size,
            strategy="rotation_reset",
            fallback_reason=fallback_reason,
            emit_chunks=emit_chunks,
        )

    def _read_range(
        self,
        path: str,
        stat: RemoteLogStat,
        offset: int,
        max_bytes: int,
        strategy: str,
        fallback_reason: str = "",
        emit_chunks: bool = True,
    ) -> list[WatchedLogChunk]:
        result = self.session.remote_executor.read_remote_log_range(
            self.session.remote_profile,
            remote_path=path,
            offset=offset,
            max_bytes=max_bytes,
        )

        if result.return_code == 0:
            self._record_strategy(strategy)
            self._record_bytes_read(result.stdout)
            if strategy == "rotation_reset":
                self._record_notice(
                    kind="rotation_reset",
                    path=path,
                    reason=fallback_reason,
                    strategy=strategy,
                )
            self._update_watermark(
                path=path,
                stat=stat,
                offset=stat.size,
                strategy=strategy,
                fallback_reason=fallback_reason,
            )
            if emit_chunks and result.stdout.strip():
                return [
                    WatchedLogChunk(
                        source="remote_log",
                        path=path,
                        content=result.to_evidence_text(),
                    )
                ]
            return []

        self._record_watermark_error(
            path=path,
            reason="range_read_failed",
            result=result,
        )
        return self._tail_fallback(
            path=path,
            reason="range_read_failed",
            advance_watermark=False,
            emit_chunks=emit_chunks,
        )

    def _tail_fallback(
        self,
        path: str,
        reason: str,
        advance_watermark: bool,
        stat: RemoteLogStat | None = None,
        strategy: str = "tail_fallback",
        skipped_bytes: int = 0,
        emit_chunks: bool = True,
    ) -> list[WatchedLogChunk]:
        self._record_strategy(strategy)
        if strategy == "tail_fallback":
            self._record_notice(
                kind="tail_fallback",
                path=path,
                reason=reason,
                strategy=strategy,
            )

        if not emit_chunks:
            if advance_watermark and stat is not None:
                self._update_watermark(
                    path=path,
                    stat=stat,
                    offset=stat.size,
                    strategy=strategy,
                    fallback_reason="" if strategy == "tail_bootstrap" else reason,
                    skipped_bytes=skipped_bytes,
                )
            return []

        result = self.session.remote_executor.read_remote_log_tail(
            self.session.remote_profile,
            remote_path=path,
            lines=self.tail_lines,
        )

        if result.return_code == 0:
            self._record_bytes_read(result.stdout)
            if advance_watermark and stat is not None:
                self._update_watermark(
                    path=path,
                    stat=stat,
                    offset=stat.size,
                    strategy=strategy,
                    fallback_reason="" if strategy == "tail_bootstrap" else reason,
                    skipped_bytes=skipped_bytes,
                )
            if result.stdout.strip():
                return [
                    WatchedLogChunk(
                        source="remote_log",
                        path=path,
                        content=result.to_evidence_text(),
                    )
                ]
            return []

        if not result.allowed or result.return_code not in {0, None}:
            return [
                WatchedLogChunk(
                    source="remote_log_error",
                    path=path,
                    content=result.to_evidence_text(),
                )
            ]

        return []

    def _tail_only(self, path: str, strategy: str) -> list[WatchedLogChunk]:
        self._record_strategy(strategy)
        result = self.session.remote_executor.read_remote_log_tail(
            self.session.remote_profile,
            remote_path=path,
            lines=self.tail_lines,
        )

        if result.return_code == 0:
            self._record_bytes_read(result.stdout)
            if result.stdout.strip():
                return [
                    WatchedLogChunk(
                        source="remote_log",
                        path=path,
                        content=result.to_evidence_text(),
                    )
                ]
            return []

        if not result.allowed or result.return_code not in {0, None}:
            return [
                WatchedLogChunk(
                    source="remote_log_error",
                    path=path,
                    content=result.to_evidence_text(),
                )
            ]

        return []

    def _update_watermark(
        self,
        path: str,
        stat: RemoteLogStat,
        offset: int,
        strategy: str,
        fallback_reason: str = "",
        skipped_bytes: int = 0,
    ) -> None:
        watermark: dict[str, Any] = {
            "inode": stat.inode,
            "size": stat.size,
            "mtime": stat.mtime,
            "offset": offset,
            "last_read_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "last_strategy": strategy,
            "fallback_reason": fallback_reason,
        }
        if skipped_bytes:
            watermark["skipped_bytes"] = skipped_bytes
        self.watermark_store.update_watermark(path, watermark)

    def _reset_poll_observability(self) -> None:
        self._strategy_counts: dict[str, int] = {}
        self._fallback_count = 0
        self._bytes_read = 0
        self._watermark_errors: list[dict[str, Any]] = []
        self.last_poll_notices = []
        self.last_poll_metrics = self._empty_poll_metrics()

    @staticmethod
    def _empty_poll_metrics() -> dict[str, Any]:
        return {
            "remote_log_strategy_counts": {},
            "remote_log_fallback_count": 0,
            "remote_log_bytes_read": 0,
            "remote_log_watermark_errors": [],
        }

    def _poll_metrics_snapshot(self) -> dict[str, Any]:
        return {
            "remote_log_strategy_counts": dict(self._strategy_counts),
            "remote_log_fallback_count": int(self._fallback_count),
            "remote_log_bytes_read": int(self._bytes_read),
            "remote_log_watermark_errors": list(self._watermark_errors),
        }

    def _record_strategy(self, strategy: str) -> None:
        self._strategy_counts[strategy] = self._strategy_counts.get(strategy, 0) + 1
        if strategy == "tail_fallback":
            self._fallback_count += 1

    def _record_bytes_read(self, text: str) -> None:
        self._bytes_read += len(text.encode("utf-8", errors="ignore"))

    def _record_watermark_error(
        self,
        path: str,
        reason: str,
        result: Any,
    ) -> None:
        self._watermark_errors.append(
            {
                "path": path,
                "reason": reason,
                "return_code": getattr(result, "return_code", None),
                "command": getattr(result, "command", ""),
            }
        )

    def _record_notice(
        self,
        kind: str,
        path: str,
        reason: str,
        strategy: str,
    ) -> None:
        self.last_poll_notices.append(
            {
                "kind": kind,
                "path": path,
                "reason": reason,
                "strategy": strategy,
            }
        )

    @staticmethod
    def _parse_stat(stdout: str) -> RemoteLogStat | None:
        parts = stdout.strip().split()
        if len(parts) != 3:
            return None

        inode, size_text, mtime_text = parts
        try:
            return RemoteLogStat(
                inode=str(inode),
                size=int(size_text),
                mtime=int(mtime_text),
            )
        except ValueError:
            return None

    @staticmethod
    def _watermark_offset(watermark: dict[str, Any]) -> int:
        try:
            return max(0, int(watermark.get("offset", 0)))
        except (TypeError, ValueError):
            return 0
