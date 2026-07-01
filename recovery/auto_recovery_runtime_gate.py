from __future__ import annotations

import json
import re
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
from recovery.auto_recovery_runtime_controls import build_runtime_precheck_result
from recovery.guarded_auto_recover_dry_run import FORBIDDEN_ACTIONS
from safe_recovery.registry import (
    SAFE_ACTION_DESCRIPTIONS,
    SAFE_FIX_BY_EVENT_TYPE,
    STRATEGY_DIAGNOSE_ONLY,
    STRATEGY_MANUAL_ESCALATION,
    STRATEGY_SAFE_AUTO_RECOVER,
    RecoveryDomainSpec,
    fix_id_for_event_type,
    iter_recovery_domain_specs,
)


ACTION_DESCRIPTIONS = dict(SAFE_ACTION_DESCRIPTIONS)


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
            spec.event_type: _policy_for_domain(
                spec,
                dry_run=dry_run_default,
            )
            for spec in iter_recovery_domain_specs()
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
    cooldown_result: dict[str, Any] | None = None,
) -> RuntimeAutoRecoveryGateResult:
    if not getattr(project.policy, "auto_recovery_policy_enabled", True):
        return _blocked_result(
            event=event,
            candidate_fix_id=_candidate_fix_id_for_event(event),
            strategy_layer=StrategyLayer.DISABLED,
            downgrade_reason="r15_policy_disabled",
            precheck_result={"passed": False, "reason": "r15_policy_disabled"},
            cooldown_result=cooldown_result or _default_cooldown_result(),
            rollback_available=False,
            policy_decision=None,
        )

    candidate_fix_id = _candidate_fix_id_for_event(event)

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
            cooldown_result=cooldown_result or _default_cooldown_result(),
            rollback_available=False,
            policy_decision=None,
        )

    precheck_result = _run_precheck(
        event=event,
        project=project,
        policy_decision=policy_decision,
    )
    cooldown_result = cooldown_result or _default_cooldown_result()
    rollback_available = bool(
        project.policy.rollback_on_failure
        and policy_decision.selected_fix_id in set(SAFE_FIX_BY_EVENT_TYPE.values())
        and precheck_result.get("rollback_plan", {}).get("available") is True
    )

    downgrade_reason = (
        policy_decision.downgrade_reason
        or _first_failed_gate_reason(
            precheck_result=precheck_result,
            cooldown_result=cooldown_result,
            rollback_available=rollback_available,
        )
    )

    auto_recover_allowed = bool(policy_decision.auto_recover_allowed)
    has_actionable_edit = _has_actionable_planned_edit(precheck_result)
    operator_required = bool(
        policy_decision.operator_required
        or _precheck_requires_operator_review(precheck_result)
    )
    dry_run = bool(policy_decision.dry_run)
    allowed_to_execute = (
        auto_recover_allowed
        and not dry_run
        and precheck_result.get("passed") is True
        and has_actionable_edit
        and precheck_result.get("no_op") is not True
        and cooldown_result.get("allowed") is True
        and rollback_available
        and not operator_required
    )

    if auto_recover_allowed and dry_run and not downgrade_reason:
        downgrade_reason = "r15_dry_run"

    would_execute = bool(allowed_to_execute)
    strategy_layer = _as_value(policy_decision.strategy_layer)
    if (
        operator_required
        and strategy_layer == StrategyLayer.SAFE_AUTO_RECOVER.value
        and "ambiguous_event_evidence" in (precheck_result.get("reasons") or [])
    ):
        strategy_layer = StrategyLayer.MANUAL_ESCALATION.value
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
        operator_required=operator_required,
        audit_required=bool(policy_decision.audit_required),
        downgrade_reason=downgrade_reason,
        precheck_result=precheck_result,
        cooldown_result=cooldown_result,
        rollback_available=rollback_available,
        policy_decision=policy_decision,
    )
    result.audit_record = _build_audit_record(result)
    return result


def refresh_runtime_auto_recovery_audit(
    result: RuntimeAutoRecoveryGateResult,
) -> RuntimeAutoRecoveryGateResult:
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


def _manual_policy(*, risk_level: RiskLevel | str = RiskLevel.HIGH) -> EventTypePolicy:
    return EventTypePolicy(
        strategy_layer=StrategyLayer.MANUAL_ESCALATION,
        risk_level=risk_level,
        allowed_fix_ids=[],
        require_operator_confirmation=True,
        audit_required=True,
        fallback_strategy=StrategyLayer.MANUAL_ESCALATION,
    )


