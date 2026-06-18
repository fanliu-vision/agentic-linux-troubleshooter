from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _parse_time(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None

    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue

    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


@dataclass(frozen=True)
class FingerprintRecord:
    fingerprint: str
    index: int
    seen_at: datetime | None = None

    @property
    def seen_at_text(self) -> str:
        if self.seen_at is None:
            return ""
        return self.seen_at.strftime("%Y-%m-%d %H:%M:%S")


class SeenFingerprintsCompactor:
    """
    Build dry-run compact plans for ProjectMonitorState.seen_fingerprints.

    This component deliberately does not write compacted state back. Any real
    compact/apply flow must be added in a later phase with backup and audit.
    """

    def build_plan(
            self,
            status_path: str | Path,
            events_path: str | Path | None = None,
            max_count: int | None = None,
            max_age_days: int | None = None,
            dry_run: bool = True,
            now: datetime | None = None,
    ) -> dict[str, Any]:
        status_path = Path(status_path)
        events_path = Path(events_path) if events_path is not None else None
        now = now or datetime.now()
        keep_policy = {
            "max_count": max_count,
            "max_age_days": max_age_days,
            "min_keep": 1,
        }

        try:
            raw_state = self._read_status(status_path)
            raw_seen = raw_state.get("seen_fingerprints") or []
            records = self._normalize_seen(raw_seen)
        except Exception as exc:
            return self._error_plan(
                status_path=status_path,
                keep_policy=keep_policy,
                dry_run=dry_run,
                error=f"{type(exc).__name__}: {exc}",
            )

        event_times = self._read_event_times(events_path)
        records = [
            FingerprintRecord(
                fingerprint=record.fingerprint,
                index=record.index,
                seen_at=record.seen_at or event_times.get(record.fingerprint),
            )
            for record in records
        ]

        keep, drop = self._split_records(
            records=records,
            max_count=max_count,
            max_age_days=max_age_days,
            now=now,
        )

        return {
            "created_at": _now_text(),
            "status_path": str(status_path),
            "events_path": str(events_path) if events_path is not None else "",
            "dry_run": bool(dry_run),
            "would_write": False,
            "safe_to_apply": False,
            "total_fingerprints": len(records),
            "keep_count": len(keep),
            "drop_count": len(drop),
            "keep_policy": keep_policy,
            "drop_candidates": [self._record_to_dict(item) for item in drop],
            "keep_fingerprints": [item.fingerprint for item in keep],
            "estimated_reduction": {
                "fingerprints": len(drop),
                "percent": round((len(drop) / len(records)) * 100, 2) if records else 0.0,
            },
            "risk": self._risk_summary(records=records, drop=drop),
            "errors": [],
        }

    def write_audit_report(
            self,
            plan: dict[str, Any],
            output_path: str | Path,
    ) -> Path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(self.render_audit_markdown(plan), encoding="utf-8")
        return output_path

    def render_audit_markdown(self, plan: dict[str, Any]) -> str:
        lines = [
            "# seen_fingerprints compact dry-run",
            "",
            f"- created_at: `{plan.get('created_at', '')}`",
            f"- dry_run: `{plan.get('dry_run', True)}`",
            f"- would_write: `{plan.get('would_write', False)}`",
            f"- safe_to_apply: `{plan.get('safe_to_apply', False)}`",
            f"- total_fingerprints: `{plan.get('total_fingerprints', 0)}`",
            f"- keep_count: `{plan.get('keep_count', 0)}`",
            f"- drop_count: `{plan.get('drop_count', 0)}`",
            "",
            "## Risk",
            "",
            str(plan.get("risk", "")),
            "",
            "## Drop Candidates",
            "",
        ]

        candidates = list(plan.get("drop_candidates") or [])
        if not candidates:
            lines.append("- <none>")
        else:
            for item in candidates:
                lines.append(
                    f"- `{item.get('fingerprint', '')}` "
                    f"reason=`{item.get('drop_reason', '')}` "
                    f"seen_at=`{item.get('seen_at', '')}`"
                )

        errors = list(plan.get("errors") or [])
        if errors:
            lines.extend(["", "## Errors", ""])
            for error in errors:
                lines.append(f"- {error}")

        return "\n".join(lines) + "\n"

    def _read_status(self, status_path: Path) -> dict[str, Any]:
        if not status_path.exists():
            return {}

        text = status_path.read_text(encoding="utf-8").strip()
        if not text:
            return {}

        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("project_status must be a JSON object")
        return data

    def _normalize_seen(self, raw_seen: Any) -> list[FingerprintRecord]:
        if not isinstance(raw_seen, list):
            raise ValueError("seen_fingerprints must be a list")

        records: list[FingerprintRecord] = []
        for index, item in enumerate(raw_seen):
            if isinstance(item, str):
                fingerprint = item
                seen_at = None
            elif isinstance(item, dict):
                fingerprint = str(item.get("fingerprint", ""))
                seen_at = _parse_time(
                    str(item.get("seen_at") or item.get("created_at") or "")
                )
            else:
                fingerprint = str(item)
                seen_at = None

            if fingerprint:
                records.append(
                    FingerprintRecord(
                        fingerprint=fingerprint,
                        index=index,
                        seen_at=seen_at,
                    )
                )

        return records

    def _read_event_times(self, events_path: Path | None) -> dict[str, datetime]:
        if events_path is None or not events_path.exists():
            return {}

        event_times: dict[str, datetime] = {}
        try:
            lines = events_path.read_text(encoding="utf-8").splitlines()
        except Exception:
            return {}

        for line in lines:
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue

            fingerprint = str(item.get("fingerprint", ""))
            created_at = _parse_time(str(item.get("created_at", "")))
            if not fingerprint or created_at is None:
                continue

            old = event_times.get(fingerprint)
            if old is None or created_at > old:
                event_times[fingerprint] = created_at

        return event_times

    def _split_records(
            self,
            records: list[FingerprintRecord],
            max_count: int | None,
            max_age_days: int | None,
            now: datetime,
    ) -> tuple[list[FingerprintRecord], list[FingerprintRecord]]:
        if not records:
            return [], []

        newest_first = sorted(
            records,
            key=lambda item: (
                item.seen_at or datetime.min,
                item.index,
            ),
            reverse=True,
        )

        keep: dict[str, FingerprintRecord] = {}
        drop: dict[str, FingerprintRecord] = {}

        count_keep: set[str] = set()
        if max_count is not None:
            max_count_value = max(1, max_count)
            count_keep = {
                item.fingerprint for item in newest_first[:max_count_value]
            }

        cutoff = None
        if max_age_days is not None:
            cutoff = now - timedelta(days=max_age_days)

        for item in records:
            keep_reasons = []
            drop_reasons = []

            if max_count is not None:
                if item.fingerprint in count_keep:
                    keep_reasons.append("within_max_count")
                else:
                    drop_reasons.append("exceeds_max_count")

            if cutoff is not None:
                if item.seen_at is None:
                    keep_reasons.append("missing_timestamp_conservative_keep")
                elif item.seen_at >= cutoff:
                    keep_reasons.append("within_max_age_days")
                else:
                    drop_reasons.append("older_than_max_age_days")

            if max_count is None and cutoff is None:
                keep_reasons.append("no_compact_policy")

            should_drop = bool(drop_reasons) and not keep_reasons
            if should_drop:
                drop[item.fingerprint] = item
            else:
                keep[item.fingerprint] = item

        if not keep and drop:
            rescue = newest_first[0]
            keep[rescue.fingerprint] = rescue
            drop.pop(rescue.fingerprint, None)

        keep_sorted = sorted(keep.values(), key=lambda item: item.index)
        drop_sorted = sorted(drop.values(), key=lambda item: item.index)
        return keep_sorted, drop_sorted

    def _record_to_dict(self, record: FingerprintRecord) -> dict[str, Any]:
        return {
            "fingerprint": record.fingerprint,
            "index": record.index,
            "seen_at": record.seen_at_text,
            "drop_reason": "outside_keep_policy",
            "risk": "medium",
        }

    def _risk_summary(
            self,
            records: list[FingerprintRecord],
            drop: list[FingerprintRecord],
    ) -> str:
        if not records:
            return "empty_seen_fingerprints_no_action"
        if not drop:
            return "dry_run_only_no_drop_candidates"
        return (
            "dry_run_only_requires_backup_before_apply; "
            "real compact must preserve latest fingerprints and audit all drops"
        )

    def _error_plan(
            self,
            status_path: Path,
            keep_policy: dict[str, Any],
            dry_run: bool,
            error: str,
    ) -> dict[str, Any]:
        return {
            "created_at": _now_text(),
            "status_path": str(status_path),
            "events_path": "",
            "dry_run": bool(dry_run),
            "would_write": False,
            "safe_to_apply": False,
            "total_fingerprints": 0,
            "keep_count": 0,
            "drop_count": 0,
            "keep_policy": keep_policy,
            "drop_candidates": [],
            "keep_fingerprints": [],
            "estimated_reduction": {
                "fingerprints": 0,
                "percent": 0.0,
            },
            "risk": "safe_error_no_action",
            "errors": [error],
        }
