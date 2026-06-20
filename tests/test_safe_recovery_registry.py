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
from policies.auto_recovery_policy import SAFE_CANDIDATE_EVENT_TYPES
from recovery.auto_recovery_runtime_controls import SAFE_FIX_SAFETY_SPECS
from recovery.auto_recovery_runtime_gate import (
    ACTION_DESCRIPTIONS,
    SAFE_FIX_BY_EVENT_TYPE,
    build_runtime_auto_recovery_policy,
)
from recovery.guarded_auto_recover_dry_run import GUARDED_DRY_RUN_CANDIDATES
from safe_recovery.registry import (
    SAFE_ACTION_DESCRIPTIONS,
    SAFE_RECOVERY_EVENT_TYPES,
    SAFE_RECOVERY_FIX_IDS,
    SAFE_RECOVERY_SPECS_BY_FIX_ID,
    SafeRecoverySpec,
    iter_safe_recovery_specs,
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


def test_runtime_policy_and_legacy_mapping_cover_every_registry_spec() -> None:
    runtime_policy = build_runtime_auto_recovery_policy(make_project())

    for spec in iter_safe_recovery_specs():
        event_policy = runtime_policy.event_type_policies[spec.event_type]
        assert event_policy.allowed_fix_ids == [spec.fix_id]
        assert event_policy.require_precheck
        assert event_policy.require_rollback
        assert RemediationPolicy.DEFAULT_FIX_MAPPING[spec.event_type] == spec.fix_id


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
