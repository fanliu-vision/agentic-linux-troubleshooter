from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from detectors import ErrorEvent, ErrorEventDetector
from fixers.apply_executor import SafeApplyExecutor
from monitors.project_registry import PolicyConfig, ProjectConfig
from policies import RemediationPolicy
from policies.auto_recovery_policy_dry_run import run_policy_dry_run
from recovery.auto_recovery_runtime_gate import (
    build_runtime_auto_recovery_policy,
    evaluate_runtime_auto_recovery_gate,
)
from recovery.guarded_auto_recover_dry_run import (
    evaluate_guarded_auto_recover_dry_run,
)


SAFE_EXPANSION_CASES = [
    (
        "cache_write_failed",
        "cache",
        "fix-cache-1",
        """
[cache] WARNING: failed to write cache file /tmp/acme_order_cache/features.bin
OSError: [Errno 28] No space left on device: '/tmp/acme_order_cache/features.bin'
[cache] fallback: continue with in-memory feature cache
""",
    ),
    (
        "optional_dependency_missing",
        "optional_dependency",
        "fix-optional-dep-1",
        """
[env] optional dependency internal_risk_sdk missing
ModuleNotFoundError: No module named 'acme_internal_sdk'
[fallback] internal risk SDK unavailable, continue with local rule engine.
""",
    ),
    (
        "worker_overload",
        "worker_overload",
        "fix-worker-1",
        """
[worker] worker overload: worker_concurrency=8 is too high for startup queue
[worker] worker pool exhausted; concurrency too high
""",
    ),
]


def make_project(
    *,
    dry_run: bool = True,
    allow_auto_apply: list[str] | None = None,
    project_dir: str = ".",
) -> ProjectConfig:
    return ProjectConfig(
        project_id="safe_expansion",
        name="Safe Expansion",
        mode="local",
        project_dir=project_dir,
        run_command="python app.py",
        policy=PolicyConfig(
            auto_recover=True,
            allow_auto_apply=allow_auto_apply
            if allow_auto_apply is not None
            else [
                "fix-network-1",
                "fix-gpu-1",
                "fix-cache-1",
                "fix-optional-dep-1",
                "fix-worker-1",
            ],
            rollback_on_failure=True,
            auto_recovery_policy_enabled=True,
            auto_recovery_dry_run=dry_run,
        ),
    )


def make_event(event_type: str, issue_type: str, raw_excerpt: str = "evidence") -> ErrorEvent:
    return ErrorEvent(
        event_type=event_type,
        issue_type=issue_type,
        severity="medium",
        summary=f"{event_type} summary",
        source="test",
        raw_excerpt=raw_excerpt,
        signature=f"safe-expansion-{event_type}",
    )


@pytest.mark.parametrize(
    ("event_type", "issue_type", "_fix_id", "text"),
    SAFE_EXPANSION_CASES,
)
def test_detector_classifies_new_safe_domains_before_generic_domains(
    event_type: str,
    issue_type: str,
    _fix_id: str,
    text: str,
) -> None:
    events = ErrorEventDetector().detect(text, source="test.log")
    event_types = {event.event_type for event in events}
    issue_types = {event.issue_type for event in events}

    assert event_type in event_types
    assert issue_type in issue_types
    assert "disk_full" not in event_types
    assert "python_env" not in event_types
    assert "host_resource" not in event_types


@pytest.mark.parametrize(
    ("event_type", "issue_type", "fix_id", "_text"),
    SAFE_EXPANSION_CASES,
)
def test_remediation_policy_allows_new_domains_only_with_explicit_allowlist(
    event_type: str,
    issue_type: str,
    fix_id: str,
    _text: str,
) -> None:
    allowed_decision = RemediationPolicy().decide(
        make_event(event_type, issue_type),
        make_project(allow_auto_apply=[fix_id]),
    )
    blocked_decision = RemediationPolicy().decide(
        make_event(event_type, issue_type),
        make_project(allow_auto_apply=[]),
    )

    assert allowed_decision.action == "auto_recover"
    assert allowed_decision.fix_id == fix_id
    assert allowed_decision.is_auto_recover
    assert blocked_decision.action == "manual_escalation"
    assert not blocked_decision.is_auto_recover


@pytest.mark.parametrize(
    ("event_type", "issue_type", "fix_id", "_text"),
    SAFE_EXPANSION_CASES,
)
def test_runtime_gate_allows_new_domains_when_live_enabled(
    event_type: str,
    issue_type: str,
    fix_id: str,
    _text: str,
) -> None:
    project = make_project(dry_run=False, allow_auto_apply=[fix_id])
    event = make_event(event_type, issue_type)
    decision = RemediationPolicy().decide(event, project)
    gate = evaluate_runtime_auto_recovery_gate(
        event=event,
        project=project,
        remediation_decision=decision,
    )

    assert gate.auto_recover_allowed
    assert gate.allowed_to_execute
    assert gate.would_execute
    assert not gate.dry_run
    assert gate.selected_fix_id == fix_id
    assert gate.audit_record["execution_result"] == "would_run_r15_live"


