from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from detectors import ErrorEvent
from monitors.project_registry import ProjectConfig
from policies import RemediationDecision
from policies.auto_recovery_policy import (
    AutoRecoveryDecision,
    AutoRecoveryPolicy,
    EventTypePolicy,
    PolicyValidationError,
    RiskLevel,
    StrategyLayer,
    resolve_policy_for_event,
)
from recovery.guarded_auto_recover_dry_run import FORBIDDEN_ACTIONS


SAFE_FIX_BY_EVENT_TYPE = {
    "network_port": "fix-network-1",
    "gpu_oom": "fix-gpu-1",
    "cache_write_failed": "fix-cache-1",
    "optional_dependency_missing": "fix-optional-dep-1",
    "worker_overload": "fix-worker-1",
}

ACTION_DESCRIPTIONS = {
    "fix-network-1": "safe JSON config edit: config.json metrics_port -> 9101",
    "fix-gpu-1": "safe JSON config edit: config.json batch_size -> 4",
    "fix-cache-1": "safe JSON config edit: disable optional cache writes",
    "fix-optional-dep-1": "safe JSON config edit: disable optional dependency integration",
    "fix-worker-1": "safe JSON config edit: reduce worker concurrency",
}


@dataclass
class RuntimeAutoRecoveryGateResult:
    event_type: str
    fingerprint: str
    strategy_layer: str
    candidate_fix_id: str
    selected_fix_id: str
    dry_run: bool
    would_execute: bool
    allowed_to_execute: bool
    auto_recover_allowed: bool
    operator_required: bool
    audit_required: bool
    downgrade_reason: str
    precheck_result: dict[str, Any] = field(default_factory=dict)
    cooldown_result: dict[str, Any] = field(default_factory=dict)
    rollback_available: bool = False
    policy_decision: AutoRecoveryDecision | None = None
    audit_record: dict[str, Any] = field(default_factory=dict)

    @property
    def is_candidate(self) -> bool:
        return bool(self.auto_recover_allowed and self.selected_fix_id)

    def to_markdown(self) -> str:
        return (
            "## R15 runtime auto_recovery gate\n\n"
            f"- event_type: `{self.event_type}`\n"
            f"- fingerprint: `{self.fingerprint}`\n"
            f"- strategy_layer: `{self.strategy_layer}`\n"
            f"- candidate_fix_id: `{self.candidate_fix_id or '<none>'}`\n"
            f"- selected_fix_id: `{self.selected_fix_id or '<none>'}`\n"
            f"- auto_recover_allowed: `{self.auto_recover_allowed}`\n"
            f"- dry_run: `{self.dry_run}`\n"
            f"- would_execute: `{self.would_execute}`\n"
            f"- allowed_to_execute: `{self.allowed_to_execute}`\n"
            f"- operator_required: `{self.operator_required}`\n"
            f"- downgrade_reason: `{self.downgrade_reason or '<none>'}`\n"
            f"- rollback_available: `{self.rollback_available}`\n\n"
            "### audit_record\n\n"
            "```json\n"
            f"{json.dumps(self.audit_record, indent=2, ensure_ascii=False)}\n"
            "```"
        )


def build_runtime_auto_recovery_policy(project: ProjectConfig) -> AutoRecoveryPolicy:
    dry_run_default = bool(getattr(project.policy, "auto_recovery_dry_run", True))

    return AutoRecoveryPolicy(
        schema_version="r15.runtime",
        default_strategy=StrategyLayer.MANUAL_ESCALATION,
        unknown_event_strategy=StrategyLayer.DIAGNOSE_ONLY,
        dry_run_default=dry_run_default,
        event_type_policies={
            "network_port": _safe_policy("fix-network-1", dry_run=dry_run_default),
            "gpu_oom": _safe_policy("fix-gpu-1", dry_run=dry_run_default),
            "cache_write_failed": _safe_policy("fix-cache-1", dry_run=dry_run_default),
            "optional_dependency_missing": _safe_policy(
                "fix-optional-dep-1",
                dry_run=dry_run_default,
            ),
            "worker_overload": _safe_policy("fix-worker-1", dry_run=dry_run_default),
            "process_crash": _manual_policy(),
            "container_k8s": _manual_policy(),
            "disk_full": _manual_policy(),
            "python_env": _manual_policy(),
            "auth_cert": _manual_policy(),
            "slurm": _manual_policy(),
            "dependency_service": _manual_policy(),
            "host_resource": _manual_policy(),
            "network_connectivity": _manual_policy(),
            "config_error": _manual_policy(),
            "permission_denied": _manual_policy(),
            "process_kill": _manual_policy(),
        },
        action_allowlist={
            fix_id: {"source": "project.policy.allow_auto_apply"}
            for fix_id in project.policy.allow_auto_apply
        },
        forbidden_actions=list(FORBIDDEN_ACTIONS),
        audit_required=True,
    )


