from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from detectors import ErrorEventDetector
from fixers.apply_executor import SafeApplyExecutor
from fixers.remote_apply_executor import RemoteSafeApplyExecutor
from monitors.project_registry import PolicyConfig, ProjectConfig
from policies import RemediationPolicy
from policies.auto_recovery_policy import (
    MANUAL_ESCALATION_EVENT_TYPES,
    SAFE_CANDIDATE_EVENT_TYPES,
)
from recovery.auto_recovery_runtime_controls import SAFE_FIX_SAFETY_SPECS
from recovery.auto_recovery_runtime_gate import build_runtime_auto_recovery_policy
from recovery.guarded_auto_recover_dry_run import GUARDED_DRY_RUN_CANDIDATES
from safe_recovery.registry import iter_safe_recovery_specs
from safe_recovery.registry_governance import (
    SafeRecoveryRegistryGovernanceInputs,
    validate_safe_recovery_registry_governance,
)


def make_project() -> ProjectConfig:
    return ProjectConfig(
        project_id="safe_registry_governance",
        name="Safe Registry Governance",
        mode="local",
        project_dir=".",
        run_command="python app.py",
        policy=PolicyConfig(
            auto_recover=True,
            allow_auto_apply=[spec.fix_id for spec in iter_safe_recovery_specs()],
            rollback_on_failure=True,
            auto_recovery_policy_enabled=True,
            auto_recovery_dry_run=True,
        ),
    )


def make_governance_inputs() -> SafeRecoveryRegistryGovernanceInputs:
    runtime_policy = build_runtime_auto_recovery_policy(make_project())

    return SafeRecoveryRegistryGovernanceInputs(
        detector_issue_by_event_type={
            rule.event_type: rule.issue_type
            for rule in ErrorEventDetector.RULES
        },
        policy_safe_event_types=set(SAFE_CANDIDATE_EVENT_TYPES),
        policy_manual_event_types=set(MANUAL_ESCALATION_EVENT_TYPES),
        remediation_fix_by_event_type=dict(RemediationPolicy.DEFAULT_FIX_MAPPING),
        remediation_manual_event_types=set(RemediationPolicy.ALWAYS_ESCALATE_EVENT_TYPES),
        remediation_manual_issue_types=set(RemediationPolicy.ALWAYS_ESCALATE_ISSUE_TYPES),
        runtime_event_policies=dict(runtime_policy.event_type_policies),
        precheck_specs_by_fix_id=dict(SAFE_FIX_SAFETY_SPECS),
        guarded_candidates_by_event_type={
            event_type: set(fix_ids)
            for event_type, fix_ids in GUARDED_DRY_RUN_CANDIDATES.items()
        },
        local_supported_fix_ids=SafeApplyExecutor.supported_safe_fix_ids(),
        remote_supported_fix_ids=RemoteSafeApplyExecutor.supported_safe_fix_ids(),
        regression_expected_event_types=_regression_expected_event_types(),
    )


def test_safe_recovery_registry_governance_contract_has_no_issues() -> None:
    issues = validate_safe_recovery_registry_governance(make_governance_inputs())

    assert issues == []


def test_governance_validator_reports_cross_layer_drift() -> None:
    base = make_governance_inputs()
    first_spec = iter_safe_recovery_specs()[0]

    policy_drift = replace(
        base,
        policy_safe_event_types=set(base.policy_safe_event_types)
        | {"unregistered_safe_event"},
    )
    policy_issues = validate_safe_recovery_registry_governance(policy_drift)

    assert any(
        issue == "policy_safe_event_types_mismatch:extra=unregistered_safe_event"
        for issue in policy_issues
    )

    executor_drift = replace(
        base,
        local_supported_fix_ids=set(base.local_supported_fix_ids)
        - {first_spec.fix_id},
    )
    executor_issues = validate_safe_recovery_registry_governance(executor_drift)

    assert any(
        issue.startswith("local_executor_supported_fix_ids_mismatch:")
        and f"missing={first_spec.fix_id}" in issue
        for issue in executor_issues
    )

    runtime_policies = dict(base.runtime_event_policies)
    runtime_policies.pop(first_spec.event_type)
    runtime_drift = replace(base, runtime_event_policies=runtime_policies)
    runtime_issues = validate_safe_recovery_registry_governance(runtime_drift)

    assert f"runtime_policy_missing:{first_spec.event_type}" in runtime_issues

    detector_drift = replace(
        base,
        detector_issue_by_event_type={
            **dict(base.detector_issue_by_event_type),
            "unregistered_detector_domain": "unregistered_detector_domain",
        },
    )
    detector_issues = validate_safe_recovery_registry_governance(detector_drift)

    assert any(
        issue == "detector_event_types_mismatch:extra=unregistered_detector_domain"
        for issue in detector_issues
    )

    regression_drift = replace(
        base,
        regression_expected_event_types=set(base.regression_expected_event_types)
        - {"python_env"},
    )
    regression_issues = validate_safe_recovery_registry_governance(regression_drift)

    assert "regression_expected_case_missing:python_env" in regression_issues


def test_governance_validator_rejects_precheck_without_runtime_registration() -> None:
    base = make_governance_inputs()
    first_spec = iter_safe_recovery_specs()[0]
    runtime_policies = dict(base.runtime_event_policies)
    runtime_policies.pop(first_spec.event_type)

    issues = validate_safe_recovery_registry_governance(
        replace(base, runtime_event_policies=runtime_policies)
    )

    assert f"runtime_policy_missing:{first_spec.event_type}" in issues
    assert not any(
        issue.startswith("precheck_spec_fix_ids_mismatch")
        for issue in issues
    )


def _regression_expected_event_types() -> set[str]:
    cases_path = PROJECT_ROOT / "tests/fixtures/regression_logs/expected_cases.json"
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    return {
        case["expected_event_type"]
        for case in cases
        if case.get("expected_event_type")
    }
