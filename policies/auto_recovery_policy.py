from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


class StrategyLayer(str, Enum):
    DIAGNOSE_ONLY = "diagnose_only"
    MANUAL_ESCALATION = "manual_escalation"
    SAFE_AUTO_RECOVER = "safe_auto_recover"
    GUARDED_AUTO_RECOVER = "guarded_auto_recover"
    DISABLED = "disabled"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


class PolicyValidationError(ValueError):
    pass


@dataclass
class EventTypePolicy:
    strategy_layer: StrategyLayer | str
    risk_level: RiskLevel | str = RiskLevel.UNKNOWN
    confidence_required: float = 0.0
    allowed_fix_ids: list[str] = field(default_factory=list)
    forbidden_actions: list[str] = field(default_factory=list)
    require_precheck: bool = False
    require_rollback: bool = False
    require_operator_confirmation: bool = False
    cooldown: dict[str, Any] = field(default_factory=dict)
    rate_limits: dict[str, Any] = field(default_factory=dict)
    audit_required: bool = True
    fallback_strategy: StrategyLayer | str = StrategyLayer.MANUAL_ESCALATION
    dry_run: bool | None = None

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> EventTypePolicy:
        return cls(
            strategy_layer=data.get("strategy_layer", StrategyLayer.MANUAL_ESCALATION),
            risk_level=data.get("risk_level", RiskLevel.UNKNOWN),
            confidence_required=float(data.get("confidence_required", 0.0) or 0.0),
            allowed_fix_ids=list(data.get("allowed_fix_ids") or []),
            forbidden_actions=list(data.get("forbidden_actions") or []),
            require_precheck=bool(data.get("require_precheck", False)),
            require_rollback=bool(data.get("require_rollback", False)),
            require_operator_confirmation=bool(
                data.get("require_operator_confirmation", False)
            ),
            cooldown=dict(data.get("cooldown") or {}),
            rate_limits=dict(data.get("rate_limits") or {}),
            audit_required=bool(data.get("audit_required", True)),
            fallback_strategy=data.get(
                "fallback_strategy",
                StrategyLayer.MANUAL_ESCALATION,
            ),
            dry_run=data.get("dry_run"),
        )


@dataclass
class AutoRecoveryPolicy:
    schema_version: str = "r15.2"
    default_strategy: StrategyLayer | str = StrategyLayer.MANUAL_ESCALATION
    unknown_event_strategy: StrategyLayer | str = StrategyLayer.DIAGNOSE_ONLY
    dry_run_default: bool = True
    global_limits: dict[str, Any] = field(default_factory=dict)
    cooldown: dict[str, Any] = field(default_factory=dict)
    rate_limits: dict[str, Any] = field(default_factory=dict)
    event_type_policies: dict[str, EventTypePolicy | Mapping[str, Any]] = field(
        default_factory=dict
    )
    action_allowlist: dict[str, Any] = field(default_factory=dict)
    forbidden_actions: list[str] = field(default_factory=list)
    audit_required: bool = True

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> AutoRecoveryPolicy:
        raw = data.get("auto_recovery_policy", data)
        audit = raw.get("audit") or {}
        return cls(
            schema_version=str(raw.get("schema_version", "r15.2")),
            default_strategy=raw.get(
                "default_strategy",
                StrategyLayer.MANUAL_ESCALATION,
            ),
            unknown_event_strategy=raw.get(
                "unknown_event_strategy",
                StrategyLayer.DIAGNOSE_ONLY,
            ),
            dry_run_default=bool(raw.get("dry_run_default", True)),
            global_limits=dict(raw.get("global_limits") or {}),
            cooldown=dict(raw.get("cooldown") or {}),
            rate_limits=dict(raw.get("rate_limits") or {}),
            event_type_policies=dict(raw.get("event_type_policies") or {}),
            action_allowlist=dict(raw.get("action_allowlist") or {}),
            forbidden_actions=list(raw.get("forbidden_actions") or []),
            audit_required=bool(audit.get("required", raw.get("audit_required", True))),
        )