def _diagnose_policy(
    *,
    risk_level: RiskLevel | str = RiskLevel.MEDIUM,
) -> EventTypePolicy:
    return EventTypePolicy(
        strategy_layer=StrategyLayer.DIAGNOSE_ONLY,
        risk_level=risk_level,
        allowed_fix_ids=[],
        require_operator_confirmation=False,
        audit_required=True,
        fallback_strategy=StrategyLayer.MANUAL_ESCALATION,
    )


def _policy_for_domain(
    spec: RecoveryDomainSpec,
    *,
    dry_run: bool,
) -> EventTypePolicy:
    if spec.strategy_layer == STRATEGY_SAFE_AUTO_RECOVER:
        return _safe_policy(spec.fix_id, dry_run=dry_run)

    if spec.strategy_layer == STRATEGY_MANUAL_ESCALATION:
        return _manual_policy(risk_level=spec.risk_level)

    if spec.strategy_layer == STRATEGY_DIAGNOSE_ONLY:
        return _diagnose_policy(risk_level=spec.risk_level)

    return _manual_policy(risk_level=RiskLevel.UNKNOWN)


def _run_precheck(
    *,
    event: ErrorEvent,
    project: ProjectConfig,
    policy_decision: AutoRecoveryDecision,
) -> dict[str, Any]:
    expected_fix_id = SAFE_FIX_BY_EVENT_TYPE.get(event.event_type, "")
    selected_fix_id = policy_decision.selected_fix_id
    precheck = build_runtime_precheck_result(
        project=project,
        event_type=event.event_type,
        selected_fix_id=selected_fix_id,
    )
    reasons: list[str] = list(precheck.get("reasons") or [])

    if not project.policy.auto_recover:
        reasons.append("project_auto_recover_disabled")

    if event.issue_type in project.policy.escalation_required:
        reasons.append("project_escalation_required")

    if expected_fix_id and selected_fix_id != expected_fix_id:
        reasons.append("target_fix_mismatch")

    if not expected_fix_id:
        reasons.append("event_type_not_existing_safe_candidate")

    if selected_fix_id and selected_fix_id not in project.policy.allow_auto_apply:
        reasons.append("fix_id_not_in_project_allowlist")

    if _matches_forbidden_text(
        [
            selected_fix_id,
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

    ambiguity_result = _safe_recovery_ambiguity_result(
        event=event,
        selected_fix_id=selected_fix_id,
    )
    if ambiguity_result["ambiguous"]:
        reasons.insert(0, "ambiguous_event_evidence")

    if not project.policy.rollback_on_failure:
        reasons.append("rollback_disabled")

    precheck.update(
        {
            "passed": not reasons,
            "reasons": reasons,
            "target_event_type": event.event_type,
            "target_fix_id": selected_fix_id,
            "project_id": project.project_id,
            "evidence_present": bool(
                event.raw_excerpt or event.summary or event.matched_keywords
            ),
            "evidence_domain_check": ambiguity_result,
        }
    )
    return precheck


def _candidate_fix_id_for_event(event: ErrorEvent) -> str:
    return fix_id_for_event_type(getattr(event, "event_type", ""))


def _default_cooldown_result() -> dict[str, Any]:
    return {
        "allowed": True,
        "reserved": False,
        "reason": "monitor_loop_seen_fingerprint_and_rate_limit_checked_before_recovery",
        "scopes": {},
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

    if precheck_result.get("no_op") is True:
        return "no_op_already_safe"

    if precheck_result.get("actionable_edit_count") == 0:
        return "no_actionable_planned_edit"

    if cooldown_result.get("allowed") is not True:
        return str(cooldown_result.get("reason") or "cooldown_not_satisfied")

    if not rollback_available:
        return "rollback_unavailable"

    return ""


def _build_audit_record(result: RuntimeAutoRecoveryGateResult) -> dict[str, Any]:
    execution_result = (
        "not_run_r15_no_op"
        if (
            result.precheck_result.get("no_op") is True
            and not _result_has_gate_blocking_failure(result)
        )
        else (
            "would_run_r15_live"
            if result.would_execute
            else (
                "not_run_r15_dry_run"
                if (
                    result.dry_run
                    and result.auto_recover_allowed
                    and not _result_has_gate_blocking_failure(result)
                )
                else "not_run_r15_gate_blocked"
            )
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
        "audit_required": result.audit_required,
        "precheck_result": dict(result.precheck_result),
        "cooldown_result": dict(result.cooldown_result),
        "rate_limit_result": {
            "checked_before_runner": True,
            "source": "MonitorLoop.rate_limit_tracker",
        },
        "rollback_available": result.rollback_available,
        "operator_required": result.operator_required,
        "downgrade_reason": result.downgrade_reason,
        "forbidden_action": _has_forbidden_action(result),
        "execution_result": execution_result,
        "rollback_result": "not_run_before_execution",
        "policy_decision": _policy_decision_summary(result.policy_decision),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


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


def _has_forbidden_action(result: RuntimeAutoRecoveryGateResult) -> bool:
    reasons = result.precheck_result.get("reasons") or []
    return result.downgrade_reason == "forbidden_action" or "forbidden_action" in reasons


def _has_actionable_planned_edit(precheck_result: dict[str, Any]) -> bool:
    try:
        return int(precheck_result.get("actionable_edit_count", 0)) > 0
    except (TypeError, ValueError):
        return False


def _precheck_requires_operator_review(precheck_result: dict[str, Any]) -> bool:
    reasons = set(precheck_result.get("reasons") or [])
    return "ambiguous_event_evidence" in reasons


def _result_has_gate_blocking_failure(result: RuntimeAutoRecoveryGateResult) -> bool:
    if result.operator_required:
        return True

    if result.precheck_result.get("passed") is not True:
        return True

    if result.downgrade_reason and result.downgrade_reason not in {
        "r15_dry_run",
        "no_op_already_safe",
    }:
        return True

    return False


def _safe_recovery_ambiguity_result(
    *,
    event: ErrorEvent,
    selected_fix_id: str,
) -> dict[str, Any]:
    text = _event_evidence_text(event)
    result: dict[str, Any] = {
        "ambiguous": False,
        "reason": "",
        "selected_fix_id": selected_fix_id,
        "selected_event_type": event.event_type,
        "conflicting_domains": [],
        "matched_domains": {},
        "policy": "allow_safe_auto_recovery_when_evidence_domain_is_single",
    }

    if not selected_fix_id or not text:
        return result

    matched_domains = {
        "worker_overload": _matching_pattern_labels(text, WORKER_OVERLOAD_MARKERS),
        "queue_backpressure": _matching_pattern_labels(text, QUEUE_BACKPRESSURE_MARKERS),
        "python_env": _matching_pattern_labels(text, PYTHON_ENV_MARKERS),
        "optional_dependency": _matching_pattern_labels(text, OPTIONAL_DEPENDENCY_MARKERS),
        "optional_integration": _matching_pattern_labels(text, OPTIONAL_INTEGRATION_MARKERS),
        "cache_write": _matching_pattern_labels(text, CACHE_WRITE_MARKERS),
        "disk_full": _matching_pattern_labels(text, DISK_FULL_MARKERS),
        "dependency_service": _matching_pattern_labels(text, DEPENDENCY_SERVICE_MARKERS),
    }
    result["matched_domains"] = {
        key: value for key, value in matched_domains.items() if value
    }

    if selected_fix_id in {"fix-worker-1", "fix-queue-backpressure-1"}:
        if matched_domains["worker_overload"] and matched_domains["queue_backpressure"]:
            return _mark_ambiguous(
                result,
                reason="worker_queue_domain_overlap",
                domains=["worker_overload", "queue_backpressure"],
            )

    if selected_fix_id == "fix-queue-backpressure-1":
        if matched_domains["queue_backpressure"] and matched_domains["dependency_service"]:
            return _mark_ambiguous(
                result,
                reason="queue_dependency_service_overlap",
                domains=["queue_backpressure", "dependency_service"],
            )

    if selected_fix_id == "fix-optional-dep-1":
        if matched_domains["python_env"] and not matched_domains["optional_dependency"]:
            return _mark_ambiguous(
                result,
                reason="python_env_without_optional_dependency_context",
                domains=["python_env", "optional_dependency"],
            )

    if selected_fix_id == "fix-optional-integration-1":
        if matched_domains["python_env"] and not matched_domains["optional_integration"]:
            return _mark_ambiguous(
                result,
                reason="python_env_without_optional_integration_context",
                domains=["python_env", "optional_integration"],
            )

    if selected_fix_id == "fix-cache-1":
        if matched_domains["disk_full"] and not matched_domains["cache_write"]:
            return _mark_ambiguous(
                result,
                reason="disk_full_without_cache_write_context",
                domains=["disk_full", "cache_write"],
            )

    return result


def _event_evidence_text(event: ErrorEvent) -> str:
    return "\n".join(
        item
        for item in [
            getattr(event, "raw_excerpt", ""),
            getattr(event, "summary", ""),
            getattr(event, "signature", ""),
            " ".join(getattr(event, "matched_keywords", []) or []),
        ]
        if item
    ).lower()


def _matching_pattern_labels(
    text: str,
    patterns: dict[str, str],
) -> list[str]:
    return [
        label
        for label, pattern in patterns.items()
        if re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
    ]


def _mark_ambiguous(
    result: dict[str, Any],
    *,
    reason: str,
    domains: list[str],
) -> dict[str, Any]:
    result["ambiguous"] = True
    result["reason"] = reason
    result["conflicting_domains"] = domains
    result["policy"] = "manual_review_required_for_cross_domain_evidence"
    return result


WORKER_OVERLOAD_MARKERS = {
    "worker_overload": r"\bworker\b.*\boverload\b",
    "worker_pool_exhausted": r"\bworker\s+pool\s+exhausted\b",
    "too_many_workers": r"\btoo\s+many\s+workers\b",
    "worker_concurrency_high": r"\bworker[_ -]?concurrency\b.*\btoo\s+high\b",
    "worker_concurrency_exhausted": r"\bworker[_ -]?concurrency\b.*\bexhausted\b",
    "concurrency_too_high": r"\bconcurrency\b.*\btoo\s+high\b",
}

QUEUE_BACKPRESSURE_MARKERS = {
    "queue_backpressure": r"\bqueue\s+backpressure\b",
    "prefetch_too_high": r"\bprefetch(?:[_ -]?count)?\b.*\btoo\s+high\b",
    "max_inflight": r"\bmax[_ -]?inflight\b",
    "consumer_lag": r"\bconsumer\s+lag\b",
    "queue_consumer_backpressure": r"\bqueue\s+consumer\b.*\b(?:lag|backpressure|overloaded)\b",
}

PYTHON_ENV_MARKERS = {
    "module_not_found": r"\bmodulenotfounderror\b",
    "no_module_named": r"\bno\s+module\s+named\b",
    "import_error": r"\bimporterror\b",
    "interpreter_pip_mismatch": r"\bpython interpreter and pip path do not belong\b",
    "distribution_not_found": r"\bpkg_resources\.distributionnotfound\b",
}

OPTIONAL_DEPENDENCY_MARKERS = {
    "optional_dependency": r"\boptional\s+(?:dependency|plugin)\b",
    "missing_optional_dependency": r"\b(?:missing|unavailable)\s+optional\s+(?:dependency|plugin)\b",
    "internal_risk_sdk_unavailable": r"\binternal\s+risk\s+sdk\s+unavailable\b",
    "acme_internal_sdk": r"\bacme_internal_sdk\b",
    "local_rule_engine_fallback": r"\bfallback\b.*\blocal\s+rule\s+engine\b",
    "optional_dependency_fallback": r"\boptional\s+dependency\s+fallback\b",
}

OPTIONAL_INTEGRATION_MARKERS = {
    "optional_integration": r"\boptional\s+integration\b",
    "optional_webhook": r"\boptional\s+webhook\b",
    "risk_sdk_integration": r"\brisk\s+sdk\s+integration\b",
    "enrichment_client": r"\benrichment\s+client\b",
    "local_enrichment_fallback": r"\bfallback\b.*\blocal\s+enrichment\b",
}

CACHE_WRITE_MARKERS = {
    "cache_write": r"\bcache\b.*\b(?:write|persist|flush)\b",
    "write_cache": r"\b(?:failed|unable)\s+to\s+write\s+cache\b",
    "feature_cache": r"\bfeature\s+cache\b",
    "memory_cache_fallback": r"\bfallback\b.*\bin-memory\s+feature\s+cache\b",
}

DISK_FULL_MARKERS = {
    "no_space_left": r"\bno\s+space\s+left\s+on\s+device\b",
    "errno_28": r"\berrno\s*28\b",
    "disk_quota": r"\bdisk\s+quota\s+exceeded\b",
    "inode_exhausted": r"\binode\b.*\b(?:full|exhausted|no\s+space)\b",
}

DEPENDENCY_SERVICE_MARKERS = {
    "database_connection": r"\b(?:mysql|postgresql|postgres|database)\b.*\bconnection\b",
    "redis_connection": r"\bredis\b.*\bconnection\s+refused\b",
    "kafka_broker": r"\bkafka\b.*\bbroker\s+unavailable\b",
    "rabbitmq_connection": r"\brabbitmq\b.*\bconnection\s+timeout\b",
    "mq_connection": r"\bmq\b.*\bconnection\s+timeout\b",
}


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
