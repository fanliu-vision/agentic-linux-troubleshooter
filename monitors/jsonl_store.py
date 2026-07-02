from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

try:
    import fcntl
except ImportError:  # pragma: no cover - this project targets Linux.
    fcntl = None  # type: ignore[assignment]


def json_dumps(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


@contextmanager
def _locked(path: Path, lock_mode: int):
    lock_path = path.with_name(path.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        if fcntl is not None:
            fcntl.flock(lock_file.fileno(), lock_mode)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_mode = fcntl.LOCK_EX if fcntl is not None else 0
    with _locked(path, lock_mode):
        with path.open("a", encoding="utf-8") as f:
            f.write(json_dumps(record) + "\n")
            f.flush()
            os.fsync(f.fileno())


def read_jsonl(
    path: Path,
    *,
    limit: int | None = None,
    offset: int = 0,
    reverse: bool = False,
    strict: bool = False,
) -> list[dict[str, Any]]:
    if not path.exists():
        return []

    lock_mode = fcntl.LOCK_SH if fcntl is not None else 0
    with _locked(path, lock_mode):
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()

    records: list[dict[str, Any]] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            if strict:
                raise
            continue
        if not isinstance(item, dict):
            if strict:
                raise ValueError("jsonl_record_must_be_object")
            continue
        records.append(item)

    if reverse:
        records = list(reversed(records))

    start = max(0, int(offset or 0))
    if start:
        records = records[start:]
    if limit is not None:
        records = records[: max(0, int(limit))]
    return records


def rewrite_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_mode = fcntl.LOCK_EX if fcntl is not None else 0
    with _locked(path, lock_mode):
        temp_path = path.with_name(path.name + ".tmp")
        with temp_path.open("w", encoding="utf-8") as f:
            for record in records:
                f.write(json_dumps(record) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, path)