def evaluate_runtime_auto_recovery_gate(
    *,
    event: ErrorEvent,
    project: ProjectConfig,
    remediation_decision: RemediationDecision,
) -> RuntimeAutoRecoveryGateResult:
    if not getattr(project.policy, "auto_recovery_policy_enabled", True):
        return _legacy_passthrough_result(
            event=event,
            project=project,
            remediation_decision=remediation_decision,
        )

    candidate_fix_id = remediation_decision.fix_id or ""

    try:
        policy = build_runtime_auto_recovery_policy(project)
        policy_decision = resolve_policy_for_event(
            event_type=event.event_type,
            fingerprint=event.fingerprint,
            confidence=_event_confidence(event),
            candidate_fix_id=candidate_fix_id,
            policy=policy,
        )
    except PolicyValidationError as exc:
        return _blocked_result(
            event=event,
            candidate_fix_id=candidate_fix_id,
            strategy_layer=StrategyLayer.DISABLED,
            downgrade_reason=f"r15_policy_validation_failed:{exc}",
            precheck_result={"passed": False, "reason": "r15_policy_validation_failed"},
            cooldown_result=_default_cooldown_result(),
            rollback_available=False,
            policy_decision=None,
        )

    precheck_result = _run_precheck(
        event=event,
        project=project,
        remediation_decision=remediation_decision,
        policy_decision=policy_decision,
    )
    cooldown_result = _default_cooldown_result()
    rollback_available = bool(
        project.policy.rollback_on_failure
        and policy_decision.selected_fix_id in set(SAFE_FIX_BY_EVENT_TYPE.values())
    )

    downgrade_reason = (
        policy_decision.downgrade_reason
        or _first_failed_gate_reason(
            precheck_result=precheck_result,
            cooldown_result=cooldown_result,
            rollback_available=rollback_available,
        )
    )

    dry_run = bool(policy_decision.dry_run)
    auto_recover_allowed = bool(policy_decision.auto_recover_allowed)
    allowed_to_execute = (
        auto_recover_allowed
        and not dry_run
        and precheck_result.get("passed") is True
        and cooldown_result.get("allowed") is True
        and rollback_available
        and not policy_decision.operator_required
    )

    if auto_recover_allowed and dry_run and not downgrade_reason:
        downgrade_reason = "r15_dry_run"

    would_execute = bool(allowed_to_execute)
    strategy_layer = _as_value(policy_decision.strategy_layer)
    if not auto_recover_allowed and strategy_layer == StrategyLayer.SAFE_AUTO_RECOVER.value:
        strategy_layer = StrategyLayer.MANUAL_ESCALATION.value

    result = RuntimeAutoRecoveryGateResult(
        event_type=event.event_type,
        fingerprint=event.fingerprint,
        strategy_layer=strategy_layer,
        candidate_fix_id=candidate_fix_id,
        selected_fix_id=policy_decision.selected_fix_id,
        dry_run=dry_run,
        would_execute=would_execute,
        allowed_to_execute=allowed_to_execute,
        auto_recover_allowed=auto_recover_allowed,
        operator_required=bool(policy_decision.operator_required),
        audit_required=bool(policy_decision.audit_required),
        downgrade_reason=downgrade_reason,
        precheck_result=precheck_result,
        cooldown_result=cooldown_result,
        rollback_available=rollback_available,
        policy_decision=policy_decision,
    )
    result.audit_record = _build_audit_record(result)
    return result