@dataclass
class AutoRecoveryDecision:
    event_type: str
    fingerprint: str
    strategy_layer: StrategyLayer
    auto_recover_allowed: bool
    dry_run: bool
    selected_fix_id: str = ""
    downgrade_reason: str = ""
    operator_required: bool = False
    audit_required: bool = True


SAFE_CANDIDATE_EVENT_TYPES = {
    "network_port",
    "gpu_oom",
}

MANUAL_ESCALATION_EVENT_TYPES = {
    "auth_cert",
    "config_error",
    "container_k8s",
    "dependency_service",
    "disk_full",
    "host_resource",
    "network_connectivity",
    "permission_denied",
    "process_crash",
    "process_kill",
    "python_env",
    "slurm",
}

AUTO_STRATEGY_LAYERS = {
    StrategyLayer.SAFE_AUTO_RECOVER,
    StrategyLayer.GUARDED_AUTO_RECOVER,
}

SAFE_FALLBACK_STRATEGIES = {
    StrategyLayer.DIAGNOSE_ONLY,
    StrategyLayer.MANUAL_ESCALATION,
    StrategyLayer.DISABLED,
}


def validate_policy(
    policy: AutoRecoveryPolicy | Mapping[str, Any],
) -> AutoRecoveryPolicy:
    normalized = _normalize_policy(policy)

    if normalized.default_strategy in AUTO_STRATEGY_LAYERS:
        raise PolicyValidationError(
            "default_strategy must not allow automatic recovery"
        )

    if normalized.unknown_event_strategy in AUTO_STRATEGY_LAYERS:
        raise PolicyValidationError(
            "unknown_event_strategy must not allow automatic recovery"
        )

    _validate_forbidden_actions_not_allowlisted(normalized)

    for event_type, event_policy in normalized.event_type_policies.items():
        _validate_event_policy(
            event_type=event_type,
            event_policy=event_policy,
            policy=normalized,
        )

    return normalized


