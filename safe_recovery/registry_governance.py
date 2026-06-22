from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Mapping

from .registry import SafeRecoverySpec, iter_safe_recovery_specs
from .semantics import (
    SEMANTIC_DISABLE_BOOL,
    SEMANTIC_LOWER_INT,
    SEMANTIC_PORT_AVAILABLE,
    SEMANTIC_SAFE_ENUM_DOWNGRADE,
    SEMANTIC_SET_LITERAL,
)


KNOWN_SEMANTIC_RULES = frozenset(
    {
        SEMANTIC_DISABLE_BOOL,
        SEMANTIC_LOWER_INT,
        SEMANTIC_PORT_AVAILABLE,
        SEMANTIC_SAFE_ENUM_DOWNGRADE,
        SEMANTIC_SET_LITERAL,
    }
)


@dataclass(frozen=True)
class SafeRecoveryRegistryGovernanceInputs:
    detector_issue_by_event_type: Mapping[str, str]
    policy_safe_event_types: set[str]
    policy_manual_event_types: set[str]
    remediation_fix_by_event_type: Mapping[str, str]
    runtime_event_policies: Mapping[str, Any]
    precheck_specs_by_fix_id: Mapping[str, SafeRecoverySpec]
    guarded_candidates_by_event_type: Mapping[str, set[str]]
    local_supported_fix_ids: set[str]
    remote_supported_fix_ids: set[str]
    regression_expected_event_types: set[str]


def validate_safe_recovery_registry_governance(
    inputs: SafeRecoveryRegistryGovernanceInputs,
) -> list[str]:
    issues: list[str] = []
    specs = list(iter_safe_recovery_specs())

    if not specs:
        issues.append("registry_empty")
        return issues

    _validate_registry_shape(specs=specs, issues=issues)

    expected_fix_by_event = {spec.event_type: spec.fix_id for spec in specs}
    expected_fix_by_issue = {spec.issue_type: spec.fix_id for spec in specs}
    expected_events = set(expected_fix_by_event)
    expected_fix_ids = set(expected_fix_by_event.values())

    _validate_detector_coverage(
        specs=specs,
        detector_issue_by_event_type=inputs.detector_issue_by_event_type,
        issues=issues,
    )
    _validate_exact_set(
        issues=issues,
        check_name="policy_safe_event_types",
        actual=inputs.policy_safe_event_types,
        expected=expected_events,
    )
    _validate_manual_overlap(
        issues=issues,
        manual_event_types=inputs.policy_manual_event_types,
        expected_events=expected_events,
    )
    _validate_remediation_mapping(
        specs=specs,
        remediation_fix_by_event_type=inputs.remediation_fix_by_event_type,
        issues=issues,
    )
    _validate_runtime_policies(
        specs=specs,
        runtime_event_policies=inputs.runtime_event_policies,
        issues=issues,
    )
    _validate_precheck_specs(
        issues=issues,
        actual=inputs.precheck_specs_by_fix_id,
        expected_fix_ids=expected_fix_ids,
    )
    _validate_guarded_candidates(
        issues=issues,
        actual=inputs.guarded_candidates_by_event_type,
        expected_fix_by_event=expected_fix_by_event,
    )
    _validate_exact_set(
        issues=issues,
        check_name="local_executor_supported_fix_ids",
        actual=inputs.local_supported_fix_ids,
        expected=expected_fix_ids,
    )
    _validate_exact_set(
        issues=issues,
        check_name="remote_executor_supported_fix_ids",
        actual=inputs.remote_supported_fix_ids,
        expected=expected_fix_ids,
    )
    _validate_regression_coverage(
        issues=issues,
        regression_expected_event_types=inputs.regression_expected_event_types,
        expected_events=expected_events,
    )

    if len(expected_fix_by_issue) != len(specs):
        issues.append("registry_duplicate_issue_type_mapping")

    return issues


