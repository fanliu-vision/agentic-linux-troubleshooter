from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from detectors import ErrorEvent
from fixers.apply_executor import SafeApplyExecutor
from monitors.project_registry import PolicyConfig, ProjectConfig
from policies import RemediationPolicy
from recovery.auto_recovery_runtime_gate import evaluate_runtime_auto_recovery_gate


SAFE_DOMAIN_ISOLATED_CASES = [
    (
        "optional_integration_failed",
        "optional_integration",
        "fix-optional-integration-1",
        {"optional_webhook_enabled": True, "untouched": "keep"},
        "optional_webhook_enabled",
        False,
    ),
    (
        "notification_sink_failed",
        "notification_sink",
        "fix-notification-sink-1",
        {"notification": {"webhook_enabled": True}, "untouched": "keep"},
        "notification.webhook_enabled",
        False,
    ),
    (
        "queue_backpressure",
        "queue_backpressure",
        "fix-queue-backpressure-1",
        {"prefetch_count": 64, "untouched": "keep"},
        "prefetch_count",
        2,
    ),
    (
        "optional_cache_backend_failed",
        "optional_cache_backend",
        "fix-cache-backend-1",
        {"cache": {"backend": "redis"}, "untouched": "keep"},
        "cache.backend",
        "memory",
    ),
    (
        "optional_service_unavailable",
        "optional_service",
        "fix-optional-service-1",
        {
            "optional_services": {"enrichment": {"enabled": True}},
            "untouched": "keep",
        },
        "optional_services.enrichment.enabled",
        False,
    ),
    (
        "observability_export_failed",
        "observability_export",
        "fix-observability-export-1",
        {"observability": {"exporter_mode": "otlp"}, "untouched": "keep"},
        "observability.exporter_mode",
        "local",
    ),
]


def make_project(
    tmp_path: Path,
    *,
    config: dict[str, Any],
    fix_id: str,
    dry_run: bool,
) -> tuple[ProjectConfig, Path]:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    config_path = project_dir / "config.json"
    config_path.write_text(
        json.dumps(config, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    project = ProjectConfig(
        project_id="r16_safe_isolated",
        name="R16 Safe Isolated",
        mode="local",
        project_dir=str(project_dir),
        run_command="python app.py",
        policy=PolicyConfig(
            auto_recover=True,
            allow_auto_apply=[fix_id],
            rollback_on_failure=True,
            auto_recovery_policy_enabled=True,
            auto_recovery_dry_run=dry_run,
        ),
    )
    return project, config_path


def make_event(event_type: str, issue_type: str) -> ErrorEvent:
    return ErrorEvent(
        event_type=event_type,
        issue_type=issue_type,
        severity="medium",
        summary=f"{event_type} isolated evidence",
        source="isolated-test",
        raw_excerpt=f"{event_type} isolated raw evidence",
        signature=f"r16-safe-isolated-{event_type}",
    )


def evaluate(event: ErrorEvent, project: ProjectConfig):
    decision = RemediationPolicy().decide(event=event, project=project)
    return evaluate_runtime_auto_recovery_gate(
        event=event,
        project=project,
        remediation_decision=decision,
    )


@pytest.mark.parametrize(
    (
        "event_type",
        "issue_type",
        "fix_id",
        "config",
        "field_path",
        "expected_value",
    ),
    SAFE_DOMAIN_ISOLATED_CASES,
)
def test_safe_domain_default_dry_run_only_audits_without_json_write(
    tmp_path: Path,
    event_type: str,
    issue_type: str,
    fix_id: str,
    config: dict[str, Any],
    field_path: str,
    expected_value: Any,
) -> None:
    original = deepcopy(config)
    project, config_path = make_project(
        tmp_path,
        config=config,
        fix_id=fix_id,
        dry_run=True,
    )

    gate = evaluate(make_event(event_type, issue_type), project)

    assert gate.auto_recover_allowed
    assert gate.dry_run
    assert gate.is_candidate
    assert not gate.allowed_to_execute
    assert not gate.would_execute
    assert gate.downgrade_reason == "r15_dry_run"
    assert gate.audit_record["execution_result"] == "not_run_r15_dry_run"
    assert gate.audit_record["precheck_result"]["actionable_edit_count"] == 1
    assert _get_nested_value(json.loads(config_path.read_text()), field_path) != expected_value
    assert json.loads(config_path.read_text(encoding="utf-8")) == original


@pytest.mark.parametrize(
    (
        "event_type",
        "issue_type",
        "fix_id",
        "config",
        "field_path",
        "expected_value",
    ),
    SAFE_DOMAIN_ISOLATED_CASES,
)
def test_safe_domain_live_mode_writes_only_json_with_backup_diff_and_rollback(
    tmp_path: Path,
    event_type: str,
    issue_type: str,
    fix_id: str,
    config: dict[str, Any],
    field_path: str,
    expected_value: Any,
) -> None:
    original = deepcopy(config)
    project, config_path = make_project(
        tmp_path,
        config=config,
        fix_id=fix_id,
        dry_run=False,
    )

    gate = evaluate(make_event(event_type, issue_type), project)

    assert gate.auto_recover_allowed
    assert not gate.dry_run
    assert gate.allowed_to_execute
    assert gate.would_execute
    assert gate.audit_record["execution_result"] == "would_run_r15_live"

    session_dir = tmp_path / "session"
    executor = SafeApplyExecutor(
        project_dir=project.project_dir,
        session_dir=str(session_dir),
    )
    apply_result = executor.apply(fix_id)

    assert apply_result.success
    assert apply_result.edit_results[0].field_path == field_path
    assert apply_result.edit_results[0].backup_path
    assert apply_result.edit_results[0].diff_path
    assert Path(apply_result.edit_results[0].backup_path).exists()
    assert Path(apply_result.edit_results[0].diff_path).exists()

    updated = json.loads(config_path.read_text(encoding="utf-8"))
    expected_config = deepcopy(original)
    _set_nested_value(expected_config, field_path, expected_value)
    assert updated == expected_config

    record_path = session_dir / "applied_fixes.json"
    records = json.loads(record_path.read_text(encoding="utf-8"))
    assert records[-1]["fix_id"] == fix_id
    assert records[-1]["edits"][0]["field_path"] == field_path

    rollback_result = executor.rollback_latest()

    assert rollback_result.success
    assert json.loads(config_path.read_text(encoding="utf-8")) == original


def _get_nested_value(data: dict[str, Any], field_path: str) -> Any:
    current: Any = data
    for part in field_path.split("."):
        current = current[part]
    return current


def _set_nested_value(data: dict[str, Any], field_path: str, value: Any) -> None:
    current: Any = data
    parts = field_path.split(".")
    for part in parts[:-1]:
        current = current[part]
    current[parts[-1]] = value
