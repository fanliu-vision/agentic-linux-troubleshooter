from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import recovery.auto_recovery_runtime_controls as runtime_controls
from detectors import ErrorEventDetector
from monitors.project_registry import PolicyConfig, ProjectConfig
from policies import RemediationPolicy
from recovery.auto_recovery_runtime_gate import evaluate_runtime_auto_recovery_gate
from safe_recovery.registry import SAFE_RECOVERY_FIX_IDS, iter_safe_recovery_specs
from safe_recovery.semantics import (
    SEMANTIC_DISABLE_BOOL,
    SEMANTIC_LOWER_INT,
    SEMANTIC_PORT_AVAILABLE,
    SEMANTIC_SAFE_ENUM_DOWNGRADE,
)
from scripts.dump_recovery_policy_matrix import build_policy_matrix


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "regression_logs"
EXPECTED_CASES_PATH = FIXTURE_DIR / "expected_cases.json"


EXPECTED_SNAPSHOT_BY_CASE_ID = {
    "network_port_basic": ("auto_recover", "safe_auto_recover", "report_only"),
    "network_port_connectivity_negative": (
        "manual_escalation",
        "manual_escalation",
        "manual_escalation",
    ),
    "gpu_oom_basic": ("auto_recover", "safe_auto_recover", "report_only"),
    "gpu_oom_host_resource_negative": (
        "manual_escalation",
        "manual_escalation",
        "manual_escalation",
    ),
    "cache_write_failed_basic": ("auto_recover", "safe_auto_recover", "report_only"),
    "cache_write_failed_disk_negative": (
        "manual_escalation",
        "manual_escalation",
        "manual_escalation",
    ),
    "optional_dependency_missing_basic": (
        "auto_recover",
        "safe_auto_recover",
        "report_only",
    ),
    "optional_dependency_missing_python_env_negative": (
        "auto_recover",
        "manual_escalation",
        "manual_escalation",
    ),
    "worker_overload_basic": ("auto_recover", "safe_auto_recover", "report_only"),
    "worker_overload_process_crash_negative": (
        "manual_escalation",
        "manual_escalation",
        "manual_escalation",
    ),
    "optional_integration_failed_basic": (
        "auto_recover",
        "safe_auto_recover",
        "report_only",
    ),
    "notification_sink_failed_basic": (
        "auto_recover",
        "safe_auto_recover",
        "report_only",
    ),
    "queue_backpressure_basic": ("auto_recover", "safe_auto_recover", "report_only"),
    "optional_integration_core_dependency_negative": (
        "auto_recover",
        "manual_escalation",
        "manual_escalation",
    ),
    "notification_sink_auth_negative": (
        "manual_escalation",
        "manual_escalation",
        "manual_escalation",
    ),
    "queue_backpressure_dependency_service_negative": (
        "manual_escalation",
        "manual_escalation",
        "manual_escalation",
    ),
    "optional_cache_backend_failed_basic": (
        "auto_recover",
        "safe_auto_recover",
        "report_only",
    ),
    "optional_cache_backend_disk_negative": (
        "manual_escalation",
        "manual_escalation",
        "manual_escalation",
    ),
    "optional_cache_backend_dependency_negative": (
        "manual_escalation",
        "manual_escalation",
        "manual_escalation",
    ),
    "optional_service_unavailable_basic": (
        "auto_recover",
        "safe_auto_recover",
        "report_only",
    ),
    "optional_service_dependency_negative": (
        "manual_escalation",
        "manual_escalation",
        "manual_escalation",
    ),
    "observability_export_failed_basic": (
        "auto_recover",
        "safe_auto_recover",
        "report_only",
    ),
    "observability_export_network_negative": (
        "manual_escalation",
        "manual_escalation",
        "manual_escalation",
    ),
    "observability_export_auth_negative": (
        "manual_escalation",
        "manual_escalation",
        "manual_escalation",
    ),
    "disk_full_basic": ("manual_escalation", "manual_escalation", "manual_escalation"),
    "python_env_basic": ("auto_recover", "manual_escalation", "manual_escalation"),
    "slurm_basic": ("manual_escalation", "manual_escalation", "manual_escalation"),
    "process_kill_unsupported": (
        "manual_escalation",
        "manual_escalation",
        "manual_escalation",
    ),
    "permission_denied_unsupported": (
        "manual_escalation",
        "manual_escalation",
        "manual_escalation",
    ),
    "process_crash_basic": (
        "manual_escalation",
        "manual_escalation",
        "manual_escalation",
    ),
    "container_k8s_basic": (
        "manual_escalation",
        "manual_escalation",
        "manual_escalation",
    ),
    "host_resource_basic": (
        "manual_escalation",
        "manual_escalation",
        "manual_escalation",
    ),
    "network_connectivity_basic": (
        "manual_escalation",
        "manual_escalation",
        "manual_escalation",
    ),
    "dependency_service_basic": (
        "manual_escalation",
        "manual_escalation",
        "manual_escalation",
    ),
    "config_path_basic": ("manual_escalation", "diagnose_only", "report_only"),
    "model_path_basic": ("manual_escalation", "diagnose_only", "report_only"),
    "sudo_required_basic": (
        "manual_escalation",
        "manual_escalation",
        "manual_escalation",
    ),
    "traceback_basic": ("report_only", "diagnose_only", "report_only"),
    "config_error_basic": (
        "manual_escalation",
        "manual_escalation",
        "manual_escalation",
    ),
    "auth_cert_basic": ("manual_escalation", "manual_escalation", "manual_escalation"),
    "benign_info": ("no_event", "not_evaluated", "no_event"),
}


@pytest.fixture(autouse=True)
def assume_target_port_available(monkeypatch) -> None:
    monkeypatch.setattr(
        runtime_controls,
        "_is_tcp_port_available",
        lambda host, port: True,
    )


