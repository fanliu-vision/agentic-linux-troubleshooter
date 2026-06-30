from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.r14_retention_dry_run import RetentionConfig, run_dry_run


NOW = datetime(2026, 6, 30, 12, 0, 0, tzinfo=timezone.utc)


def write_file(root: Path, relative_path: str, text: str, *, days_old: int) -> Path:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    mtime = (NOW - timedelta(days=days_old)).timestamp()
    os.utime(path, (mtime, mtime))
    return path


def write_json(root: Path, relative_path: str, payload: dict, *, days_old: int) -> Path:
    return write_file(
        root,
        relative_path,
        json.dumps(payload, ensure_ascii=False, indent=2),
        days_old=days_old,
    )


def read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_retention_dry_run_classifies_protects_and_selects_candidates(
    tmp_path: Path,
) -> None:
    old_candidate_report = write_file(
        tmp_path,
        "outputs/monitors/projA/old1/"
        "event_20260501_010000_network_port_auto_recover_final_llm_report.md",
        "network port recovered report",
        days_old=60,
    )
    referenced_report = write_file(
        tmp_path,
        "outputs/monitors/projA/oldref/"
        "event_20260501_020000_network_port_auto_recover_final_llm_report.md",
        "network port referenced report",
        days_old=60,
    )
    manual_report = write_file(
        tmp_path,
        "outputs/monitors/projA/manual/"
        "event_20260501_030000_disk_full_manual_escalation_final_llm_report.md",
        "manual_escalation operator_required",
        days_old=60,
    )
    latest_report = write_file(
        tmp_path,
        "outputs/monitors/projA/new/"
        "event_20260629_010000_cache_write_failed_auto_recover_final_llm_report.md",
        "latest cache report",
        days_old=1,
    )
    recovery_state = write_json(
        tmp_path,
        "outputs/monitors/projA/old1/remote_applied_fixes.json",
        {"rollback_available": True, "records": []},
        days_old=60,
    )

    write_file(
        tmp_path,
        "outputs/alerts/projA_alerts.jsonl",
        json.dumps(
            {
                "project_id": "projA",
                "action": "auto_recover",
                "status": "recovered",
                "report_paths": [str(referenced_report.relative_to(tmp_path))],
            }
        )
        + "\n",
        days_old=1,
    )
    write_file(
        tmp_path,
        "outputs/alerts/projA_latest_alert.md",
        "# latest alert\n",
        days_old=1,
    )
    old_alert = write_json(
        tmp_path,
        "outputs/alerts/projA_alerts/"
        "20260501_010000_network_port_recovered_abcd.json",
        {
            "project_id": "projA",
            "action": "auto_recover",
            "status": "recovered",
            "report_paths": [str(referenced_report.relative_to(tmp_path))],
        },
        days_old=60,
    )
    write_json(
        tmp_path,
        "outputs/alerts/projA_alerts/"
        "20260629_010000_cache_write_failed_recovered_efgh.json",
        {
            "project_id": "projA",
            "action": "auto_recover",
            "status": "recovered",
            "report_paths": [str(latest_report.relative_to(tmp_path))],
        },
        days_old=1,
    )
    manual_alert = write_json(
        tmp_path,
        "outputs/alerts/projA_alerts/"
        "20260501_030000_disk_full_manual_escalation_zzzz.json",
        {
            "project_id": "projA",
            "action": "manual_escalation",
            "status": "manual_escalation",
            "report_paths": [str(manual_report.relative_to(tmp_path))],
        },
        days_old=60,
    )

    old_acceptance = write_file(
        tmp_path,
        "acceptance_artifacts/r14_smoke_20260501_010000/summary.md",
        "# old acceptance\n",
        days_old=60,
    )
    latest_acceptance = write_file(
        tmp_path,
        "acceptance_artifacts/r14_smoke_20260629_010000/summary.md",
        "# latest acceptance\n",
        days_old=1,
    )
    timeless_acceptance = write_file(
        tmp_path,
        "acceptance_artifacts/r13_run_logs/r13.log",
        "no timestamp run bucket\n",
        days_old=60,
    )

    daemon_log = write_file(
        tmp_path,
        "state/projA/daemon.log",
        "daemon line\n" * 5,
        days_old=0,
    )
    old_daemon_backup = write_file(
        tmp_path,
        "state/projA/daemon.log.20260501_010000",
        "old daemon backup\n",
        days_old=60,
    )
    latest_daemon_backup = write_file(
        tmp_path,
        "state/projA/daemon.log.20260629_010000",
        "latest daemon backup\n",
        days_old=1,
    )
    project_status = write_json(
        tmp_path,
        "state/projA/project_status.json",
        {"status": "ok"},
        days_old=0,
    )
    events_jsonl = write_file(
        tmp_path,
        "state/projA/events.jsonl",
        "{}\n",
        days_old=0,
    )

    output_dir = tmp_path / "acceptance_artifacts" / "r14_3b_retention_dry_run_test"
    summary = run_dry_run(
        project_root=tmp_path,
        output_dir=output_dir,
        now=NOW,
        config=RetentionConfig(
            reports_retention_days=30,
            keep_latest_reports_per_project=1,
            alerts_retention_days=30,
            keep_latest_alerts_per_project=1,
            acceptance_artifacts_retention_days=30,
            keep_latest_artifact_runs_per_prefix=1,
            daemon_log_max_size_bytes=20,
            daemon_log_keep_backups=1,
        ),
    )

    inventory_by_path = {row["path"]: row for row in summary["inventory"]}
    candidates_by_path = {row["path"]: row for row in summary["candidates"]}
    protected_by_path = {row["path"]: row for row in summary["protected"]}

    assert inventory_by_path[str(old_candidate_report.relative_to(tmp_path))][
        "artifact_type"
    ] == "monitor_event_report"
    assert inventory_by_path[str(old_alert.relative_to(tmp_path))]["artifact_type"] == (
        "alert_archive_json"
    )
    assert inventory_by_path[str(daemon_log.relative_to(tmp_path))]["artifact_type"] == (
        "daemon_log"
    )

    assert str(old_candidate_report.relative_to(tmp_path)) in candidates_by_path
    assert "older_than_reports_retention_days" in candidates_by_path[
        str(old_candidate_report.relative_to(tmp_path))
    ]["candidate_reasons"]
    assert str(old_alert.relative_to(tmp_path)) in candidates_by_path
    assert str(old_acceptance.relative_to(tmp_path)) in candidates_by_path
    assert str(old_daemon_backup.relative_to(tmp_path)) in candidates_by_path

    daemon_row = candidates_by_path[str(daemon_log.relative_to(tmp_path))]
    assert daemon_row["candidate_action"] == "rotate_copy_plan"
    assert daemon_row["protected"] is True
    assert daemon_row["estimated_reclaim_bytes"] == 0
    assert daemon_row["details"]["would_truncate"] is False

    referenced_row = protected_by_path[str(referenced_report.relative_to(tmp_path))]
    assert referenced_row["referenced_by_alert"] is True
    assert "referenced_by_alert" in referenced_row["protected_reasons"]
    assert "manual_escalation_or_operator_required" in protected_by_path[
        str(manual_report.relative_to(tmp_path))
    ]["protected_reasons"]
    assert "manual_escalation_or_operator_required" in protected_by_path[
        str(manual_alert.relative_to(tmp_path))
    ]["protected_reasons"]
    assert "within_latest_report_keep_limit" in protected_by_path[
        str(latest_report.relative_to(tmp_path))
    ]["protected_reasons"]
    assert "recovery_state_or_rollback_audit" in protected_by_path[
        str(recovery_state.relative_to(tmp_path))
    ]["protected_reasons"]
    assert "within_latest_acceptance_run_keep_limit" in protected_by_path[
        str(latest_acceptance.relative_to(tmp_path))
    ]["protected_reasons"]
    assert "acceptance_run_without_timestamp" in protected_by_path[
        str(timeless_acceptance.relative_to(tmp_path))
    ]["protected_reasons"]
    assert "within_daemon_backup_keep_limit" in protected_by_path[
        str(latest_daemon_backup.relative_to(tmp_path))
    ]["protected_reasons"]
    assert "state_core_file" in protected_by_path[
        str(project_status.relative_to(tmp_path))
    ]["protected_reasons"]
    assert "state_core_file" in protected_by_path[
        str(events_jsonl.relative_to(tmp_path))
    ]["protected_reasons"]

    assert summary["metrics"]["candidate_count"] >= 5
    assert summary["metrics"]["delete_candidate_count"] >= 4
    assert summary["metrics"]["daemon_rotation_plan_count"] == 1
    assert summary["safety"] == {
        "delete_executed": False,
        "move_executed": False,
        "truncate_executed": False,
        "rotation_executed": False,
        "state_modified": False,
    }


