from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from detectors import ErrorEvent
from fixers.apply_executor import SafeApplyExecutor
from monitors.project_registry import PolicyConfig, ProjectConfig
from policies import RemediationPolicy
import recovery.auto_recovery_runtime_controls as runtime_controls
from recovery.auto_recovery_runtime_gate import evaluate_runtime_auto_recovery_gate
from safe_recovery.semantics import (
    SEMANTIC_SAFE_ENUM_DOWNGRADE,
    evaluate_safe_transition,
)


def make_project(
    tmp_path: Path,
    *,
    config: dict,
    dry_run: bool = False,
    allow_auto_apply: list[str],
) -> ProjectConfig:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "config.json").write_text(
        json.dumps(config, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return ProjectConfig(
        project_id="semantic_precheck",
        name="Semantic Precheck",
        mode="local",
        project_dir=str(project_dir),
        run_command="python app.py",
        policy=PolicyConfig(
            auto_recover=True,
            allow_auto_apply=allow_auto_apply,
            rollback_on_failure=True,
            auto_recovery_policy_enabled=True,
            auto_recovery_dry_run=dry_run,
        ),
    )


def make_event(event_type: str, issue_type: str) -> ErrorEvent:
    return ErrorEvent(
        event_type=event_type,
        issue_type=issue_type,
        severity="medium",
        summary=f"{event_type} evidence",
        source="test",
        raw_excerpt=f"{event_type} raw evidence",
        signature=f"semantic-{event_type}",
    )


def evaluate(event: ErrorEvent, project: ProjectConfig):
    decision = RemediationPolicy().decide(event=event, project=project)
    return evaluate_runtime_auto_recovery_gate(
        event=event,
        project=project,
        remediation_decision=decision,
    )


def test_gpu_batch_size_must_decrease(tmp_path: Path) -> None:
    project = make_project(
        tmp_path,
        config={"batch_size": 2},
        allow_auto_apply=["fix-gpu-1"],
    )

    result = evaluate(make_event("gpu_oom", "gpu"), project)

    assert result.auto_recover_allowed
    assert not result.allowed_to_execute
    assert result.downgrade_reason == "unsafe_semantic_transition"
    edit = result.precheck_result["unsafe_planned_edits"][0]
    assert edit["field_path"] == "batch_size"
    assert edit["semantic_rule"] == "lower_int"
    assert edit["semantic_reason"] == "integer_value_would_not_decrease"


def test_worker_concurrency_must_decrease(tmp_path: Path) -> None:
    project = make_project(
        tmp_path,
        config={"worker_concurrency": 1},
        allow_auto_apply=["fix-worker-1"],
    )

    result = evaluate(make_event("worker_overload", "worker_overload"), project)

    assert result.auto_recover_allowed
    assert not result.allowed_to_execute
    assert result.downgrade_reason == "unsafe_semantic_transition"
    edit = result.precheck_result["unsafe_planned_edits"][0]
    assert edit["field_path"] == "worker_concurrency"
    assert edit["semantic_reason"] == "integer_value_would_not_decrease"


def test_boolean_disable_no_op_is_audited_without_execution(tmp_path: Path) -> None:
    project = make_project(
        tmp_path,
        config={"cache_enabled": False},
        allow_auto_apply=["fix-cache-1"],
    )

    result = evaluate(make_event("cache_write_failed", "cache"), project)

    assert result.auto_recover_allowed
    assert result.precheck_result["no_op"] is True
    assert result.precheck_result["semantic_status"] == "no_op"
    assert not result.allowed_to_execute
    assert result.downgrade_reason == "no_op_already_safe"
    assert result.audit_record["execution_result"] == "not_run_r15_no_op"


def test_network_port_target_must_be_available(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        runtime_controls,
        "_is_tcp_port_available",
        lambda host, port: False,
    )
    project = make_project(
        tmp_path,
        config={"metrics_host": "127.0.0.1", "metrics_port": 9000},
        allow_auto_apply=["fix-network-1"],
    )

    result = evaluate(make_event("network_port", "network_port"), project)

    assert result.auto_recover_allowed
    assert not result.allowed_to_execute
    assert result.downgrade_reason == "unsafe_semantic_transition"
    edit = result.precheck_result["unsafe_planned_edits"][0]
    assert edit["field_path"] == "metrics_port"
    assert edit["semantic_reason"] == "target_port_not_available"
    assert edit["target_port_available"] is False


def test_direct_apply_cannot_raise_gpu_batch_size(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    session_dir = tmp_path / "session"
    project_dir.mkdir()
    config_path = project_dir / "config.json"
    config_path.write_text(
        json.dumps({"batch_size": 2}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    result = SafeApplyExecutor(
        project_dir=str(project_dir),
        session_dir=str(session_dir),
    ).apply("fix-gpu-1")

    assert not result.success
    assert result.edit_results[0].semantic_status == "unsafe"
    assert result.edit_results[0].semantic_reason == "integer_value_would_not_decrease"
    assert json.loads(config_path.read_text(encoding="utf-8"))["batch_size"] == 2
    assert not (session_dir / "applied_fixes.json").exists()


def test_direct_apply_no_op_does_not_create_apply_record(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    session_dir = tmp_path / "session"
    project_dir.mkdir()
    config_path = project_dir / "config.json"
    config_path.write_text(
        json.dumps({"cache_enabled": False}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    result = SafeApplyExecutor(
        project_dir=str(project_dir),
        session_dir=str(session_dir),
    ).apply("fix-cache-1")

    assert result.success
    assert result.edit_results[0].no_op
    assert result.edit_results[0].semantic_status == "no_op"
    assert json.loads(config_path.read_text(encoding="utf-8"))["cache_enabled"] is False
    assert not (session_dir / "applied_fixes.json").exists()


def test_safe_enum_downgrade_allows_only_local_mode_targets() -> None:
    memory_result = evaluate_safe_transition(
        semantic_rule=SEMANTIC_SAFE_ENUM_DOWNGRADE,
        old_value="redis",
        new_value="memory",
    )
    console_result = evaluate_safe_transition(
        semantic_rule=SEMANTIC_SAFE_ENUM_DOWNGRADE,
        old_value="webhook",
        new_value="console",
    )

    assert memory_result["actionable"] is True
    assert memory_result["semantic_safe"] is True
    assert memory_result["semantic_reason"] == "safe_enum_downgrade_target_allowlisted"
    assert console_result["actionable"] is True
    assert console_result["semantic_safe"] is True


def test_safe_enum_downgrade_rejects_remote_or_non_string_targets() -> None:
    remote_result = evaluate_safe_transition(
        semantic_rule=SEMANTIC_SAFE_ENUM_DOWNGRADE,
        old_value="memory",
        new_value="remote",
    )
    non_string_result = evaluate_safe_transition(
        semantic_rule=SEMANTIC_SAFE_ENUM_DOWNGRADE,
        old_value="redis",
        new_value=True,
    )

    assert remote_result["semantic_status"] == "unsafe"
    assert remote_result["semantic_reason"] == "safe_enum_target_not_allowlisted"
    assert non_string_result["semantic_status"] == "unsafe"
    assert (
        non_string_result["semantic_reason"]
        == "safe_enum_downgrade_requires_string_values"
    )