def resolve_policy_for_event(
    *,
    event_type: str,
    fingerprint: str,
    confidence: float,
    candidate_fix_id: str,
    policy: AutoRecoveryPolicy | Mapping[str, Any],
) -> AutoRecoveryDecision:
    normalized = validate_policy(policy)
    candidate_fix_id = candidate_fix_id or ""

    event_policy = normalized.event_type_policies.get(event_type)
    forbidden_actions = list(normalized.forbidden_actions)
    if event_policy is not None:
        forbidden_actions.extend(event_policy.forbidden_actions)

    if _matches_forbidden_action(candidate_fix_id, forbidden_actions):
        return AutoRecoveryDecision(
            event_type=event_type,
            fingerprint=fingerprint,
            strategy_layer=StrategyLayer.DISABLED,
            auto_recover_allowed=False,
            dry_run=True,
            selected_fix_id="",
            downgrade_reason="candidate_fix_id_forbidden",
            operator_required=True,
            audit_required=True,
        )

    if event_policy is None:
        if event_type in MANUAL_ESCALATION_EVENT_TYPES:
            strategy = StrategyLayer.MANUAL_ESCALATION
            reason = "event_type_defaults_to_manual_escalation"
        else:
            strategy = normalized.unknown_event_strategy
            reason = "unknown_event_type"

        return _non_auto_decision(
            event_type=event_type,
            fingerprint=fingerprint,
            strategy_layer=strategy,
            dry_run=True,
            downgrade_reason=reason,
            audit_required=normalized.audit_required,
        )

    if event_policy.strategy_layer == StrategyLayer.DISABLED:
        return _non_auto_decision(
            event_type=event_type,
            fingerprint=fingerprint,
            strategy_layer=StrategyLayer.DISABLED,
            dry_run=True,
            downgrade_reason="event_type_policy_disabled",
            audit_required=event_policy.audit_required,
        )

    if event_type in MANUAL_ESCALATION_EVENT_TYPES:
        return _non_auto_decision(
            event_type=event_type,
            fingerprint=fingerprint,
            strategy_layer=StrategyLayer.MANUAL_ESCALATION,
            dry_run=True,
            downgrade_reason="event_type_defaults_to_manual_escalation",
            audit_required=event_policy.audit_required,
        )

    if event_policy.strategy_layer == StrategyLayer.DIAGNOSE_ONLY:
        return _non_auto_decision(
            event_type=event_type,
            fingerprint=fingerprint,
            strategy_layer=StrategyLayer.DIAGNOSE_ONLY,
            dry_run=True,
            downgrade_reason="event_type_policy_diagnose_only",
            audit_required=event_policy.audit_required,
        )

    if event_policy.strategy_layer == StrategyLayer.MANUAL_ESCALATION:
        return _non_auto_decision(
            event_type=event_type,
            fingerprint=fingerprint,
            strategy_layer=StrategyLayer.MANUAL_ESCALATION,
            dry_run=True,
            downgrade_reason="event_type_policy_manual_escalation",
            audit_required=event_policy.audit_required,
        )

    if event_policy.strategy_layer == StrategyLayer.GUARDED_AUTO_RECOVER:
        return _non_auto_decision(
            event_type=event_type,
            fingerprint=fingerprint,
            strategy_layer=StrategyLayer.GUARDED_AUTO_RECOVER,
            dry_run=True,
            downgrade_reason="guarded_auto_recover_dry_run_only",
            audit_required=event_policy.audit_required,
            operator_required=True,
        )

    if event_type not in SAFE_CANDIDATE_EVENT_TYPES:
        return _fallback_decision(
            event_type=event_type,
            fingerprint=fingerprint,
            event_policy=event_policy,
            reason="event_type_not_safe_auto_recover_candidate",
        )

    if confidence < event_policy.confidence_required:
        return _fallback_decision(
            event_type=event_type,
            fingerprint=fingerprint,
            event_policy=event_policy,
            reason="confidence_below_required",
        )

    if not candidate_fix_id:
        return _fallback_decision(
            event_type=event_type,
            fingerprint=fingerprint,
            event_policy=event_policy,
            reason="candidate_fix_id_missing",
        )

    if candidate_fix_id not in event_policy.allowed_fix_ids:
        return _fallback_decision(
            event_type=event_type,
            fingerprint=fingerprint,
            event_policy=event_policy,
            reason="candidate_fix_id_not_allowed_for_event_type",
        )

    if normalized.action_allowlist and candidate_fix_id not in normalized.action_allowlist:
        return _fallback_decision(
            event_type=event_type,
            fingerprint=fingerprint,
            event_policy=event_policy,
            reason="candidate_fix_id_not_in_action_allowlist",
        )

    return AutoRecoveryDecision(
        event_type=event_type,
        fingerprint=fingerprint,
        strategy_layer=StrategyLayer.SAFE_AUTO_RECOVER,
        auto_recover_allowed=True,
        dry_run=_effective_dry_run(normalized, event_policy),
        selected_fix_id=candidate_fix_id,
        downgrade_reason="",
        operator_required=event_policy.require_operator_confirmation,
        audit_required=event_policy.audit_required,
    )


def _normalize_policy(
    policy: AutoRecoveryPolicy | Mapping[str, Any],
) -> AutoRecoveryPolicy:
    if isinstance(policy, Mapping):
        policy = AutoRecoveryPolicy.from_mapping(policy)

    if not isinstance(policy, AutoRecoveryPolicy):
        raise PolicyValidationError("policy must be an AutoRecoveryPolicy or mapping")

    normalized_event_policies: dict[str, EventTypePolicy] = {}
    for event_type, raw_event_policy in policy.event_type_policies.items():
        event_policy = _normalize_event_policy(event_type, raw_event_policy)
        normalized_event_policies[event_type] = event_policy

    return AutoRecoveryPolicy(
        schema_version=policy.schema_version,
        default_strategy=_coerce_strategy_layer(
            policy.default_strategy,
            "default_strategy",
        ),
        unknown_event_strategy=_coerce_strategy_layer(
            policy.unknown_event_strategy,
            "unknown_event_strategy",
        ),
        dry_run_default=bool(policy.dry_run_default),
        global_limits=dict(policy.global_limits),
        cooldown=dict(policy.cooldown),
        rate_limits=dict(policy.rate_limits),
        event_type_policies=normalized_event_policies,
        action_allowlist=dict(policy.action_allowlist),
        forbidden_actions=list(policy.forbidden_actions),
        audit_required=bool(policy.audit_required),
    )


