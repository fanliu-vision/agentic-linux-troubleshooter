from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping

from safe_recovery.registry import SAFE_FIX_BY_EVENT_TYPE


FORBIDDEN_ACTIONS = [
    "kill -9",
    "rm -rf",
    "pip install",
    "systemctl restart",
    "systemctl stop",
    "kubectl delete",
    "kubectl apply",
    "权限提升",
    "跨主机破坏性操作",
]

MANUAL_ESCALATION_EVENT_TYPES = {
    "auth_cert",
    "container_k8s",
    "disk_full",
    "process_crash",
    "python_env",
}

GUARDED_DRY_RUN_CANDIDATES = {
    event_type: {fix_id}
    for event_type, fix_id in SAFE_FIX_BY_EVENT_TYPE.items()
}


@dataclass
class GuardedAutoRecoverDryRunResult:
    event_type: str
    fingerprint: str
    strategy_layer: str
    candidate_fix_id: str
    would_execute: bool
    dry_run: bool
    allowed_by_policy: bool
    precheck_passed: bool
    cooldown_allowed: bool
    rollback_available: bool
    operator_required: bool
    downgrade_reason: str
    audit_record: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "fingerprint": self.fingerprint,
            "strategy_layer": self.strategy_layer,
            "candidate_fix_id": self.candidate_fix_id,
            "would_execute": self.would_execute,
            "dry_run": self.dry_run,
            "allowed_by_policy": self.allowed_by_policy,
            "precheck_passed": self.precheck_passed,
            "cooldown_allowed": self.cooldown_allowed,
            "rollback_available": self.rollback_available,
            "operator_required": self.operator_required,
            "downgrade_reason": self.downgrade_reason,
            "audit_record": dict(self.audit_record),
        }


def evaluate_guarded_auto_recover_dry_run(
    *,
    event_type: str,
    fingerprint: str,
    candidate_fix_id: str,
    strategy_layer: str | Enum,
    policy_decision: Any,
    precheck_result: bool | Mapping[str, Any],
    cooldown_result: bool | Mapping[str, Any],
    rollback_available: bool,
    action_description: str = "",
    forbidden_actions: list[str] | None = None,
) -> GuardedAutoRecoverDryRunResult:
    strategy_layer_value = _as_value(strategy_layer)
    candidate_fix_id = candidate_fix_id or ""
    forbidden_actions = forbidden_actions or FORBIDDEN_ACTIONS
    forbidden_hit = _find_forbidden_action(
        [candidate_fix_id, action_description],
        forbidden_actions,
    )

    precheck_passed = _extract_bool(precheck_result, "passed", default=False)
    cooldown_allowed = _extract_bool(cooldown_result, "allowed", default=False)
    policy_allowed = _policy_allows_candidate(policy_decision)
    operator_required = True

    downgrade_reason = ""
    final_strategy_layer = strategy_layer_value

    if forbidden_hit:
        final_strategy_layer = "disabled"
        downgrade_reason = "forbidden_action"
    elif event_type in MANUAL_ESCALATION_EVENT_TYPES:
        final_strategy_layer = "manual_escalation"
        downgrade_reason = "event_type_requires_manual_escalation"
    elif event_type not in GUARDED_DRY_RUN_CANDIDATES:
        if strategy_layer_value == "diagnose_only":
            final_strategy_layer = "diagnose_only"
        else:
            final_strategy_layer = "manual_escalation"
        downgrade_reason = "event_type_not_guarded_candidate"
    elif candidate_fix_id not in GUARDED_DRY_RUN_CANDIDATES[event_type]:
        final_strategy_layer = "manual_escalation"
        downgrade_reason = "candidate_fix_id_not_guarded_allowed"
    elif not policy_allowed:
        final_strategy_layer = "manual_escalation"
        downgrade_reason = "policy_did_not_allow_candidate"
    elif not precheck_passed:
        final_strategy_layer = "manual_escalation"
        downgrade_reason = "precheck_failed"
    elif not cooldown_allowed:
        final_strategy_layer = "manual_escalation"
        downgrade_reason = "cooldown_not_satisfied"
    elif not rollback_available:
        final_strategy_layer = "manual_escalation"
        downgrade_reason = "rollback_unavailable"
    else:
        final_strategy_layer = "guarded_auto_recover"

    allowed_by_policy = (
        not downgrade_reason
        and final_strategy_layer == "guarded_auto_recover"
        and policy_allowed
        and precheck_passed
        and cooldown_allowed
        and rollback_available
    )

    result = GuardedAutoRecoverDryRunResult(
        event_type=event_type,
        fingerprint=fingerprint,
        strategy_layer=final_strategy_layer,
        candidate_fix_id=candidate_fix_id,
        would_execute=False,
        dry_run=True,
        allowed_by_policy=allowed_by_policy,
        precheck_passed=precheck_passed,
        cooldown_allowed=cooldown_allowed,
        rollback_available=bool(rollback_available),
        operator_required=operator_required,
        downgrade_reason=downgrade_reason,
    )
    result.audit_record = build_guarded_audit_record(
        result=result,
        policy_decision=policy_decision,
        precheck_result=precheck_result,
        cooldown_result=cooldown_result,
        forbidden_action=forbidden_hit,
    )
    return result