def load_expected_cases() -> list[dict[str, Any]]:
    return json.loads(EXPECTED_CASES_PATH.read_text(encoding="utf-8"))


def test_policy_matrix_exposes_safe_manual_unknown_and_legacy_only_domains() -> None:
    rows = {row.event_type: row for row in build_policy_matrix()}

    assert rows["network_port"].registry_strategy == "safe_auto_recover"
    assert rows["network_port"].legacy_action == "auto_recover"
    assert rows["network_port"].runtime_strategy == "safe_auto_recover"
    assert rows["network_port"].project_allowlist == "allowlisted"
    assert rows["network_port"].local_executor_support is True
    assert rows["network_port"].remote_executor_support is True

    assert rows["disk_full"].registry_strategy == "manual_escalation"
    assert rows["disk_full"].legacy_action == "manual_escalation"
    assert rows["disk_full"].runtime_strategy == "manual_escalation"
    assert "manual_outside_registry" not in rows["disk_full"].drift_notes

    assert rows["unknown_future_domain"].legacy_action == "report_only"
    assert rows["unknown_future_domain"].runtime_strategy == "diagnose_only"
    assert "unknown_event_probe" in rows["unknown_future_domain"].drift_notes

    assert rows["config_path"].registry_strategy == "diagnose_only"
    assert rows["config_path"].registry_fix_id == "fix-config-path-1"
    assert rows["config_path"].legacy_fix_id == "fix-config-path-1"
    assert rows["config_path"].runtime_strategy == "diagnose_only"
    assert "legacy_auto_but_runtime_blocks" in rows["config_path"].drift_notes
    assert "legacy_only_fix_mapping" not in rows["config_path"].drift_notes

    assert rows["python_env"].registry_strategy == "manual_escalation"
    assert rows["python_env"].registry_fix_id == "fix-python-1"
    assert rows["python_env"].legacy_action == "auto_recover"
    assert rows["python_env"].legacy_fix_id == "fix-python-1"
    assert rows["python_env"].runtime_strategy == "manual_escalation"
    assert "legacy_auto_but_runtime_blocks" in rows["python_env"].drift_notes


@pytest.mark.parametrize(
    "case",
    load_expected_cases(),
    ids=lambda case: case["case_id"],
)
def test_fixture_final_recovery_action_snapshot(
    tmp_path: Path,
    case: dict[str, Any],
) -> None:
    assert set(EXPECTED_SNAPSHOT_BY_CASE_ID) == {
        item["case_id"] for item in load_expected_cases()
    }

    log_path = FIXTURE_DIR / case["log_file"]
    events = ErrorEventDetector().detect(
        log_path.read_text(encoding="utf-8"),
        source=f"fixture:{case['log_file']}",
    )
    expected = EXPECTED_SNAPSHOT_BY_CASE_ID[case["case_id"]]

    if case["expected_event_type"] is None:
        assert events == []
        assert expected == ("no_event", "not_evaluated", "no_event")
        return

    matching_events = [
        event for event in events if event.event_type == case["expected_event_type"]
    ]
    assert matching_events, [event.event_type for event in events]
    event = matching_events[0]

    project = make_snapshot_project(tmp_path)
    legacy_decision = RemediationPolicy().decide(event=event, project=project)
    gate = evaluate_runtime_auto_recovery_gate(
        event=event,
        project=project,
        remediation_decision=legacy_decision,
    )

    expected_legacy_action, expected_r15_strategy, expected_final_action = expected
    assert legacy_decision.action == expected_legacy_action
    assert gate.strategy_layer == expected_r15_strategy
    assert _final_runner_action(legacy_decision, gate) == expected_final_action


def make_snapshot_project(tmp_path: Path) -> ProjectConfig:
    project_dir = tmp_path / "snapshot_project"
    project_dir.mkdir(exist_ok=True)
    (project_dir / "config.json").write_text(
        json.dumps(_snapshot_config(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return ProjectConfig(
        project_id="recovery_policy_snapshot",
        name="Recovery Policy Snapshot",
        mode="local",
        project_dir=str(project_dir),
        run_command="python app.py",
        policy=PolicyConfig(
            auto_recover=True,
            allow_auto_apply=sorted(SAFE_RECOVERY_FIX_IDS | {"fix-python-1"}),
            rollback_on_failure=True,
            auto_recovery_policy_enabled=True,
            auto_recovery_dry_run=True,
        ),
    )


def _snapshot_config() -> dict[str, Any]:
    data: dict[str, Any] = {}
    for spec in iter_safe_recovery_specs():
        candidate = spec.candidates[0]
        _set_nested(data, candidate.field_path, _old_value_for_candidate(candidate))
    return data


def _old_value_for_candidate(candidate: Any) -> Any:
    if candidate.semantic_rule == SEMANTIC_DISABLE_BOOL:
        return True
    if candidate.semantic_rule == SEMANTIC_LOWER_INT:
        return int(candidate.new_value) + 8
    if candidate.semantic_rule == SEMANTIC_PORT_AVAILABLE:
        return int(candidate.new_value) - 101
    if candidate.semantic_rule == SEMANTIC_SAFE_ENUM_DOWNGRADE:
        return "remote"
    return "enabled"


def _set_nested(data: dict[str, Any], field_path: str, value: Any) -> None:
    current = data
    parts = field_path.split(".")
    for part in parts[:-1]:
        current = current.setdefault(part, {})
    current[parts[-1]] = value


def _final_runner_action(legacy_decision: Any, gate: Any) -> str:
    if gate.allowed_to_execute:
        return "auto_recover"
    return "manual_escalation" if gate.operator_required else "report_only"