def _normalize_event_policy(
    event_type: str,
    event_policy: EventTypePolicy | Mapping[str, Any],
) -> EventTypePolicy:
    if isinstance(event_policy, Mapping):
        event_policy = EventTypePolicy.from_mapping(event_policy)

    if not isinstance(event_policy, EventTypePolicy):
        raise PolicyValidationError(
            f"event_type policy for {event_type!r} must be EventTypePolicy or mapping"
        )

    return EventTypePolicy(
        strategy_layer=_coerce_strategy_layer(
            event_policy.strategy_layer,
            f"event_type_policies.{event_type}.strategy_layer",
        ),
        risk_level=_coerce_risk_level(
            event_policy.risk_level,
            f"event_type_policies.{event_type}.risk_level",
        ),
        confidence_required=_coerce_confidence(
            event_policy.confidence_required,
            f"event_type_policies.{event_type}.confidence_required",
        ),
        allowed_fix_ids=list(event_policy.allowed_fix_ids),
        forbidden_actions=list(event_policy.forbidden_actions),
        require_precheck=bool(event_policy.require_precheck),
        require_rollback=bool(event_policy.require_rollback),
        require_operator_confirmation=bool(event_policy.require_operator_confirmation),
        cooldown=dict(event_policy.cooldown),
        rate_limits=dict(event_policy.rate_limits),
        audit_required=bool(event_policy.audit_required),
        fallback_strategy=_coerce_strategy_layer(
            event_policy.fallback_strategy,
            f"event_type_policies.{event_type}.fallback_strategy",
        ),
        dry_run=(
            None
            if event_policy.dry_run is None
            else bool(event_policy.dry_run)
        ),
    )


def _validate_event_policy(
    *,
    event_type: str,
    event_policy: EventTypePolicy,
    policy: AutoRecoveryPolicy,
) -> None:
    if event_policy.fallback_strategy not in SAFE_FALLBACK_STRATEGIES:
        raise PolicyValidationError(
            f"{event_type}: fallback_strategy must not allow automatic recovery"
        )

    if event_type == "unknown" and event_policy.strategy_layer in AUTO_STRATEGY_LAYERS:
        raise PolicyValidationError("unknown event_type must not auto recover")

    if event_policy.strategy_layer == StrategyLayer.DISABLED:
        if event_policy.allowed_fix_ids:
            raise PolicyValidationError(
                f"{event_type}: disabled policy must not contain allowed_fix_ids"
            )
        return

    if event_policy.strategy_layer == StrategyLayer.SAFE_AUTO_RECOVER:
        if not event_policy.allowed_fix_ids:
            raise PolicyValidationError(
                f"{event_type}: safe_auto_recover requires allowed_fix_ids"
            )
        if not event_policy.require_precheck:
            raise PolicyValidationError(
                f"{event_type}: safe_auto_recover requires precheck"
            )
        if not event_policy.require_rollback:
            raise PolicyValidationError(
                f"{event_type}: safe_auto_recover requires rollback"
            )
        if event_policy.risk_level != RiskLevel.LOW:
            raise PolicyValidationError(
                f"{event_type}: safe_auto_recover requires low risk_level"
            )
        if not event_policy.audit_required:
            raise PolicyValidationError(
                f"{event_type}: recovery candidates require audit"
            )

    if event_policy.strategy_layer == StrategyLayer.GUARDED_AUTO_RECOVER:
        if not _effective_dry_run(policy, event_policy):
            raise PolicyValidationError(
                f"{event_type}: guarded_auto_recover must default to dry-run"
            )
        if not event_policy.audit_required:
            raise PolicyValidationError(
                f"{event_type}: recovery candidates require audit"
            )


