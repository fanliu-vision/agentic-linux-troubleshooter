from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from monitors.project_registry import ProjectConfig, ProjectRegistry
from monitors.recovery_history_store import (
    ROLLBACK_STATUS_AVAILABLE,
    RecoveryHistoryStore,
    _identity,
    backup_record,
    normalize_edits,
)


LOCAL_RECORD_NAME = "applied_fixes.json"
REMOTE_RECORD_NAME = "remote_applied_fixes.json"


class RecoveryHistoryService:
    def __init__(
        self,
        *,
        project_id: str,
        state_dir: str = "state",
        config_path: str = "configs/projects.yaml",
        output_root: str = "outputs/monitors",
    ) -> None:
        self.project_id = project_id
        self.state_dir = state_dir
        self.config_path = config_path
        self.output_root = output_root
        self.store = RecoveryHistoryStore(project_id=project_id, state_dir=state_dir)

    @property
    def project(self) -> ProjectConfig:
        return ProjectRegistry(self.config_path).get(self.project_id)

    def history(
        self,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> dict[str, Any]:
        rows = self.store.merged_records(
            self.scanned_apply_records(),
            limit=limit,
            offset=offset,
        )
        return {
            "project_id": self.project_id,
            "history_path": str(self.store.history_path),
            "records": rows,
            "rollback_target": self.latest_rollback_target(),
        }

    def latest_rollback_target(self) -> dict[str, Any]:
        mode = "remote" if self.project.is_remote else "local"
        record_name = REMOTE_RECORD_NAME if mode == "remote" else LOCAL_RECORD_NAME
        path = self._latest_record_path(record_name)
        if path is None:
            return {}

        records = _load_json_records(path)
        if not records:
            return {}

        record_index = len(records) - 1
        raw = records[record_index]
        target = self._record_from_apply_record(
            path=path,
            record=raw,
            record_index=record_index,
            mode=mode,
            rollback_available=True,
        )
        stored = self.store.get_applied_by_identity(str(target.get("identity", "")))
        if stored:
            enriched = dict(target)
            for key in ["history_id", "fingerprint", "event_type", "job_id", "request_id", "audit_json"]:
                if stored.get(key):
                    enriched[key] = stored.get(key)
            return enriched
        return target

    def scanned_apply_records(self) -> list[dict[str, Any]]:
        root = Path(self.output_root) / self.project_id
        if not root.exists():
            return []

        latest_local = self._latest_record_path(LOCAL_RECORD_NAME)
        latest_remote = self._latest_record_path(REMOTE_RECORD_NAME)
        rows: list[dict[str, Any]] = []
        for path in sorted(root.rglob("*.json")):
            if path.name not in {LOCAL_RECORD_NAME, REMOTE_RECORD_NAME}:
                continue
            records = _load_json_records(path)
            mode = "remote" if path.name == REMOTE_RECORD_NAME else "local"
            latest_path = latest_remote if mode == "remote" else latest_local
            for index, record in enumerate(records):
                rows.append(
                    self._record_from_apply_record(
                        path=path,
                        record=record,
                        record_index=index,
                        mode=mode,
                        rollback_available=(
                            path == latest_path
                            and index == len(records) - 1
                            and mode == ("remote" if self.project.is_remote else "local")
                        ),
                    )
                )
        return rows

    def _latest_record_path(self, record_name: str) -> Path | None:
        root = Path(self.output_root) / self.project_id
        if not root.exists():
            return None
        records = [path for path in root.rglob(record_name) if path.is_file()]
        if not records:
            return None
        return sorted(records, key=lambda path: path.stat().st_mtime)[-1]

    def _record_from_apply_record(
        self,
        *,
        path: Path,
        record: dict[str, Any],
        record_index: int,
        mode: str,
        rollback_available: bool,
    ) -> dict[str, Any]:
        edits = normalize_edits(list(record.get("edits") or []))
        identity = _identity(
            record_path=str(path),
            record_index=record_index,
            fix_id=str(record.get("fix_id", "")),
            edits=edits,
        )
        return {
            "history_id": "",
            "identity": identity,
            "created_at": _record_created_at(path),
            "updated_at": _record_created_at(path),
            "project_id": self.project_id,
            "fix_id": str(record.get("fix_id", "")),
            "fingerprint": str(record.get("fingerprint", "")),
            "event_type": str(record.get("event_type", "")),
            "job_id": str(record.get("job_id", "")),
            "request_id": str(record.get("request_id", "")),
            "mode": mode,
            "source": "scan",
            "record_path": str(path),
            "record_index": record_index,
            "edits": edits,
            "backup_record": backup_record(record_path=str(path), edits=edits),
            "rollback_status": ROLLBACK_STATUS_AVAILABLE,
            "rollback_available": bool(rollback_available and edits),
            "rollback_job_id": "",
            "rollback_report_id": "",
            "rollback_audit": {},
            "rollback_edits": [],
            "audit_json": {},
            "metadata": {
                "remote": record.get("remote", ""),
                "remote_project_dir": record.get("remote_project_dir", ""),
            },
        }


def _load_json_records(path: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def _record_created_at(path: Path) -> str:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
    except OSError:
        return ""
