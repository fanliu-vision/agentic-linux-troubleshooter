from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sessions import TroubleshootingSession


@dataclass
class WatchedLogChunk:
    source: str
    path: str
    content: str


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
    Remote log watcher based on SSH tail.

    It polls the last N lines and relies on event fingerprint deduplication.
    """

    def __init__(
        self,
        log_files: list[str],
        session: TroubleshootingSession,
        tail_lines: int = 200,
    ) -> None:
        self.log_files = log_files
        self.session = session
        self.tail_lines = tail_lines

    def poll(self) -> list[WatchedLogChunk]:
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

            result = self.session.remote_executor.read_remote_log_tail(
                self.session.remote_profile,
                remote_path=path,
                lines=self.tail_lines,
            )

            evidence = result.to_evidence_text()

            if result.return_code == 0 and result.stdout.strip():
                chunks.append(
                    WatchedLogChunk(
                        source="remote_log",
                        path=path,
                        content=evidence,
                    )
                )
            elif result.return_code not in {0, None}:
                chunks.append(
                    WatchedLogChunk(
                        source="remote_log_error",
                        path=path,
                        content=evidence,
                    )
                )

        return chunks