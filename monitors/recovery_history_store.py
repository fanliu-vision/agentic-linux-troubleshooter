from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from monitors.jsonl_store import append_jsonl, read_jsonl, rewrite_jsonl


RECOVERY_HISTORY_SCHEMA_VERSION = "recovery_history.v1"

RECOVERY_RECORD_APPLIED = "fix_applied"
RECOVERY_RECORD_ROLLBACK_STARTED = "rollback_started"
RECOVERY_RECORD_ROLLBACK_FINISHED = "rollback_finished"

ROLLBACK_STATUS_AVAILABLE = "available"
ROLLBACK_STATUS_RUNNING = "running"
ROLLBACK_STATUS_SUCCEEDED = "succeeded"
ROLLBACK_STATUS_FAILED = "failed"
ROLLBACK_STATUS_NOT_AVAILABLE = "not_available"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_dumps(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


def _jsonable(data: Any) -> Any:
    try:
        json.dumps(data, ensure_ascii=False)
        return data
    except TypeError:
        return json.loads(json.dumps(data, ensure_ascii=False, default=str))


def _identity(
    *,
    record_path: str,
    record_index: int,
    fix_id: str,
    edits: list[dict[str, Any]],
) -> str:
    raw = json.dumps(
        {
            "record_path": record_path,
            "record_index": record_index,
            "fix_id": fix_id,
            "edits": edits,
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def normalize_edits(edits: list[Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in edits:
        if not isinstance(item, dict):
            item = {
                "field_path": str(getattr(item, "field_path", "")),
                "old_value": getattr(item, "old_value", None),
                "new_value": getattr(item, "new_value", None),
                "config_path": str(getattr(item, "config_path", "")),
                "backup_path": str(getattr(item, "backup_path", "")),
                "diff_path": str(getattr(item, "diff_path", "")),
                "success": bool(getattr(item, "success", False)),
                "message": str(getattr(item, "message", "")),
            }
        normalized.append(
            {
                "field_path": str(item.get("field_path", "")),
                "old_value": _jsonable(item.get("old_value")),
                "new_value": _jsonable(item.get("new_value")),
                "config_path": str(item.get("config_path", "")),
                "backup_path": str(item.get("backup_path", "")),
                "diff_path": str(item.get("diff_path", "")),
                "success": bool(item.get("success", True)),
                "message": str(item.get("message", "")),
            }
        )
    return normalized


def backup_record(
    *,
    record_path: str,
    edits: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "record_path": str(record_path),
        "backup_paths": [str(item.get("backup_path", "")) for item in edits if item.get("backup_path")],
        "diff_paths": [str(item.get("diff_path", "")) for item in edits if item.get("diff_path")],
        "edit_count": len(edits),
    }


class RecoveryHistoryStore:
    def __init__(self, project_id: str, state_dir: str = "state") -> None:
        self.project_id = project_id
        self.state_dir = Path(state_dir)
        self.project_state_dir = self.state_dir / project_id
        self.history_path = self.project_state_dir / "recovery_history.jsonl"
        self.project_state_dir.mkdir(parents=True, exist_ok=True)

    def register_applied(
        self,
        *,
        fix_id: str,
        edits: list[Any],
        record_path: str = "",
        record_index: int = -1,
        fingerprint: str = "",
        event_type: str = "",
        job_id: str = "",
        request_id: str = "",
        mode: str = "",
        source: str = "",
        audit_json: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_edits = normalize_edits(edits)
        if not normalized_edits:
            return {}

        identity = _identity(
            record_path=str(record_path),
            record_index=int(record_index),
            fix_id=str(fix_id),
            edits=normalized_edits,
        )
        existing = self.get_applied_by_identity(identity)
        if existing:
            return existing

        now = _now_iso()
        record = {
            "schema_version": RECOVERY_HISTORY_SCHEMA_VERSION,
            "record_type": RECOVERY_RECORD_APPLIED,
            "created_at": now,
            "updated_at": now,
            "project_id": self.project_id,
            "history_id": uuid.uuid4().hex,
            "identity": identity,
            "fix_id": str(fix_id),
            "fingerprint": str(fingerprint),
            "event_type": str(event_type),
            "job_id": str(job_id),
            "request_id": str(request_id),
            "mode": str(mode),
            "source": str(source),
            "record_path": str(record_path),
            "record_index": int(record_index),
            "edits": normalized_edits,
            "backup_record": backup_record(
                record_path=str(record_path),
                edits=normalized_edits,
            ),
            "rollback_status": ROLLBACK_STATUS_AVAILABLE,
            "rollback_available": True,
            "rollback_job_id": "",
            "rollback_report_id": "",
            "rollback_audit": {},
            "audit_json": _jsonable(dict(audit_json or {})),
            "metadata": _jsonable(dict(metadata or {})),
        }
        self._append(record)
        return record

    def record_rollback_started(
        self,
        *,
        target: dict[str, Any],
        job_id: str,
        operator: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        record = self._rollback_record(
            record_type=RECOVERY_RECORD_ROLLBACK_STARTED,
            target=target,
            job_id=job_id,
            operator=operator,
            rollback_status=ROLLBACK_STATUS_RUNNING,
            success=False,
            rollback_edits=[],
            report_id="",
            audit_json={},
            metadata=metadata,
        )
        self._append(record)
        return record

    def record_rollback_finished(
        self,
        *,
        target: dict[str, Any],
        job_id: str,
        operator: str = "",
        success: bool,
        rollback_edits: list[Any],
        report_id: str = "",
        audit_json: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        record = self._rollback_record(
            record_type=RECOVERY_RECORD_ROLLBACK_FINISHED,
            target=target,
            job_id=job_id,
            operator=operator,
            rollback_status=(
                ROLLBACK_STATUS_SUCCEEDED if success else ROLLBACK_STATUS_FAILED
            ),
            success=success,
            rollback_edits=normalize_edits(rollback_edits),
            report_id=report_id,
            audit_json=dict(audit_json or {}),
            metadata=metadata,
        )
        self._append(record)
        return record

    def read_all(
        self,
        *,
        limit: int | None = None,
        offset: int = 0,
        reverse: bool = False,
    ) -> list[dict[str, Any]]:
        return read_jsonl(
            self.history_path,
            limit=limit,
            offset=offset,
            reverse=reverse,
        )

    def get_applied_by_identity(self, identity: str) -> dict[str, Any]:
        applied = [
            item for item in self.read_all()
            if item.get("record_type") == RECOVERY_RECORD_APPLIED
            and item.get("identity") == identity
        ]
        if not applied:
            return {}
        return sorted(applied, key=lambda item: str(item.get("created_at", "")))[-1]

    def latest_rollbacks_by_identity(self) -> dict[str, dict[str, Any]]:
        latest: dict[str, dict[str, Any]] = {}
        for record in self.read_all():
            if record.get("record_type") not in {
                RECOVERY_RECORD_ROLLBACK_STARTED,
                RECOVERY_RECORD_ROLLBACK_FINISHED,
            }:
                continue
            identity = str(record.get("target_identity", ""))
            if not identity:
                continue
            if str(record.get("created_at", "")) >= str(latest.get(identity, {}).get("created_at", "")):
                latest[identity] = record
        return latest

    def merged_records(
        self,
        scanned_records: list[dict[str, Any]],
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        scanned_by_identity = {
            str(item.get("identity", "")): dict(item)
            for item in scanned_records
            if item.get("identity")
        }
        stored_applied = [
            item for item in self.read_all()
            if item.get("record_type") == RECOVERY_RECORD_APPLIED
        ]
        latest_rollback = self.latest_rollbacks_by_identity()

        merged: dict[str, dict[str, Any]] = {}
        for item in stored_applied:
            identity = str(item.get("identity", ""))
            if identity:
                merged[identity] = self._ui_record(dict(item), source="store")

        for identity, item in scanned_by_identity.items():
            base = dict(merged.get(identity, {}))
            base.update(self._ui_record(item, source="scan"))
            merged[identity] = base

        for identity, rollback in latest_rollback.items():
            target = dict(rollback.get("target_snapshot") or {})
            if identity not in merged and target:
                merged[identity] = self._ui_record(target, source="rollback")
            if identity in merged:
                merged[identity].update(
                    {
                        "rollback_status": str(rollback.get("rollback_status", "")),
                        "rollback_available": (
                            bool(merged[identity].get("rollback_available"))
                            and rollback.get("rollback_status") != ROLLBACK_STATUS_SUCCEEDED
                        ),
                        "rollback_job_id": str(rollback.get("job_id", "")),
                        "rollback_report_id": str(rollback.get("report_id", "")),
                        "rollback_audit": dict(rollback.get("audit_json") or {}),
                        "rollback_edits": list(rollback.get("rollback_edits") or []),
                        "rolled_back_at": str(rollback.get("created_at", "")),
                    }
                )

        rows = list(merged.values())
        rows = sorted(
            rows,
            key=lambda item: (
                str(item.get("updated_at") or item.get("created_at") or ""),
                str(item.get("history_id", "")),
            ),
            reverse=True,
        )
        if offset:
            rows = rows[max(0, int(offset)) :]
        if limit is not None:
            rows = rows[: max(0, int(limit))]
        return rows

    def _rollback_record(
        self,
        *,
        record_type: str,
        target: dict[str, Any],
        job_id: str,
        operator: str,
        rollback_status: str,
        success: bool,
        rollback_edits: list[dict[str, Any]],
        report_id: str,
        audit_json: dict[str, Any],
        metadata: dict[str, Any] | None,
    ) -> dict[str, Any]:
        now = _now_iso()
        return {
            "schema_version": RECOVERY_HISTORY_SCHEMA_VERSION,
            "record_type": record_type,
            "created_at": now,
            "updated_at": now,
            "project_id": self.project_id,
            "rollback_id": uuid.uuid4().hex,
            "target_history_id": str(target.get("history_id", "")),
            "target_identity": str(target.get("identity", "")),
            "target_snapshot": _jsonable(dict(target)),
            "job_id": str(job_id),
            "operator": str(operator),
            "fix_id": str(target.get("fix_id", "")),
            "fingerprint": str(target.get("fingerprint", "")),
            "event_type": str(target.get("event_type", "")),
            "mode": str(target.get("mode", "")),
            "record_path": str(target.get("record_path", "")),
            "rollback_status": rollback_status,
            "success": bool(success),
            "rollback_edits": _jsonable(list(rollback_edits)),
            "report_id": str(report_id),
            "audit_json": _jsonable(dict(audit_json)),
            "metadata": _jsonable(dict(metadata or {})),
        }

    @staticmethod
    def _ui_record(record: dict[str, Any], *, source: str) -> dict[str, Any]:
        edits = normalize_edits(list(record.get("edits") or []))
        return {
            "history_id": str(record.get("history_id", "")),
            "identity": str(record.get("identity", "")),
            "created_at": str(record.get("created_at", "")),
            "updated_at": str(record.get("updated_at", "")),
            "project_id": str(record.get("project_id", "")),
            "fix_id": str(record.get("fix_id", "")),
            "fingerprint": str(record.get("fingerprint", "")),
            "event_type": str(record.get("event_type", "")),
            "job_id": str(record.get("job_id", "")),
            "request_id": str(record.get("request_id", "")),
            "mode": str(record.get("mode", "")),
            "source": source,
            "record_path": str(record.get("record_path", "")),
            "record_index": int(record.get("record_index", -1)),
            "edits": edits,
            "backup_record": dict(
                record.get("backup_record")
                or backup_record(record_path=str(record.get("record_path", "")), edits=edits)
            ),
            "rollback_status": str(
                record.get("rollback_status", ROLLBACK_STATUS_AVAILABLE)
            ),
            "rollback_available": bool(record.get("rollback_available", False)),
            "rollback_job_id": str(record.get("rollback_job_id", "")),
            "rollback_report_id": str(record.get("rollback_report_id", "")),
            "rollback_audit": dict(record.get("rollback_audit") or {}),
            "rollback_edits": list(record.get("rollback_edits") or []),
            "audit_json": dict(record.get("audit_json") or {}),
            "metadata": dict(record.get("metadata") or {}),
        }

    def _append(self, record: dict[str, Any]) -> None:
        append_jsonl(self.history_path, record)

    def compact(self) -> list[dict[str, Any]]:
        records = self.read_all()
        rewrite_jsonl(self.history_path, records)
        return records