def _validate_registry_shape(
    *,
    specs: list[SafeRecoverySpec],
    issues: list[str],
) -> None:
    _validate_unique(
        issues=issues,
        values=[spec.event_type for spec in specs],
        name="event_type",
    )
    _validate_unique(
        issues=issues,
        values=[spec.issue_type for spec in specs],
        name="issue_type",
    )
    _validate_unique(
        issues=issues,
        values=[spec.fix_id for spec in specs],
        name="fix_id",
    )

    for spec in specs:
        subject = spec.fix_id or spec.event_type or "<unknown>"
        for field_name in (
            "event_type",
            "issue_type",
            "fix_id",
            "relative_config_path",
            "low_risk_reason",
            "action_description",
            "local_success_message",
            "remote_success_message",
            "remote_failure_message",
        ):
            if not str(getattr(spec, field_name, "")).strip():
                issues.append(f"registry_missing_field:{subject}:{field_name}")

        if spec.fix_id and not spec.fix_id.startswith("fix-"):
            issues.append(f"registry_invalid_fix_id:{spec.fix_id}")

        if not _is_safe_json_config_path(spec.relative_config_path):
            issues.append(
                f"registry_invalid_config_path:{subject}:{spec.relative_config_path}"
            )

        if not spec.candidates:
            issues.append(f"registry_missing_candidates:{subject}")

        for candidate in spec.candidates:
            field_parts = candidate.field_path.split(".")
            if not candidate.field_path.strip():
                issues.append(f"registry_candidate_missing_field_path:{subject}")
            if (
                candidate.field_path.startswith(".")
                or candidate.field_path.endswith(".")
            ):
                issues.append(
                    f"registry_candidate_invalid_field_path:{subject}:{candidate.field_path}"
                )
            if not all(field_parts):
                issues.append(
                    f"registry_candidate_invalid_field_path:{subject}:{candidate.field_path}"
                )
            if candidate.semantic_rule not in KNOWN_SEMANTIC_RULES:
                issues.append(
                    "registry_candidate_unknown_semantic_rule:"
                    f"{subject}:{candidate.field_path}:{candidate.semantic_rule}"
                )


def _validate_detector_coverage(
    *,
    specs: list[SafeRecoverySpec],
    detector_issue_by_event_type: Mapping[str, str],
    issues: list[str],
) -> None:
    for spec in specs:
        detected_issue = detector_issue_by_event_type.get(spec.event_type)
        if detected_issue is None:
            issues.append(f"detector_missing_event_type:{spec.event_type}")
            continue
        if detected_issue != spec.issue_type:
            issues.append(
                "detector_issue_type_mismatch:"
                f"{spec.event_type}:expected={spec.issue_type}:actual={detected_issue}"
            )


def _validate_manual_overlap(
    *,
    issues: list[str],
    manual_event_types: set[str],
    expected_events: set[str],
) -> None:
    overlap = sorted(expected_events & manual_event_types)
    if overlap:
        issues.append(f"policy_manual_overlap:{','.join(overlap)}")


def _validate_remediation_mapping(
    *,
    specs: list[SafeRecoverySpec],
    remediation_fix_by_event_type: Mapping[str, str],
    issues: list[str],
) -> None:
    for spec in specs:
        actual = remediation_fix_by_event_type.get(spec.event_type)
        if actual is None:
            issues.append(f"remediation_mapping_missing:{spec.event_type}")
            continue
        if actual != spec.fix_id:
            issues.append(
                "remediation_mapping_mismatch:"
                f"{spec.event_type}:expected={spec.fix_id}:actual={actual}"
            )