def _safe_policy(fix_id: str, *, dry_run: bool) -> EventTypePolicy:
    return EventTypePolicy(
        strategy_layer=StrategyLayer.SAFE_AUTO_RECOVER,
        risk_level=RiskLevel.LOW,
        confidence_required=0.8,
        allowed_fix_ids=[fix_id],
        require_precheck=True,
        require_rollback=True,
        require_operator_confirmation=False,
        audit_required=True,
        fallback_strategy=StrategyLayer.MANUAL_ESCALATION,
        dry_run=dry_run,
    )


def _manual_policy() -> EventTypePolicy:
    return EventTypePolicy(
        strategy_layer=StrategyLayer.MANUAL_ESCALATION,
        risk_level=RiskLevel.HIGH,
        allowed_fix_ids=[],
        require_operator_confirmation=True,
        audit_required=True,
        fallback_strategy=StrategyLayer.MANUAL_ESCALATION,
    )


def _run_precheck(
    *,
    event: ErrorEvent,
    project: ProjectConfig,
    remediation_decision: RemediationDecision,
    policy_decision: AutoRecoveryDecision,
) -> dict[str, Any]:
    reasons: list[str] = []
    expected_fix_id = SAFE_FIX_BY_EVENT_TYPE.get(event.event_type, "")
    selected_fix_id = policy_decision.selected_fix_id

    if not project.policy.auto_recover:
        reasons.append("project_auto_recover_disabled")

    if not remediation_decision.is_auto_recover:
        reasons.append("legacy_policy_did_not_allow_auto_recover")

    if expected_fix_id and selected_fix_id != expected_fix_id:
        reasons.append("target_fix_mismatch")

    if not expected_fix_id:
        reasons.append("event_type_not_existing_safe_candidate")

    if selected_fix_id and selected_fix_id not in project.policy.allow_auto_apply:
        reasons.append("fix_id_not_in_project_allowlist")

    if _matches_forbidden_text(
        [
            selected_fix_id,
            remediation_decision.reason,
            ACTION_DESCRIPTIONS.get(selected_fix_id, ""),
        ]
    ):
        reasons.append("forbidden_action")

    if project.is_remote:
        if not project.remote_project_dir:
            reasons.append("remote_project_dir_missing")
    elif not project.project_dir:
        reasons.append("project_dir_missing")

    if not (event.raw_excerpt or event.summary or event.matched_keywords):
        reasons.append("insufficient_evidence")

    if not project.policy.rollback_on_failure:
        reasons.append("rollback_disabled")

    return {
        "passed": not reasons,
        "reasons": reasons,
        "target_event_type": event.event_type,
        "target_fix_id": selected_fix_id,
        "project_id": project.project_id,
    }


def _default_cooldown_result() -> dict[str, Any]:
    return {
        "allowed": True,
        "reason": "monitor_loop_seen_fingerprint_and_rate_limit_checked_before_recovery",
    }


def _first_failed_gate_reason(
    *,
    precheck_result: dict[str, Any],
    cooldown_result: dict[str, Any],
    rollback_available: bool,
) -> str:
    if precheck_result.get("passed") is not True:
        reasons = precheck_result.get("reasons") or ["precheck_failed"]
        return str(reasons[0])

    if cooldown_result.get("allowed") is not True:
        return str(cooldown_result.get("reason") or "cooldown_not_satisfied")

    if not rollback_available:
        return "rollback_unavailable"

    return ""


