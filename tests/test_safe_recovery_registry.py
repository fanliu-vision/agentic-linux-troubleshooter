from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from fixers.apply_executor import SafeApplyExecutor
from fixers.remote_apply_executor import RemoteSafeApplyExecutor
from monitors.project_registry import PolicyConfig, ProjectConfig
from policies import RemediationPolicy
from policies.auto_recovery_policy import (
    MANUAL_ESCALATION_EVENT_TYPES,
    SAFE_CANDIDATE_EVENT_TYPES,
)
from recovery.auto_recovery_runtime_controls import SAFE_FIX_SAFETY_SPECS
from recovery.auto_recovery_runtime_gate import (
    ACTION_DESCRIPTIONS,
    SAFE_FIX_BY_EVENT_TYPE,
    build_runtime_auto_recovery_policy,
)
from recovery.guarded_auto_recover_dry_run import GUARDED_DRY_RUN_CANDIDATES
from safe_recovery.registry import (
    DIAGNOSE_ONLY_EVENT_TYPES,
    MANUAL_ESCALATION_EVENT_TYPES as REGISTRY_MANUAL_ESCALATION_EVENT_TYPES,
    RECOVERY_DOMAIN_EVENT_TYPES,
    SAFE_ACTION_DESCRIPTIONS,
    SAFE_CANDIDATE_EVENT_TYPES as REGISTRY_SAFE_CANDIDATE_EVENT_TYPES,
    SAFE_RECOVERY_EVENT_TYPES,
    SAFE_RECOVERY_FIX_IDS,
    SAFE_RECOVERY_SPECS_BY_FIX_ID,
    fix_id_for_event_type,
    get_recovery_domain_spec_for_event_type,
    SafeRecoverySpec,
    iter_recovery_domain_specs,
    iter_safe_recovery_specs,
    strategy_for_event_type,
)


@pytest.fixture(autouse=True)
def assume_target_port_available(monkeypatch) -> None:
    monkeypatch.setattr(
        SafeApplyExecutor,
        "_is_tcp_port_available",
        staticmethod(lambda host, port: True),
    )


def make_project() -> ProjectConfig:
    return ProjectConfig(
        project_id="safe_registry",
        name="Safe Registry",
        mode="local",
        project_dir=".",
        run_command="python app.py",
        policy=PolicyConfig(
            auto_recover=True,
            allow_auto_apply=sorted(SAFE_RECOVERY_FIX_IDS),
            rollback_on_failure=True,
            auto_recovery_policy_enabled=True,
            auto_recovery_dry_run=True,
        ),
    )


def test_safe_recovery_registry_is_the_single_safe_candidate_source() -> None:
    expected_fix_by_event = {
        spec.event_type: spec.fix_id
        for spec in iter_safe_recovery_specs()
    }
    expected_guarded = {
        spec.event_type: {spec.fix_id}
        for spec in iter_safe_recovery_specs()
    }

    assert SAFE_CANDIDATE_EVENT_TYPES == set(SAFE_RECOVERY_EVENT_TYPES)
    assert SAFE_FIX_BY_EVENT_TYPE == expected_fix_by_event
    assert ACTION_DESCRIPTIONS == SAFE_ACTION_DESCRIPTIONS
    assert GUARDED_DRY_RUN_CANDIDATES == expected_guarded
    assert SAFE_FIX_SAFETY_SPECS == SAFE_RECOVERY_SPECS_BY_FIX_ID


def test_domain_policy_registry_exports_safe_manual_and_diagnose_sources() -> None:
    domain_event_types = [
        spec.event_type for spec in iter_recovery_domain_specs()
    ]
    assert len(domain_event_types) == len(set(domain_event_types))
    assert not (
        REGISTRY_SAFE_CANDIDATE_EVENT_TYPES
        & REGISTRY_MANUAL_ESCALATION_EVENT_TYPES
    )
    assert not (REGISTRY_SAFE_CANDIDATE_EVENT_TYPES & DIAGNOSE_ONLY_EVENT_TYPES)
    assert not (REGISTRY_MANUAL_ESCALATION_EVENT_TYPES & DIAGNOSE_ONLY_EVENT_TYPES)

    assert SAFE_CANDIDATE_EVENT_TYPES == set(REGISTRY_SAFE_CANDIDATE_EVENT_TYPES)
    assert MANUAL_ESCALATION_EVENT_TYPES == set(
        REGISTRY_MANUAL_ESCALATION_EVENT_TYPES
    )
    assert REGISTRY_SAFE_CANDIDATE_EVENT_TYPES == SAFE_RECOVERY_EVENT_TYPES

    assert "python_env" in REGISTRY_MANUAL_ESCALATION_EVENT_TYPES
    assert strategy_for_event_type("python_env") == "manual_escalation"
    assert fix_id_for_event_type("python_env") == "fix-python-1"
    assert get_recovery_domain_spec_for_event_type("python_env").operator_required

    assert "disk_full" in REGISTRY_MANUAL_ESCALATION_EVENT_TYPES
    assert strategy_for_event_type("disk_full") == "manual_escalation"
    assert fix_id_for_event_type("disk_full") == ""

    assert "config_path" in DIAGNOSE_ONLY_EVENT_TYPES
    assert strategy_for_event_type("config_path") == "diagnose_only"
    assert fix_id_for_event_type("config_path") == "fix-config-path-1"

    assert strategy_for_event_type("unknown_future_domain") == "unregistered"
    assert fix_id_for_event_type("unknown_future_domain") == ""