def build_guarded_audit_record(
    *,
    result: GuardedAutoRecoverDryRunResult,
    policy_decision: Any,
    precheck_result: bool | Mapping[str, Any],
    cooldown_result: bool | Mapping[str, Any],
    forbidden_action: str = "",
) -> dict[str, Any]:
    return {
        "event_type": result.event_type,
        "fingerprint": result.fingerprint,
        "strategy_layer": result.strategy_layer,
        "candidate_fix_id": result.candidate_fix_id,
        "would_execute": result.would_execute,
        "dry_run": result.dry_run,
        "allowed_by_policy": result.allowed_by_policy,
        "precheck_passed": result.precheck_passed,
        "precheck_result": _normalize_result(precheck_result),
        "cooldown_allowed": result.cooldown_allowed,
        "cooldown_result": _normalize_result(cooldown_result),
        "rollback_available": result.rollback_available,
        "operator_required": result.operator_required,
        "downgrade_reason": result.downgrade_reason,
        "forbidden_action": forbidden_action,
        "policy_decision": _policy_decision_summary(policy_decision),
        "execution_result": "not_run_guarded_dry_run",
        "rollback_result": "not_run_guarded_dry_run",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def _policy_allows_candidate(policy_decision: Any) -> bool:
    if isinstance(policy_decision, Mapping):
        return bool(policy_decision.get("auto_recover_allowed", False))

    return bool(getattr(policy_decision, "auto_recover_allowed", False))


def _policy_decision_summary(policy_decision: Any) -> dict[str, Any]:
    if isinstance(policy_decision, Mapping):
        return dict(policy_decision)

    fields = [
        "event_type",
        "fingerprint",
        "strategy_layer",
        "auto_recover_allowed",
        "dry_run",
        "selected_fix_id",
        "downgrade_reason",
        "operator_required",
        "audit_required",
    ]
    summary: dict[str, Any] = {}
    for field_name in fields:
        if hasattr(policy_decision, field_name):
            summary[field_name] = _as_value(getattr(policy_decision, field_name))
    return summary


def _extract_bool(
    value: bool | Mapping[str, Any],
    key: str,
    *,
    default: bool,
) -> bool:
    if isinstance(value, Mapping):
        return bool(value.get(key, default))
    return bool(value)


def _normalize_result(value: bool | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {"value": bool(value)}


def _find_forbidden_action(values: list[str], forbidden_actions: list[str]) -> str:
    normalized_forbidden = {
        _normalize_text(action): action for action in forbidden_actions
    }

    for value in values:
        normalized_value = _normalize_text(value)
        if not normalized_value:
            continue
        for normalized_action, original_action in normalized_forbidden.items():
            if normalized_action and normalized_action in normalized_value:
                return original_action

    return ""


def _normalize_text(value: str) -> str:
    return " ".join(str(value).strip().lower().split())


def _as_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    return value
