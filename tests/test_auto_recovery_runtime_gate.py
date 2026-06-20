from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from detectors import ErrorEvent
from monitors.project_registry import PolicyConfig, ProjectConfig
from policies import RemediationPolicy
from recovery.auto_recovery_runtime_gate import evaluate_runtime_auto_recovery_gate


def make_project(
    *,
    dry_run: bool = True,
    allow_auto_apply: list[str] | None = None,
    rollback_on_failure: bool = True,
    policy_enabled: bool = True,
) -> ProjectConfig:
    project_dir = Path(tempfile.mkdtemp(prefix="r15-runtime-gate-"))
    (project_dir / "config.json").write_text(
        json.dumps(
            {
                "metrics_port": 9000,
                "batch_size": 16,
                "simulate_disk_full": True,
                "simulate_python_env_mismatch": True,
                "worker_concurrency": 8,
            }
        ),
        encoding="utf-8",
    )
    return ProjectConfig(
        project_id="runtime_gate",
        name="Runtime Gate",
        mode="local",
        project_dir=str(project_dir),
        run_command="python app.py",
        policy=PolicyConfig(
            auto_recover=True,
            allow_auto_apply=allow_auto_apply
            if allow_auto_apply is not None
            else ["fix-network-1", "fix-gpu-1"],
            rollback_on_failure=rollback_on_failure,
            auto_recovery_policy_enabled=policy_enabled,
            auto_recovery_dry_run=dry_run,
        ),
    )


def make_event(event_type: str, issue_type: str) -> ErrorEvent:
    return ErrorEvent(
        event_type=event_type,
        issue_type=issue_type,
        severity="high",
        summary=f"{event_type} summary",
        source="test",
        raw_excerpt=f"{event_type} evidence",
        signature=f"runtime-gate-{event_type}",
    )


def evaluate(event: ErrorEvent, project: ProjectConfig):
    decision = RemediationPolicy().decide(event=event, project=project)
    return evaluate_runtime_auto_recovery_gate(
        event=event,
        project=project,
        remediation_decision=decision,
    )


def test_default_runtime_gate_dry_run_blocks_existing_safe_candidate() -> None:
    result = evaluate(
        make_event("network_port", "network_port"),
        make_project(dry_run=True),
    )

    assert result.auto_recover_allowed
    assert result.dry_run
    assert result.is_candidate
    assert not result.allowed_to_execute
    assert not result.would_execute
    assert result.downgrade_reason == "r15_dry_run"
    assert result.audit_record["execution_result"] == "not_run_r15_dry_run"


def test_runtime_gate_allows_live_when_dry_run_is_explicitly_disabled() -> None:
    result = evaluate(
        make_event("network_port", "network_port"),
        make_project(dry_run=False),
    )

    assert result.auto_recover_allowed
    assert not result.dry_run
    assert result.allowed_to_execute
    assert result.would_execute
    assert result.selected_fix_id == "fix-network-1"
    assert result.audit_record["execution_result"] == "would_run_r15_live"
    assert result.precheck_result["config_read_status"] == "read_ok"
    assert result.precheck_result["planned_edits"] == [
        {
            "field_path": "metrics_port",
            "old_value_available": True,
            "old_value": 9000,
            "new_value": 9101,
            "already_target_value": False,
        }
    ]
    assert result.precheck_result["rollback_plan"]["available"] is True


def test_runtime_gate_allows_existing_gpu_fix_when_live_enabled() -> None:
    result = evaluate(
        make_event("gpu_oom", "gpu"),
        make_project(dry_run=False),
    )

    assert result.auto_recover_allowed
    assert result.allowed_to_execute
    assert result.selected_fix_id == "fix-gpu-1"


def test_runtime_gate_blocks_python_env_even_when_legacy_policy_allows_it() -> None:
    project = make_project(
        dry_run=False,
        allow_auto_apply=["fix-network-1", "fix-gpu-1", "fix-python-1"],
    )
    event = make_event("python_env", "python_env")
    legacy_decision = RemediationPolicy().decide(event=event, project=project)

    assert legacy_decision.is_auto_recover

    result = evaluate_runtime_auto_recovery_gate(
        event=event,
        project=project,
        remediation_decision=legacy_decision,
    )

    assert not result.auto_recover_allowed
    assert not result.allowed_to_execute
    assert result.strategy_layer == "manual_escalation"
    assert result.downgrade_reason == "event_type_defaults_to_manual_escalation"


def test_runtime_gate_requires_rollback_for_live_execution() -> None:
    result = evaluate(
        make_event("network_port", "network_port"),
        make_project(dry_run=False, rollback_on_failure=False),
    )

    assert result.auto_recover_allowed
    assert not result.allowed_to_execute
    assert not result.rollback_available
    assert "rollback_disabled" in result.precheck_result["reasons"]
    assert "rollback_plan_unavailable" in result.precheck_result["reasons"]


def test_runtime_gate_policy_disabled_blocks_legacy_passthrough() -> None:
    result = evaluate(
        make_event("network_port", "network_port"),
        make_project(dry_run=False, policy_enabled=False),
    )

    assert result.strategy_layer == "disabled"
    assert result.downgrade_reason == "r15_policy_disabled"
    assert not result.auto_recover_allowed
    assert not result.allowed_to_execute
    assert result.audit_record["execution_result"] == "not_run_r15_gate_blocked"
