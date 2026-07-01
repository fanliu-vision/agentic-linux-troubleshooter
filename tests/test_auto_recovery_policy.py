from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from policies.auto_recovery_policy import (
    AutoRecoveryPolicy,
    EventTypePolicy,
    PolicyValidationError,
    RiskLevel,
    StrategyLayer,
    resolve_policy_for_event,
    validate_policy,
)


def make_policy(
    event_type_policies: dict[str, EventTypePolicy] | None = None,
    default_strategy: StrategyLayer | str = StrategyLayer.MANUAL_ESCALATION,
    dry_run_default: bool = True,
    action_allowlist: dict[str, object] | None = None,
    forbidden_actions: list[str] | None = None,
) -> AutoRecoveryPolicy:
    return AutoRecoveryPolicy(
        default_strategy=default_strategy,
        unknown_event_strategy=StrategyLayer.DIAGNOSE_ONLY,
        dry_run_default=dry_run_default,
        event_type_policies=event_type_policies or {
            "network_port": safe_network_policy(),
            "gpu_oom": safe_gpu_policy(),
            "process_crash": manual_policy(),
            "container_k8s": manual_policy(),
            "disk_full": manual_policy(),
            "python_env": manual_policy(),
            "auth_cert": manual_policy(),
        },
        action_allowlist=action_allowlist
        if action_allowlist is not None
        else {
            "fix-network-1": {"risk_level": "low"},
            "fix-gpu-1": {"risk_level": "low"},
        },
        forbidden_actions=forbidden_actions
        if forbidden_actions is not None
        else [
            "kill -9",
            "rm -rf",
            "pip install",
            "systemctl restart",
            "systemctl stop",
            "kubectl delete",
            "kubectl apply",
            "权限提升",
            "跨主机破坏性操作",
        ],
    )


def safe_network_policy(**overrides: object) -> EventTypePolicy:
    data = {
        "strategy_layer": StrategyLayer.SAFE_AUTO_RECOVER,
        "risk_level": RiskLevel.LOW,
        "confidence_required": 0.8,
        "allowed_fix_ids": ["fix-network-1"],
        "require_precheck": True,
        "require_rollback": True,
        "audit_required": True,
        "fallback_strategy": StrategyLayer.MANUAL_ESCALATION,
    }
    data.update(overrides)
    return EventTypePolicy(**data)


def safe_gpu_policy(**overrides: object) -> EventTypePolicy:
    data = {
        "strategy_layer": StrategyLayer.SAFE_AUTO_RECOVER,
        "risk_level": RiskLevel.LOW,
        "confidence_required": 0.8,
        "allowed_fix_ids": ["fix-gpu-1"],
        "require_precheck": True,
        "require_rollback": True,
        "audit_required": True,
        "fallback_strategy": StrategyLayer.MANUAL_ESCALATION,
    }
    data.update(overrides)
    return EventTypePolicy(**data)


def manual_policy(**overrides: object) -> EventTypePolicy:
    data = {
        "strategy_layer": StrategyLayer.MANUAL_ESCALATION,
        "risk_level": RiskLevel.HIGH,
        "allowed_fix_ids": [],
        "require_operator_confirmation": True,
        "audit_required": True,
        "fallback_strategy": StrategyLayer.MANUAL_ESCALATION,
    }
    data.update(overrides)
    return EventTypePolicy(**data)


def resolve(
    event_type: str,
    candidate_fix_id: str = "",
    policy: AutoRecoveryPolicy | None = None,
):
    return resolve_policy_for_event(
        event_type=event_type,
        fingerprint=f"fp-{event_type}",
        confidence=0.95,
        candidate_fix_id=candidate_fix_id,
        policy=policy or make_policy(),
    )


def test_valid_schema_passes() -> None:
    validated = validate_policy(make_policy())

    assert validated.event_type_policies["network_port"].strategy_layer == (
        StrategyLayer.SAFE_AUTO_RECOVER
    )


def test_unknown_strategy_layer_fails() -> None:
    policy = make_policy(
        event_type_policies={
            "network_port": safe_network_policy(strategy_layer="unknown_layer")
        }
    )

    with pytest.raises(PolicyValidationError):
        validate_policy(policy)


def test_default_strategy_safe_auto_recover_fails() -> None:
    with pytest.raises(PolicyValidationError):
        validate_policy(
            make_policy(default_strategy=StrategyLayer.SAFE_AUTO_RECOVER)
        )


def test_safe_auto_recover_without_allowed_fix_ids_fails() -> None:
    policy = make_policy(
        event_type_policies={
            "network_port": safe_network_policy(allowed_fix_ids=[])
        }
    )

    with pytest.raises(PolicyValidationError):
        validate_policy(policy)


def test_safe_auto_recover_without_precheck_fails() -> None:
    policy = make_policy(
        event_type_policies={
            "network_port": safe_network_policy(require_precheck=False)
        }
    )

    with pytest.raises(PolicyValidationError):
        validate_policy(policy)


def test_safe_auto_recover_without_rollback_fails() -> None:
    policy = make_policy(
        event_type_policies={
            "network_port": safe_network_policy(require_rollback=False)
        }
    )

    with pytest.raises(PolicyValidationError):
        validate_policy(policy)


