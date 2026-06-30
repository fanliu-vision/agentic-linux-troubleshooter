from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import recovery.auto_recovery_runtime_controls as runtime_controls
from fixers.apply_executor import SafeApplyExecutor
from scripts.r16_live_fault_injection_validate import build_live_injection_summary


def patch_port_probes(monkeypatch) -> None:
    monkeypatch.setattr(
        runtime_controls,
        "_is_tcp_port_available",
        lambda host, port: True,
    )
    monkeypatch.setattr(
        SafeApplyExecutor,
        "_is_tcp_port_available",
        staticmethod(lambda host, port: True),
    )


def test_live_safe_fault_injection_recovers_and_reports(
    tmp_path: Path,
    monkeypatch,
) -> None:
    patch_port_probes(monkeypatch)

    summary = build_live_injection_summary(
        output_dir=tmp_path / "live",
        report_mode="rule",
        safe_event_types=["network_port"],
        high_risk_event_types=[],
    )

    assert summary["conclusion"] == "PASS", summary["failed_checks"]
    assert len(summary["safe_rows"]) == 1

    row = summary["safe_rows"][0]
    assert row["event_type"] == "network_port"
    assert row["initial_rerun_failed"] is True
    assert row["apply_success"] is True
    assert row["rerun_success"] is True
    assert row["recovered"] is True
    assert row["execution_result"] == "executed_recovered"
    assert row["rollback_result"] == "not_needed_recovered"
    assert row["dry_run"] is False
    assert row["allowed_to_execute"] is True
    assert row["current_value"] == row["expected_value"]
    assert row["backup_diff_ok"] is True
    assert row["safe_recovery_ok"] is True
    assert row["report_ok"] is True
    assert row["notification_ok"] is True
    assert row["notification_status"] == "recovered"

    for path in [*row["backup_paths"], *row["diff_paths"], *row["report_paths"]]:
        assert Path(path).exists(), path

    audit = json.loads(Path(row["audit_path"]).read_text(encoding="utf-8"))
    assert audit["strategy_layer"] == "safe_auto_recover"
    assert audit["fix_id"] == "fix-network-1"
    assert audit["apply_success"] is True
    assert audit["rerun_success"] is True
    assert audit["recovered"] is True

    alert = json.loads(Path(row["notification_payload_path"]).read_text(encoding="utf-8"))
    assert alert["status"] == "recovered"
    assert alert["event_type"] == "network_port"
    assert alert["execution_result"] == "executed_recovered"
    assert alert["recovery_audit_record"]["recovered"] is True


def test_live_non_safe_fault_injection_notifies_and_audits(
    tmp_path: Path,
    monkeypatch,
) -> None:
    patch_port_probes(monkeypatch)

    summary = build_live_injection_summary(
        output_dir=tmp_path / "live",
        report_mode="rule",
        safe_event_types=[],
        high_risk_event_types=["disk_full", "process_crash"],
    )

    assert summary["conclusion"] == "PASS", summary["failed_checks"]
    assert summary["safe_rows"] == []
    assert len(summary["high_risk_rows"]) == 2

    for row in summary["high_risk_rows"]:
        assert row["decision_action"] == "manual_escalation"
        assert row["strategy_layer"] == "manual_escalation"
        assert row["auto_recover_allowed"] is False
        assert row["allowed_to_execute"] is False
        assert row["would_execute"] is False
        assert row["apply_success"] is False
        assert row["rerun_success"] is False
        assert row["recovered"] is False
        assert row["execution_result"] == "not_run_r15_gate_blocked"
        assert row["manual_audit_ok"] is True
        assert row["report_ok"] is True
        assert row["notification_ok"] is True
        assert row["notification_status"] == "manual_escalation"

        audit = json.loads(Path(row["audit_path"]).read_text(encoding="utf-8"))
        assert audit["action"] == "manual_escalation"
        assert audit["operator_required"] is True
        assert audit["audit_required"] is True
        assert audit["execution_result"] == "not_run_r15_gate_blocked"

        alert = json.loads(
            Path(row["notification_payload_path"]).read_text(encoding="utf-8")
        )
        assert alert["status"] == "manual_escalation"
        assert alert["event_type"] == row["event_type"]
        assert alert["auto_recover_allowed"] is False
        assert alert["recovery_audit_record"]["action"] == "manual_escalation"


def test_live_reports_include_notification_stage(
    tmp_path: Path,
    monkeypatch,
) -> None:
    patch_port_probes(monkeypatch)

    summary = build_live_injection_summary(
        output_dir=tmp_path / "live",
        report_mode="rule",
        safe_event_types=["cache_write_failed"],
        high_risk_event_types=["permission_denied"],
    )

    assert summary["conclusion"] == "PASS", summary["failed_checks"]

    for row in [*summary["safe_rows"], *summary["high_risk_rows"]]:
        post_report = Path(row["post_report_path"])
        assert post_report.exists()
        text = post_report.read_text(encoding="utf-8")
        assert "Stage 6C 自动恢复结果" in text
        assert "NotificationAgent" in text
        assert "Stage 6D" in text or "通知" in text
        assert row["post_report_has_notification"] is True