def test_retention_dry_run_writes_jsonl_and_chinese_markdown(tmp_path: Path) -> None:
    report = write_file(
        tmp_path,
        "outputs/monitors/projA/old1/"
        "event_20260501_010000_network_port_auto_recover_final_llm_report.md",
        "old report",
        days_old=60,
    )
    output_dir = tmp_path / "acceptance_artifacts" / "r14_3b_retention_dry_run_test"

    run_dry_run(
        project_root=tmp_path,
        output_dir=output_dir,
        now=NOW,
        config=RetentionConfig(
            reports_retention_days=30,
            keep_latest_reports_per_project=0,
            alerts_retention_days=30,
            keep_latest_alerts_per_project=0,
            acceptance_artifacts_retention_days=30,
            keep_latest_artifact_runs_per_prefix=0,
            daemon_log_max_size_bytes=20,
            daemon_log_keep_backups=0,
        ),
    )

    for file_name in [
        "retention_inventory.json",
        "retention_plan.json",
        "retention_candidates.jsonl",
        "retention_protected.jsonl",
        "retention_summary.json",
        "retention_dry_run_report.md",
    ]:
        assert (output_dir / file_name).exists()

    candidates = read_jsonl(output_dir / "retention_candidates.jsonl")
    assert any(row["path"] == str(report.relative_to(tmp_path)) for row in candidates)

    markdown = (output_dir / "retention_dry_run_report.md").read_text(
        encoding="utf-8"
    )
    assert "# R14-3b Retention / Log Rotation Dry-Run 报告" in markdown
    assert "不会删除文件" in markdown
    assert "候选清单" in markdown
    assert "保护清单" in markdown

    assert report.exists()