def test_legacy_remediation_policy_constants_are_registry_derived() -> None:
    domain_specs = list(iter_recovery_domain_specs())
    expected_fix_mapping = {
        spec.event_type: spec.fix_id
        for spec in domain_specs
        if spec.fix_id
    }
    expected_manual_events = {
        spec.event_type
        for spec in domain_specs
        if spec.strategy_layer == "manual_escalation" and not spec.fix_id
    }
    expected_manual_issues = {
        spec.issue_type
        for spec in domain_specs
        if spec.strategy_layer == "manual_escalation" and not spec.fix_id
    }

    assert RemediationPolicy.DEFAULT_FIX_MAPPING == expected_fix_mapping
    assert RemediationPolicy.ALWAYS_ESCALATE_EVENT_TYPES == expected_manual_events
    assert RemediationPolicy.ALWAYS_ESCALATE_ISSUE_TYPES == expected_manual_issues
    assert RemediationPolicy.DEFAULT_FIX_MAPPING["python_env"] == "fix-python-1"
    assert "python_env" not in RemediationPolicy.ALWAYS_ESCALATE_EVENT_TYPES


def test_runtime_policy_and_legacy_mapping_cover_every_registry_spec() -> None:
    runtime_policy = build_runtime_auto_recovery_policy(make_project())

    for spec in iter_safe_recovery_specs():
        event_policy = runtime_policy.event_type_policies[spec.event_type]
        assert event_policy.allowed_fix_ids == [spec.fix_id]
        assert event_policy.require_precheck
        assert event_policy.require_rollback
        assert RemediationPolicy.DEFAULT_FIX_MAPPING[spec.event_type] == spec.fix_id


def test_runtime_policy_is_generated_for_every_registry_domain() -> None:
    runtime_policy = build_runtime_auto_recovery_policy(make_project())

    assert set(runtime_policy.event_type_policies) == set(RECOVERY_DOMAIN_EVENT_TYPES)

    for spec in iter_recovery_domain_specs():
        event_policy = runtime_policy.event_type_policies[spec.event_type]
        strategy = getattr(event_policy.strategy_layer, "value", event_policy.strategy_layer)
        if spec.strategy_layer == "safe_auto_recover":
            assert strategy == "safe_auto_recover"
            assert event_policy.allowed_fix_ids == [spec.fix_id]
            assert event_policy.require_precheck is True
            assert event_policy.require_rollback is True
            assert event_policy.require_operator_confirmation is False
        elif spec.strategy_layer == "manual_escalation":
            assert strategy == "manual_escalation"
            assert event_policy.allowed_fix_ids == []
            assert event_policy.require_operator_confirmation is True
        elif spec.strategy_layer == "diagnose_only":
            assert strategy == "diagnose_only"
            assert event_policy.allowed_fix_ids == []
            assert event_policy.require_operator_confirmation is False
        else:
            raise AssertionError(f"unexpected strategy: {spec.strategy_layer}")


def test_local_and_remote_executors_advertise_all_registry_safe_fixes() -> None:
    expected = set(SAFE_RECOVERY_FIX_IDS)

    assert SafeApplyExecutor.supported_safe_fix_ids() == expected
    assert RemoteSafeApplyExecutor.supported_safe_fix_ids() == expected


@pytest.mark.parametrize(
    "spec",
    list(iter_safe_recovery_specs()),
    ids=lambda spec: spec.fix_id,
)
def test_local_executor_applies_and_rolls_back_each_registry_fix(
    tmp_path: Path,
    spec: SafeRecoverySpec,
) -> None:
    candidate = spec.candidates[0]
    project_dir = tmp_path / spec.fix_id
    session_dir = tmp_path / f"{spec.fix_id}-session"
    project_dir.mkdir()

    original_config: dict[str, Any] = {"service_name": "safe-registry-test"}
    _set_nested_value(
        original_config,
        candidate.field_path,
        _old_value_for(candidate.new_value),
    )
    config_path = project_dir / spec.relative_config_path
    config_path.write_text(
        json.dumps(original_config, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    executor = SafeApplyExecutor(
        project_dir=str(project_dir),
        session_dir=str(session_dir),
    )
    apply_result = executor.apply(spec.fix_id)

    assert apply_result.success
    assert apply_result.edit_results[0].field_path == candidate.field_path
    updated = json.loads(config_path.read_text(encoding="utf-8"))
    assert _get_nested_value(updated, candidate.field_path) == candidate.new_value

    rollback_result = executor.rollback_latest()

    assert rollback_result.success
    rolled_back = json.loads(config_path.read_text(encoding="utf-8"))
    assert rolled_back == original_config


def _old_value_for(new_value: Any) -> Any:
    if isinstance(new_value, bool):
        return not new_value

    if isinstance(new_value, int):
        return new_value + 10

    if isinstance(new_value, str):
        return f"old-{new_value}"

    return None


def _set_nested_value(data: dict[str, Any], field_path: str, value: Any) -> None:
    current = data
    parts = field_path.split(".")
    for part in parts[:-1]:
        current = current.setdefault(part, {})
    current[parts[-1]] = value


def _get_nested_value(data: dict[str, Any], field_path: str) -> Any:
    current: Any = data
    for part in field_path.split("."):
        current = current[part]
    return current