def _build_audit_record(result: RuntimeAutoRecoveryGateResult) -> dict[str, Any]:
    execution_result = (
        "would_run_r15_live"
        if result.would_execute
        else (
            "not_run_r15_dry_run"
            if result.dry_run and result.auto_recover_allowed
            else "not_run_r15_gate_blocked"
        )
    )

    return {
        "event_type": result.event_type,
        "fingerprint": result.fingerprint,
        "strategy_layer": result.strategy_layer,
        "selected_policy": "r15.runtime",
        "candidate_fix_id": result.candidate_fix_id,
        "selected_fix_id": result.selected_fix_id,
        "would_execute": result.would_execute,
        "dry_run": result.dry_run,
        "auto_recover_allowed": result.auto_recover_allowed,
        "allowed_to_execute": result.allowed_to_execute,
        "precheck_result": dict(result.precheck_result),
        "cooldown_result": dict(result.cooldown_result),
        "rate_limit_result": {
            "checked_before_runner": True,
            "source": "MonitorLoop.rate_limit_tracker",
        },
        "rollback_available": result.rollback_available,
        "operator_required": result.operator_required,
        "downgrade_reason": result.downgrade_reason,
        "execution_result": execution_result,
        "rollback_result": "not_run_before_execution",
        "policy_decision": _policy_decision_summary(result.policy_decision),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def _legacy_passthrough_result(
    *,
    event: ErrorEvent,
    project: ProjectConfig,
    remediation_decision: RemediationDecision,
) -> RuntimeAutoRecoveryGateResult:
    allowed = bool(remediation_decision.is_auto_recover)
    selected_fix_id = remediation_decision.fix_id if allowed else ""
    result = RuntimeAutoRecoveryGateResult(
        event_type=event.event_type,
        fingerprint=event.fingerprint,
        strategy_layer="legacy_remediation_policy",
        candidate_fix_id=remediation_decision.fix_id,
        selected_fix_id=selected_fix_id,
        dry_run=False,
        would_execute=allowed,
        allowed_to_execute=allowed,
        auto_recover_allowed=allowed,
        operator_required=False,
        audit_required=False,
        downgrade_reason="" if allowed else remediation_decision.reason,
        precheck_result={"passed": allowed, "legacy_policy_enabled": True},
        cooldown_result=_default_cooldown_result(),
        rollback_available=bool(project.policy.rollback_on_failure),
        policy_decision=None,
    )
    result.audit_record = _build_audit_record(result)
    return result


def _blocked_result(
    *,
    event: ErrorEvent,
    candidate_fix_id: str,
    strategy_layer: StrategyLayer,
    downgrade_reason: str,
    precheck_result: dict[str, Any],
    cooldown_result: dict[str, Any],
    rollback_available: bool,
    policy_decision: AutoRecoveryDecision | None,
) -> RuntimeAutoRecoveryGateResult:
    result = RuntimeAutoRecoveryGateResult(
        event_type=event.event_type,
        fingerprint=event.fingerprint,
        strategy_layer=strategy_layer.value,
        candidate_fix_id=candidate_fix_id,
        selected_fix_id="",
        dry_run=True,
        would_execute=False,
        allowed_to_execute=False,
        auto_recover_allowed=False,
        operator_required=True,
        audit_required=True,
        downgrade_reason=downgrade_reason,
        precheck_result=precheck_result,
        cooldown_result=cooldown_result,
        rollback_available=rollback_available,
        policy_decision=policy_decision,
    )
    result.audit_record = _build_audit_record(result)
    return result


def _policy_decision_summary(
    decision: AutoRecoveryDecision | None,
) -> dict[str, Any]:
    if decision is None:
        return {}

    return {
        "event_type": decision.event_type,
        "fingerprint": decision.fingerprint,
        "strategy_layer": _as_value(decision.strategy_layer),
        "auto_recover_allowed": decision.auto_recover_allowed,
        "dry_run": decision.dry_run,
        "selected_fix_id": decision.selected_fix_id,
        "downgrade_reason": decision.downgrade_reason,
        "operator_required": decision.operator_required,
        "audit_required": decision.audit_required,
    }


def _matches_forbidden_text(values: list[str]) -> bool:
    normalized_forbidden = [_normalize_text(item) for item in FORBIDDEN_ACTIONS]
    for value in values:
        normalized = _normalize_text(value)
        if not normalized:
            continue
        if any(item and item in normalized for item in normalized_forbidden):
            return True
    return False


def _normalize_text(value: str) -> str:
    return " ".join(str(value).strip().lower().split())


def _event_confidence(event: ErrorEvent) -> float:
    confidence = getattr(event, "confidence", None)
    if confidence is None:
        return 1.0
    try:
        return float(confidence)
    except (TypeError, ValueError):
        return 0.0


def _as_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    return value
