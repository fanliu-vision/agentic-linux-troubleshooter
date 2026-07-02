from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from monitors.jsonl_store import append_jsonl, read_jsonl, rewrite_jsonl


REPORT_INDEX_SCHEMA_VERSION = "report_index.v1"
REPORT_RECORD = "report"

REPORT_TYPE_DIAGNOSTIC = "diagnostic_report"
REPORT_TYPE_EVENT = "event_report"
REPORT_TYPE_AUTO_RECOVERY = "auto_recovery_report"
REPORT_TYPE_ROLLBACK = "rollback_report"
REPORT_TYPE_AUDIT_JSON = "audit_json"

REPORT_TYPES = {
    REPORT_TYPE_DIAGNOSTIC,
    REPORT_TYPE_EVENT,
    REPORT_TYPE_AUTO_RECOVERY,
    REPORT_TYPE_ROLLBACK,
    REPORT_TYPE_AUDIT_JSON,
}


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


def _sort_key(record: dict[str, Any]) -> tuple[str, str]:
    return (
        str(record.get("generated_at") or record.get("created_at") or ""),
        str(record.get("report_id", "")),
    )


class ReportIndexStore:
    def __init__(self, project_id: str, state_dir: str = "state") -> None:
        self.project_id = project_id
        self.state_dir = Path(state_dir)
        self.project_state_dir = self.state_dir / project_id
        self.report_index_path = self.project_state_dir / "report_index.jsonl"
        self.generated_reports_dir = self.project_state_dir / "reports"
        self.project_state_dir.mkdir(parents=True, exist_ok=True)

    def register_report(
        self,
        *,
        path: str,
        report_type: str,
        generated_at: str = "",
        fingerprint: str = "",
        event_type: str = "",
        job_id: str = "",
        title: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        report_type = report_type if report_type in REPORT_TYPES else REPORT_TYPE_EVENT
        generated_at = generated_at or _now_iso()
        report_id = self._report_id(
            path=path,
            report_type=report_type,
            fingerprint=fingerprint,
            event_type=event_type,
            job_id=job_id,
        )
        record = {
            "schema_version": REPORT_INDEX_SCHEMA_VERSION,
            "record_type": REPORT_RECORD,
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
            "generated_at": generated_at,
            "project_id": self.project_id,
            "report_id": report_id,
            "path": str(path),
            "fingerprint": str(fingerprint),
            "event_type": str(event_type),
            "job_id": str(job_id),
            "report_type": report_type,
            "title": title or self._title_for(report_type=report_type, path=path),
            "metadata": _jsonable(dict(metadata or {})),
        }
        self._append(record)
        return record

    def register_reports(
        self,
        paths: list[str],
        *,
        report_type: str,
        generated_at: str = "",
        fingerprint: str = "",
        event_type: str = "",
        job_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for path in paths:
            if not path:
                continue
            records.append(
                self.register_report(
                    path=path,
                    report_type=report_type,
                    generated_at=generated_at,
                    fingerprint=fingerprint,
                    event_type=event_type,
                    job_id=job_id,
                    metadata=metadata,
                )
            )
        return records

    def register_text_report(
        self,
        *,
        content: str,
        report_type: str,
        fingerprint: str = "",
        event_type: str = "",
        job_id: str = "",
        title: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        generated_at = _now_iso()
        path = self._generated_report_path(
            report_type=report_type,
            fingerprint=fingerprint,
            event_type=event_type,
            job_id=job_id,
            suffix=".md",
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return self.register_report(
            path=str(path),
            report_type=report_type,
            generated_at=generated_at,
            fingerprint=fingerprint,
            event_type=event_type,
            job_id=job_id,
            title=title,
            metadata=metadata,
        )

    def register_audit_json(
        self,
        *,
        audit_json: dict[str, Any],
        fingerprint: str = "",
        event_type: str = "",
        job_id: str = "",
        title: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        generated_at = _now_iso()
        path = self._generated_report_path(
            report_type=REPORT_TYPE_AUDIT_JSON,
            fingerprint=fingerprint,
            event_type=event_type,
            job_id=job_id,
            suffix=".json",
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(audit_json, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        return self.register_report(
            path=str(path),
            report_type=REPORT_TYPE_AUDIT_JSON,
            generated_at=generated_at,
            fingerprint=fingerprint,
            event_type=event_type,
            job_id=job_id,
            title=title or "审计 JSON",
            metadata=metadata,
        )

    def read_all(
        self,
        *,
        limit: int | None = None,
        offset: int = 0,
        reverse: bool = False,
    ) -> list[dict[str, Any]]:
        return read_jsonl(
            self.report_index_path,
            limit=limit,
            offset=offset,
            reverse=reverse,
        )

    def reports(
        self,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        latest: dict[str, dict[str, Any]] = {}
        for record in self.read_all():
            report_id = str(record.get("report_id", ""))
            if not report_id:
                continue
            latest[report_id] = record

        rows = sorted(latest.values(), key=_sort_key, reverse=True)
        if offset:
            rows = rows[max(0, int(offset)) :]
        if limit is not None:
            return rows[: max(0, int(limit))]
        return rows

    def reports_for_fingerprint(
        self,
        fingerprint: str,
        *,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        rows = [
            record for record in self.reports()
            if str(record.get("fingerprint", "")) == fingerprint
        ]
        if limit is not None:
            return rows[: max(0, int(limit))]
        return rows

    def grouped_for_event(self, fingerprint: str) -> dict[str, Any]:
        all_reports = self.reports()
        event_reports = [
            record for record in all_reports
            if str(record.get("fingerprint", "")) == fingerprint
        ]

        return {
            "latest": self._by_type(all_reports, REPORT_TYPE_DIAGNOSTIC, limit=5),
            "event": event_reports,
            "auto_recovery": self._by_type(
                event_reports,
                REPORT_TYPE_AUTO_RECOVERY,
            ),
            "rollback": self._by_type(event_reports, REPORT_TYPE_ROLLBACK),
            "audit_json": self._by_type(event_reports, REPORT_TYPE_AUDIT_JSON),
        }

    def get(self, report_id: str) -> dict[str, Any]:
        for record in self.reports():
            if record.get("report_id") == report_id:
                return record
        raise KeyError(f"report_not_found:{report_id}")

    def detail(self, report_id: str, *, max_chars: int = 200_000) -> dict[str, Any]:
        record = self.get(report_id)
        content, content_status = self._read_report_content(
            str(record.get("path", "")),
            max_chars=max_chars,
        )
        return {
            "report": record,
            "content": content,
            "content_status": content_status,
            "truncated": content_status == "truncated",
        }

    def _append(self, record: dict[str, Any]) -> None:
        append_jsonl(self.report_index_path, record)

    def compact(self) -> list[dict[str, Any]]:
        records = self.reports()
        rewrite_jsonl(self.report_index_path, list(reversed(records)))
        return records

    def _generated_report_path(
        self,
        *,
        report_type: str,
        fingerprint: str,
        event_type: str,
        job_id: str,
        suffix: str,
    ) -> Path:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        parts = [
            timestamp,
            self._safe_token(report_type),
            self._safe_token(event_type or "event"),
            self._safe_token(fingerprint or "nofp"),
            self._safe_token(job_id or "nojob"),
        ]
        return self.generated_reports_dir / ("_".join(parts) + suffix)

    def _read_report_content(
        self,
        path_text: str,
        *,
        max_chars: int,
    ) -> tuple[str, str]:
        path = self._resolve_safe_path(path_text)
        if path is None:
            return "", "path_not_allowed"
        if not path.exists() or not path.is_file():
            return "", "missing"

        text = path.read_text(encoding="utf-8", errors="replace")
        if len(text) > max_chars:
            return text[:max_chars], "truncated"
        return text, "ok"

    def _resolve_safe_path(self, path_text: str) -> Path | None:
        if not path_text:
            return None

        raw = Path(path_text)
        path = raw if raw.is_absolute() else Path.cwd() / raw
        try:
            resolved = path.resolve()
        except OSError:
            return None

        allowed_roots = [
            Path.cwd().resolve(),
            self.state_dir.resolve(),
            self.project_state_dir.resolve(),
        ]
        for root in allowed_roots:
            if resolved == root or root in resolved.parents:
                return resolved
        return None

    def _report_id(
        self,
        *,
        path: str,
        report_type: str,
        fingerprint: str,
        event_type: str,
        job_id: str,
    ) -> str:
        base = "|".join(
            [
                self.project_id,
                str(path),
                str(report_type),
                str(fingerprint),
                str(event_type),
                str(job_id),
            ]
        )
        return hashlib.sha256(base.encode("utf-8")).hexdigest()[:20]

    @staticmethod
    def _by_type(
        records: list[dict[str, Any]],
        report_type: str,
        *,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        rows = [
            record for record in records
            if record.get("report_type") == report_type
        ]
        if limit is not None:
            return rows[: max(0, int(limit))]
        return rows

    @staticmethod
    def _title_for(*, report_type: str, path: str) -> str:
        labels = {
            REPORT_TYPE_DIAGNOSTIC: "诊断报告",
            REPORT_TYPE_EVENT: "事件报告",
            REPORT_TYPE_AUTO_RECOVERY: "自动恢复报告",
            REPORT_TYPE_ROLLBACK: "回滚报告",
            REPORT_TYPE_AUDIT_JSON: "审计 JSON",
        }
        suffix = Path(path).name if path else ""
        label = labels.get(report_type, "报告")
        return f"{label} - {suffix}" if suffix else label

    @staticmethod
    def _safe_token(value: str) -> str:
        token = "".join(
            char if char.isalnum() or char in {"-", "_"} else "_"
            for char in str(value)
        ).strip("_")
        return token[:80] or "item"
