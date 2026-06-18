from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from monitors.seen_fingerprints_compactor import SeenFingerprintsCompactor


def write_status(path: Path, seen_fingerprints: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "project_id": "test_project",
                "seen_fingerprints": seen_fingerprints,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def write_events(path: Path, rows: list[tuple[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps(
            {
                "created_at": created_at,
                "fingerprint": fingerprint,
            }
        )
        for fingerprint, created_at in rows
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_dry_run_does_not_modify_original_file() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        status_path = Path(tmp) / "state" / "project_status.json"
        events_path = Path(tmp) / "state" / "events.jsonl"
        write_status(status_path, ["old", "new"])
        write_events(
            events_path,
            [
                ("old", "2026-01-01 00:00:00"),
                ("new", "2026-01-10 00:00:00"),
            ],
        )
        before = status_path.read_text(encoding="utf-8")

        plan = SeenFingerprintsCompactor().build_plan(
            status_path=status_path,
            events_path=events_path,
            max_count=1,
            dry_run=True,
            now=datetime(2026, 1, 10),
        )

        after = status_path.read_text(encoding="utf-8")
        assert before == after
        assert plan["dry_run"] is True
        assert plan["would_write"] is False


def test_max_count_keeps_latest_n_fingerprints() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        status_path = Path(tmp) / "project_status.json"
        events_path = Path(tmp) / "events.jsonl"
        write_status(status_path, ["one", "two", "three"])
        write_events(
            events_path,
            [
                ("one", "2026-01-01 00:00:00"),
                ("two", "2026-01-02 00:00:00"),
                ("three", "2026-01-03 00:00:00"),
            ],
        )

        plan = SeenFingerprintsCompactor().build_plan(
            status_path=status_path,
            events_path=events_path,
            max_count=2,
            now=datetime(2026, 1, 10),
        )

        assert plan["keep_count"] == 2
        assert plan["drop_count"] == 1
        assert plan["keep_fingerprints"] == ["two", "three"]
        assert [item["fingerprint"] for item in plan["drop_candidates"]] == ["one"]


def test_max_age_days_filters_old_fingerprints() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        status_path = Path(tmp) / "project_status.json"
        events_path = Path(tmp) / "events.jsonl"
        write_status(status_path, ["old", "recent"])
        write_events(
            events_path,
            [
                ("old", "2026-01-01 00:00:00"),
                ("recent", "2026-01-09 00:00:00"),
            ],
        )

        plan = SeenFingerprintsCompactor().build_plan(
            status_path=status_path,
            events_path=events_path,
            max_age_days=2,
            now=datetime(2026, 1, 10),
        )

        assert plan["keep_fingerprints"] == ["recent"]
        assert [item["fingerprint"] for item in plan["drop_candidates"]] == ["old"]


def test_empty_file_returns_empty_plan_without_error() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        status_path = Path(tmp) / "project_status.json"
        status_path.write_text("", encoding="utf-8")

        plan = SeenFingerprintsCompactor().build_plan(status_path=status_path)

        assert plan["total_fingerprints"] == 0
        assert plan["keep_count"] == 0
        assert plan["drop_count"] == 0
        assert plan["errors"] == []


def test_corrupt_status_returns_safe_error_plan() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        status_path = Path(tmp) / "project_status.json"
        status_path.write_text("{not-json", encoding="utf-8")

        plan = SeenFingerprintsCompactor().build_plan(status_path=status_path)

        assert plan["dry_run"] is True
        assert plan["would_write"] is False
        assert plan["safe_to_apply"] is False
        assert plan["risk"] == "safe_error_no_action"
        assert plan["errors"]


def test_compact_plan_contains_keep_drop_statistics() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        status_path = Path(tmp) / "project_status.json"
        write_status(status_path, ["one", "two", "three"])

        plan = SeenFingerprintsCompactor().build_plan(
            status_path=status_path,
            max_count=2,
        )

        assert plan["total_fingerprints"] == 3
        assert plan["keep_count"] == 2
        assert plan["drop_count"] == 1
        assert plan["keep_policy"]["max_count"] == 2
        assert plan["estimated_reduction"]["fingerprints"] == 1
        assert plan["drop_candidates"]


def test_compactor_never_drops_all_fingerprints() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        status_path = Path(tmp) / "project_status.json"
        events_path = Path(tmp) / "events.jsonl"
        write_status(status_path, ["older", "old"])
        write_events(
            events_path,
            [
                ("older", "2026-01-01 00:00:00"),
                ("old", "2026-01-02 00:00:00"),
            ],
        )

        plan = SeenFingerprintsCompactor().build_plan(
            status_path=status_path,
            events_path=events_path,
            max_age_days=1,
            now=datetime(2026, 1, 10),
        )

        assert plan["keep_count"] == 1
        assert plan["drop_count"] == 1
        assert plan["keep_fingerprints"] == ["old"]


def test_default_dry_run_is_true_and_audit_report_can_be_written_to_temp_dir() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        status_path = Path(tmp) / "project_status.json"
        audit_path = Path(tmp) / "audit" / "seen_compact.md"
        write_status(status_path, ["one", "two"])

        compactor = SeenFingerprintsCompactor()
        plan = compactor.build_plan(status_path=status_path, max_count=1)
        compactor.write_audit_report(plan, audit_path)

        assert plan["dry_run"] is True
        assert audit_path.exists()
        assert "seen_fingerprints compact dry-run" in audit_path.read_text(
            encoding="utf-8"
        )
