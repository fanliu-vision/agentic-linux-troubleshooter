from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import recovery.auto_recovery_runtime_controls as runtime_controls
from scripts.r16_isolated_fault_injection_validate import (
    HIGH_RISK_INJECTION_LOGS,
    SAFE_INJECTION_LOGS,
    build_isolated_injection_summary,
)
from safe_recovery.registry import iter_safe_recovery_specs


def test_isolated_fault_injection_summary_passes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        runtime_controls,
        "_is_tcp_port_available",
        lambda host, port: True,
    )

    summary = build_isolated_injection_summary(tmp_path / "r16-injection")

    assert summary["conclusion"] == "PASS", summary["failed_checks"]
    assert summary["auto_recovery_dry_run"] is True
    assert summary["safe_domain_count"] == len(list(iter_safe_recovery_specs()))
    assert summary["high_risk_count"] == len(HIGH_RISK_INJECTION_LOGS)
    assert summary["failed_checks"] == []


def test_safe_domains_detect_fix_id_and_only_emit_dry_run_audit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        runtime_controls,
        "_is_tcp_port_available",
        lambda host, port: True,
    )

    summary = build_isolated_injection_summary(tmp_path / "r16-injection")

    for row in summary["safe_rows"]:
        assert row["event_type"] in SAFE_INJECTION_LOGS
        assert row["detector_ok"]
        assert row["fix_id_ok"]
        assert row["decision_action"] == "auto_recover"
        assert row["strategy_layer"] == "safe_auto_recover"
        assert row["dry_run"] is True
        assert row["auto_recover_allowed"] is True
        assert row["allowed_to_execute"] is False
        assert row["would_execute"] is False
        assert row["execution_result"] == "not_run_r15_dry_run"
        assert row["rollback_result"] == "not_run_before_execution"
        assert row["precheck_passed"]
        assert row["actionable_edit_count"] == 1
        assert row["backup_plan_present"]
        assert row["diff_plan_present"]
        assert row["rollback_plan_ok"]
        assert row["no_write_ok"]
        assert row["backup_or_diff_files_created"] == []
        assert row["config_unchanged"]

        audit = json.loads(Path(row["audit_path"]).read_text(encoding="utf-8"))
        precheck = audit["precheck_result"]
        assert audit["dry_run"] is True
        assert audit["allowed_to_execute"] is False
        assert precheck["rollback_plan"]["available"] is True
        assert precheck["rollback_plan"]["backup_created_before_write"] is True
        assert precheck["actionable_planned_edits"][0]["field_path"] == row[
            "planned_field_path"
        ]


def test_high_risk_domains_remain_manual_or_diagnose_only(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        runtime_controls,
        "_is_tcp_port_available",
        lambda host, port: True,
    )

    summary = build_isolated_injection_summary(tmp_path / "r16-injection")

    for row in summary["high_risk_rows"]:
        assert row["event_type"] in HIGH_RISK_INJECTION_LOGS
        assert row["detector_ok"], row
        assert row["fallback_ok"], row
        assert row["decision_action"] in {"manual_escalation", "report_only"}
        assert row["strategy_layer"] in {
            "manual_escalation",
            "diagnose_only",
            "disabled",
        }
        assert row["auto_recover_allowed"] is False
        assert row["allowed_to_execute"] is False
        assert row["would_execute"] is False
        assert row["execution_result"] == "not_run_r15_gate_blocked"

    unknown = summary["unknown_row"]
    assert unknown["passed"]
    assert unknown["decision_action"] == "report_only"
    assert unknown["strategy_layer"] == "diagnose_only"
    assert unknown["auto_recover_allowed"] is False
    assert unknown["allowed_to_execute"] is False


def test_injection_uses_real_shape_project_files(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        runtime_controls,
        "_is_tcp_port_available",
        lambda host, port: True,
    )

    summary = build_isolated_injection_summary(tmp_path / "r16-injection")
    all_rows = summary["safe_rows"] + summary["high_risk_rows"] + [
        summary["unknown_row"]
    ]

    for row in all_rows:
        project_dir = Path(row["project_dir"])
        assert project_dir.exists()
        assert Path(row["log_path"]).exists()
        assert (project_dir / "config.json").exists()
        assert (project_dir / "state" / "project_status.json").exists()
        assert (project_dir / "state" / "events.jsonl").exists()
        assert (project_dir / "etc" / "demo-service" / "service.conf").exists()
        assert (
            project_dir / "var" / "lib" / "demo-service" / "runtime_state.json"
        ).exists()

        status = json.loads(
            (project_dir / "state" / "project_status.json").read_text(
                encoding="utf-8"
            )
        )
        assert status["auto_recovery_dry_run"] is True
        assert status["event_type"] == row["event_type"]