def _coerce_strategy_layer(value: StrategyLayer | str, field_name: str) -> StrategyLayer:
    if isinstance(value, StrategyLayer):
        return value

    try:
        return StrategyLayer(str(value))
    except ValueError as exc:
        raise PolicyValidationError(
            f"{field_name}: unknown strategy_layer {value!r}"
        ) from exc


def _coerce_risk_level(value: RiskLevel | str, field_name: str) -> RiskLevel:
    if isinstance(value, RiskLevel):
        return value

    try:
        return RiskLevel(str(value))
    except ValueError as exc:
        raise PolicyValidationError(
            f"{field_name}: unknown risk_level {value!r}"
        ) from exc


def _coerce_confidence(value: float, field_name: str) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError) as exc:
        raise PolicyValidationError(f"{field_name}: confidence must be numeric") from exc

    if not 0.0 <= confidence <= 1.0:
        raise PolicyValidationError(
            f"{field_name}: confidence must be between 0.0 and 1.0"
        )

    return confidence


def _validate_forbidden_actions_not_allowlisted(policy: AutoRecoveryPolicy) -> None:
    forbidden_actions = {_normalize_action(item) for item in policy.forbidden_actions}
    if not forbidden_actions:
        return

    for value in _walk_allowlist_values(policy.action_allowlist):
        if _normalize_action(value) in forbidden_actions:
            raise PolicyValidationError(
                f"forbidden action {value!r} must not appear in action_allowlist"
            )


def _walk_allowlist_values(value: Any) -> list[str]:
    if isinstance(value, Mapping):
        values: list[str] = []
        for key, item in value.items():
            values.extend(_walk_allowlist_values(key))
            values.extend(_walk_allowlist_values(item))
        return values

    if isinstance(value, (list, tuple, set)):
        values = []
        for item in value:
            values.extend(_walk_allowlist_values(item))
        return values

    if isinstance(value, str):
        return [value]

    return []


def _matches_forbidden_action(candidate: str, forbidden_actions: list[str]) -> bool:
    if not candidate:
        return False

    normalized_candidate = _normalize_action(candidate)
    return any(
        normalized_candidate == _normalize_action(action)
        for action in forbidden_actions
    )


def _normalize_action(value: str) -> str:
    return " ".join(str(value).strip().lower().split())


def _effective_dry_run(
    policy: AutoRecoveryPolicy,
    event_policy: EventTypePolicy,
) -> bool:
    if event_policy.dry_run is None:
        return bool(policy.dry_run_default)
    return bool(event_policy.dry_run)


def _fallback_decision(
    *,
    event_type: str,
    fingerprint: str,
    event_policy: EventTypePolicy,
    reason: str,
) -> AutoRecoveryDecision:
    return _non_auto_decision(
        event_type=event_type,
        fingerprint=fingerprint,
        strategy_layer=event_policy.fallback_strategy,
        dry_run=True,
        downgrade_reason=reason,
        audit_required=event_policy.audit_required,
    )


def _non_auto_decision(
    *,
    event_type: str,
    fingerprint: str,
    strategy_layer: StrategyLayer,
    dry_run: bool,
    downgrade_reason: str,
    audit_required: bool,
    operator_required: bool | None = None,
) -> AutoRecoveryDecision:
    if operator_required is None:
        operator_required = strategy_layer in {
            StrategyLayer.MANUAL_ESCALATION,
            StrategyLayer.DISABLED,
            StrategyLayer.GUARDED_AUTO_RECOVER,
        }

    return AutoRecoveryDecision(
        event_type=event_type,
        fingerprint=fingerprint,
        strategy_layer=strategy_layer,
        auto_recover_allowed=False,
        dry_run=dry_run,
        selected_fix_id="",
        downgrade_reason=downgrade_reason,
        operator_required=operator_required,
        audit_required=audit_required,
    )