def _validate_runtime_policies(
    *,
    specs: list[SafeRecoverySpec],
    runtime_event_policies: Mapping[str, Any],
    issues: list[str],
) -> None:
    expected_events = {spec.event_type for spec in specs}
    runtime_safe_events = {
        event_type
        for event_type, policy in runtime_event_policies.items()
        if _as_value(getattr(policy, "strategy_layer", "")) == "safe_auto_recover"
    }
    _validate_exact_set(
        issues=issues,
        check_name="runtime_safe_event_types",
        actual=runtime_safe_events,
        expected=expected_events,
    )

    for spec in specs:
        policy = runtime_event_policies.get(spec.event_type)
        if policy is None:
            issues.append(f"runtime_policy_missing:{spec.event_type}")
            continue

        strategy = _as_value(getattr(policy, "strategy_layer", ""))
        if strategy != "safe_auto_recover":
            issues.append(
                f"runtime_policy_strategy_mismatch:{spec.event_type}:actual={strategy}"
            )

        allowed_fix_ids = list(getattr(policy, "allowed_fix_ids", []) or [])
        if allowed_fix_ids != [spec.fix_id]:
            issues.append(
                "runtime_policy_fix_mismatch:"
                f"{spec.event_type}:expected={spec.fix_id}:actual={','.join(allowed_fix_ids)}"
            )

        if getattr(policy, "require_precheck", False) is not True:
            issues.append(f"runtime_policy_precheck_missing:{spec.event_type}")
        if getattr(policy, "require_rollback", False) is not True:
            issues.append(f"runtime_policy_rollback_missing:{spec.event_type}")
        if getattr(policy, "require_operator_confirmation", True):
            issues.append(f"runtime_policy_operator_required:{spec.event_type}")
        if getattr(policy, "audit_required", False) is not True:
            issues.append(f"runtime_policy_audit_missing:{spec.event_type}")
        if _as_value(getattr(policy, "risk_level", "")) != "low":
            issues.append(f"runtime_policy_risk_not_low:{spec.event_type}")


def _validate_precheck_specs(
    *,
    issues: list[str],
    actual: Mapping[str, SafeRecoverySpec],
    expected_fix_ids: set[str],
) -> None:
    _validate_exact_set(
        issues=issues,
        check_name="precheck_spec_fix_ids",
        actual=set(actual),
        expected=expected_fix_ids,
    )

    for spec in iter_safe_recovery_specs():
        precheck_spec = actual.get(spec.fix_id)
        if precheck_spec is None:
            continue
        if precheck_spec != spec:
            issues.append(f"precheck_spec_mismatch:{spec.fix_id}")


def _validate_guarded_candidates(
    *,
    issues: list[str],
    actual: Mapping[str, set[str]],
    expected_fix_by_event: Mapping[str, str],
) -> None:
    _validate_exact_set(
        issues=issues,
        check_name="guarded_candidate_event_types",
        actual=set(actual),
        expected=set(expected_fix_by_event),
    )

    for event_type, expected_fix_id in expected_fix_by_event.items():
        actual_fix_ids = set(actual.get(event_type, set()))
        if actual_fix_ids != {expected_fix_id}:
            issues.append(
                "guarded_candidate_fix_mismatch:"
                f"{event_type}:expected={expected_fix_id}:actual={','.join(sorted(actual_fix_ids))}"
            )


def _validate_regression_coverage(
    *,
    issues: list[str],
    regression_expected_event_types: set[str],
    expected_events: set[str],
) -> None:
    missing = sorted(expected_events - regression_expected_event_types)
    for event_type in missing:
        issues.append(f"regression_expected_case_missing:{event_type}")


def _validate_exact_set(
    *,
    issues: list[str],
    check_name: str,
    actual: set[str],
    expected: set[str],
) -> None:
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing or extra:
        details = []
        if missing:
            details.append(f"missing={','.join(missing)}")
        if extra:
            details.append(f"extra={','.join(extra)}")
        issues.append(f"{check_name}_mismatch:{':'.join(details)}")


def _validate_unique(
    *,
    issues: list[str],
    values: list[str],
    name: str,
) -> None:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)

    for value in sorted(duplicates):
        issues.append(f"registry_duplicate_{name}:{value}")


def _is_safe_json_config_path(relative_config_path: str) -> bool:
    if not relative_config_path:
        return False
    path = PurePosixPath(relative_config_path)
    if path.is_absolute():
        return False
    if ".." in path.parts:
        return False
    return path.suffix.lower() == ".json"


def _as_value(value: Any) -> str:
    enum_value = getattr(value, "value", value)
    return str(enum_value)