@pytest.mark.parametrize(
    ("event_type", "issue_type", "fix_id", "_text"),
    SAFE_EXPANSION_CASES,
)
def test_runtime_gate_keeps_new_domains_dry_run_when_configured(
    event_type: str,
    issue_type: str,
    fix_id: str,
    _text: str,
) -> None:
    project = make_project(dry_run=True, allow_auto_apply=[fix_id])
    event = make_event(event_type, issue_type)
    decision = RemediationPolicy().decide(event, project)
    gate = evaluate_runtime_auto_recovery_gate(
        event=event,
        project=project,
        remediation_decision=decision,
    )

    assert gate.auto_recover_allowed
    assert gate.dry_run
    assert gate.is_candidate
    assert not gate.allowed_to_execute
    assert not gate.would_execute
    assert gate.downgrade_reason == "r15_dry_run"
    assert gate.audit_record["execution_result"] == "not_run_r15_dry_run"


def test_policy_dry_run_and_guarded_audit_cover_new_domains() -> None:
    project = make_project(dry_run=True)
    policy = build_runtime_auto_recovery_policy(project)
    sample_events = [
        {
            "event_type": event_type,
            "fingerprint": f"dry-run-{event_type}",
            "confidence": 0.95,
            "candidate_fix_id": fix_id,
        }
        for event_type, _issue_type, fix_id, _text in SAFE_EXPANSION_CASES
    ]

    dry_run = run_policy_dry_run(policy, sample_events)

    assert dry_run.policy_valid
    assert dry_run.summary["auto_recover_allowed_count"] == len(SAFE_EXPANSION_CASES)
    for decision in dry_run.decisions:
        assert decision.strategy_layer == "safe_auto_recover"
        assert decision.auto_recover_allowed
        assert decision.dry_run

        guarded = evaluate_guarded_auto_recover_dry_run(
            event_type=decision.event_type,
            fingerprint=decision.fingerprint,
            candidate_fix_id=decision.selected_fix_id,
            strategy_layer=decision.strategy_layer,
            policy_decision=decision.to_dict(),
            precheck_result={"passed": True},
            cooldown_result={"allowed": True},
            rollback_available=True,
        )

        assert guarded.allowed_by_policy
        assert guarded.dry_run
        assert not guarded.would_execute
        assert guarded.audit_record["execution_result"] == "not_run_guarded_dry_run"


@pytest.mark.parametrize(
    ("fix_id", "initial_config", "field_name", "expected_value"),
    [
        ("fix-cache-1", {"cache_enabled": True}, "cache_enabled", False),
        (
            "fix-optional-dep-1",
            {"optional_dependency_enabled": True},
            "optional_dependency_enabled",
            False,
        ),
        ("fix-worker-1", {"worker_concurrency": 8}, "worker_concurrency", 2),
    ],
)
def test_safe_apply_executor_modifies_only_controlled_json_fields_and_rolls_back(
    tmp_path: Path,
    fix_id: str,
    initial_config: dict[str, object],
    field_name: str,
    expected_value: object,
) -> None:
    project_dir = tmp_path / fix_id
    session_dir = tmp_path / f"{fix_id}-session"
    project_dir.mkdir()
    config_path = project_dir / "config.json"
    original_config = {
        "service_name": "safe-expansion-test",
        "metrics_port": 9100,
        "batch_size": 16,
        **initial_config,
    }
    config_path.write_text(
        json.dumps(original_config, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    executor = SafeApplyExecutor(
        project_dir=str(project_dir),
        session_dir=str(session_dir),
    )
    apply_result = executor.apply(fix_id)

    assert apply_result.success
    data = json.loads(config_path.read_text(encoding="utf-8"))
    assert data[field_name] == expected_value
    assert apply_result.edit_results[0].field_path == field_name
    assert apply_result.edit_results[0].backup_path
    assert apply_result.edit_results[0].diff_path

    rollback_result = executor.rollback_latest()

    assert rollback_result.success
    rolled_back = json.loads(config_path.read_text(encoding="utf-8"))
    assert rolled_back == original_config


@pytest.mark.parametrize(
    ("event_type", "issue_type"),
    [
        ("disk_full", "disk"),
        ("python_env", "python_env"),
        ("process_crash", "process"),
        ("container_k8s", "container_k8s"),
        ("auth_cert", "auth_cert"),
    ],
)
def test_high_risk_or_generic_domains_remain_manual(
    event_type: str,
    issue_type: str,
) -> None:
    project = make_project()
    decision = RemediationPolicy().decide(make_event(event_type, issue_type), project)

    assert not decision.is_auto_recover
    assert decision.action in {"manual_escalation", "report_only"}


def test_forbidden_action_still_blocks_guarded_candidate() -> None:
    result = evaluate_guarded_auto_recover_dry_run(
        event_type="worker_overload",
        fingerprint="forbidden-worker",
        candidate_fix_id="fix-worker-1",
        strategy_layer="safe_auto_recover",
        policy_decision={"auto_recover_allowed": True},
        precheck_result={"passed": True},
        cooldown_result={"allowed": True},
        rollback_available=True,
        action_description="systemctl restart workers",
    )

    assert result.strategy_layer == "disabled"
    assert result.downgrade_reason == "forbidden_action"
    assert not result.allowed_by_policy
    assert not result.would_execute