def test_guarded_auto_recover_non_dry_run_fails() -> None:
    policy = make_policy(
        dry_run_default=False,
        event_type_policies={
            "network_port": EventTypePolicy(
                strategy_layer=StrategyLayer.GUARDED_AUTO_RECOVER,
                risk_level=RiskLevel.MEDIUM,
                allowed_fix_ids=["fix-network-1"],
                require_precheck=True,
                require_rollback=True,
                audit_required=True,
            )
        },
    )

    with pytest.raises(PolicyValidationError):
        validate_policy(policy)


def test_disabled_with_allowed_fix_ids_fails() -> None:
    policy = make_policy(
        event_type_policies={
            "network_port": EventTypePolicy(
                strategy_layer=StrategyLayer.DISABLED,
                risk_level=RiskLevel.CRITICAL,
                allowed_fix_ids=["fix-network-1"],
            )
        }
    )

    with pytest.raises(PolicyValidationError):
        validate_policy(policy)


def test_unknown_event_type_does_not_auto_recover() -> None:
    decision = resolve("unknown_future_domain", "fix-network-1")

    assert decision.strategy_layer == StrategyLayer.DIAGNOSE_ONLY
    assert not decision.auto_recover_allowed


def test_registry_rejects_safe_auto_recover_for_unregistered_event() -> None:
    policy = make_policy(
        event_type_policies={
            "unknown_future_domain": safe_network_policy(),
        }
    )

    with pytest.raises(PolicyValidationError, match="requires registry domain"):
        validate_policy(policy)


def test_registry_rejects_safe_auto_recover_for_manual_domain() -> None:
    policy = make_policy(
        event_type_policies={
            "disk_full": safe_network_policy(),
        }
    )

    with pytest.raises(PolicyValidationError, match="requires registry safe_auto_recover"):
        validate_policy(policy)


def test_registry_rejects_safe_auto_recover_with_wrong_fix_id() -> None:
    policy = make_policy(
        event_type_policies={
            "network_port": safe_network_policy(allowed_fix_ids=["fix-gpu-1"]),
        }
    )

    with pytest.raises(PolicyValidationError, match="must match registry"):
        validate_policy(policy)


def test_manual_domain_policy_never_selects_candidate_fix_id() -> None:
    decision = resolve("python_env", "fix-python-1")

    assert decision.strategy_layer == StrategyLayer.MANUAL_ESCALATION
    assert not decision.auto_recover_allowed
    assert decision.selected_fix_id == ""
    assert decision.operator_required


def test_explicit_event_policy_is_not_overridden_by_registry_manual_set() -> None:
    policy = make_policy(
        event_type_policies={
            "python_env": EventTypePolicy(
                strategy_layer=StrategyLayer.DIAGNOSE_ONLY,
                risk_level=RiskLevel.MEDIUM,
                allowed_fix_ids=[],
                audit_required=True,
                fallback_strategy=StrategyLayer.MANUAL_ESCALATION,
            )
        }
    )

    decision = resolve("python_env", "fix-python-1", policy=policy)

    assert decision.strategy_layer == StrategyLayer.DIAGNOSE_ONLY
    assert not decision.auto_recover_allowed
    assert decision.selected_fix_id == ""


def test_process_crash_resolves_to_manual_escalation() -> None:
    decision = resolve("process_crash")

    assert decision.strategy_layer == StrategyLayer.MANUAL_ESCALATION
    assert not decision.auto_recover_allowed
    assert decision.operator_required


def test_container_k8s_resolves_to_manual_escalation() -> None:
    decision = resolve("container_k8s")

    assert decision.strategy_layer == StrategyLayer.MANUAL_ESCALATION
    assert not decision.auto_recover_allowed
    assert decision.operator_required


def test_network_port_allowed_fix_resolves_to_safe_auto_recover_candidate() -> None:
    decision = resolve("network_port", "fix-network-1")

    assert decision.strategy_layer == StrategyLayer.SAFE_AUTO_RECOVER
    assert decision.auto_recover_allowed
    assert decision.selected_fix_id == "fix-network-1"
    assert decision.dry_run


def test_network_port_unknown_fix_id_downgrades() -> None:
    decision = resolve("network_port", "unknown-fix")

    assert decision.strategy_layer == StrategyLayer.MANUAL_ESCALATION
    assert not decision.auto_recover_allowed
    assert decision.selected_fix_id == ""
    assert decision.downgrade_reason == "candidate_fix_id_not_allowed_for_event_type"


def test_forbidden_action_hit_resolves_to_disabled() -> None:
    decision = resolve("network_port", "kill -9")

    assert decision.strategy_layer == StrategyLayer.DISABLED
    assert not decision.auto_recover_allowed
    assert decision.downgrade_reason == "candidate_fix_id_forbidden"


def test_decision_contains_audit_required() -> None:
    decision = resolve("network_port", "fix-network-1")

    assert decision.audit_required is True


def test_forbidden_action_in_action_allowlist_fails_validation() -> None:
    policy = make_policy(action_allowlist={"kill -9": {}})

    with pytest.raises(PolicyValidationError):
        validate_policy(policy)
