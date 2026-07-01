from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from policies.auto_recovery_policy import (
    StrategyLayer,
    resolve_policy_for_event,
    validate_policy,
)
from policies.auto_recovery_policy_dry_run import (
    load_policy_schema,
    run_policy_dry_run,
)
from recovery.guarded_auto_recover_dry_run import (
    evaluate_guarded_auto_recover_dry_run,
)
from safe_recovery.registry import safe_fix_id_for_event_type


EXAMPLE_POLICY_PATH = (
    PROJECT_ROOT / "docs" / "examples" / "r15_policy_schema_example.yaml"
)
EXISTING_NETWORK_FIX_ID = "fix-network-1"
EXISTING_GPU_FIX_ID = safe_fix_id_for_event_type("gpu_oom")


def load_policy() -> dict[str, object]:
    return load_policy_schema(EXAMPLE_POLICY_PATH)


def resolve_event(
    *,
    event_type: str,
    fingerprint: str,
    candidate_fix_id: str,
    confidence: float = 0.95,
):
    policy = validate_policy(load_policy())
    return resolve_policy_for_event(
        event_type=event_type,
        fingerprint=fingerprint,
        confidence=confidence,
        candidate_fix_id=candidate_fix_id,
        policy=policy,
    )


def guarded_from_decision(decision, *, action_description: str = ""):
    return evaluate_guarded_auto_recover_dry_run(
        event_type=decision.event_type,
        fingerprint=decision.fingerprint,
        candidate_fix_id=decision.selected_fix_id or "",
        strategy_layer=decision.strategy_layer,
        policy_decision=decision,
        precheck_result={"passed": True, "reason": "controlled_validation"},
        cooldown_result={"allowed": True, "reason": "controlled_validation"},
        rollback_available=True,
        action_description=action_description,
    )


def snapshot_files(path: Path) -> set[Path]:
    if not path.exists():
        return set()
    return {
        item.relative_to(path)
        for item in path.rglob("*")
        if item.is_file()
    }


def test_network_port_existing_safe_candidate_policy_dry_run_and_guarded_audit() -> None:
    decision = resolve_event(
        event_type="network_port",
        fingerprint="test-network-port-safe",
        candidate_fix_id=EXISTING_NETWORK_FIX_ID,
    )

    assert decision.strategy_layer == StrategyLayer.SAFE_AUTO_RECOVER
    assert decision.auto_recover_allowed
    assert decision.selected_fix_id == EXISTING_NETWORK_FIX_ID

    dry_run = run_policy_dry_run(
        load_policy(),
        [
            {
                "event_type": "network_port",
                "fingerprint": "test-network-port-safe",
                "confidence": 0.95,
                "candidate_fix_id": EXISTING_NETWORK_FIX_ID,
            }
        ],
    )
    assert dry_run.policy_valid
    assert dry_run.decisions[0].strategy_layer == "safe_auto_recover"
    assert dry_run.decisions[0].auto_recover_allowed
    assert dry_run.decisions[0].dry_run

    guarded = guarded_from_decision(decision)
    assert guarded.allowed_by_policy
    assert guarded.audit_record["event_type"] == "network_port"
    assert guarded.audit_record["candidate_fix_id"] == EXISTING_NETWORK_FIX_ID
    assert guarded.dry_run
    assert not guarded.would_execute


def test_gpu_oom_existing_batch_size_candidate_policy_dry_run_and_guarded_audit() -> None:
    assert EXISTING_GPU_FIX_ID == "fix-gpu-1"

    decision = resolve_event(
        event_type="gpu_oom",
        fingerprint="test-gpu-oom-safe",
        candidate_fix_id=EXISTING_GPU_FIX_ID,
    )

    assert decision.strategy_layer == StrategyLayer.SAFE_AUTO_RECOVER
    assert decision.auto_recover_allowed
    assert decision.selected_fix_id == EXISTING_GPU_FIX_ID

    dry_run = run_policy_dry_run(
        load_policy(),
        [
            {
                "event_type": "gpu_oom",
                "fingerprint": "test-gpu-oom-safe",
                "confidence": 0.95,
                "candidate_fix_id": EXISTING_GPU_FIX_ID,
            }
        ],
    )
    assert dry_run.policy_valid
    assert dry_run.decisions[0].strategy_layer == "safe_auto_recover"
    assert dry_run.decisions[0].auto_recover_allowed
    assert dry_run.decisions[0].dry_run

    guarded = guarded_from_decision(
        decision,
        action_description="existing batch_size safe fix dry-run",
    )
    assert guarded.allowed_by_policy
    assert guarded.audit_record["event_type"] == "gpu_oom"
    assert guarded.audit_record["candidate_fix_id"] == EXISTING_GPU_FIX_ID
    assert guarded.dry_run
    assert not guarded.would_execute


def test_unknown_fix_id_downgrades() -> None:
    decision = resolve_event(
        event_type="network_port",
        fingerprint="test-network-port-unknown-fix",
        candidate_fix_id="unknown_fix_id",
    )

    assert not decision.auto_recover_allowed
    assert decision.strategy_layer in {
        StrategyLayer.MANUAL_ESCALATION,
        StrategyLayer.DIAGNOSE_ONLY,
    }
    assert decision.downgrade_reason


@pytest.mark.parametrize(
    "event_type",
    [
        "process_crash",
        "container_k8s",
        "disk_full",
        "python_env",
        "auth_cert",
    ],
)
def test_high_risk_faults_remain_manual_or_diagnose(event_type: str) -> None:
    decision = resolve_event(
        event_type=event_type,
        fingerprint=f"test-{event_type}",
        candidate_fix_id="",
    )

    assert not decision.auto_recover_allowed
    assert decision.strategy_layer in {
        StrategyLayer.MANUAL_ESCALATION,
        StrategyLayer.DIAGNOSE_ONLY,
    }

    guarded = evaluate_guarded_auto_recover_dry_run(
        event_type=event_type,
        fingerprint=f"test-{event_type}",
        candidate_fix_id="",
        strategy_layer=decision.strategy_layer,
        policy_decision=decision,
        precheck_result={"passed": True},
        cooldown_result={"allowed": True},
        rollback_available=True,
    )
    assert not guarded.allowed_by_policy
    assert not guarded.would_execute


@pytest.mark.parametrize(
    "action_description",
    [
        "systemctl restart demo.service",
        "kubectl delete pod/demo",
        "rm -rf /tmp/cache",
        "pip install missing-package",
        "kill -9 1234",
    ],
)
def test_forbidden_actions_are_blocked(action_description: str) -> None:
    decision = resolve_event(
        event_type="network_port",
        fingerprint=f"test-forbidden-{action_description.split()[0]}",
        candidate_fix_id=EXISTING_NETWORK_FIX_ID,
    )

    guarded = evaluate_guarded_auto_recover_dry_run(
        event_type="network_port",
        fingerprint=decision.fingerprint,
        candidate_fix_id=decision.selected_fix_id,
        strategy_layer=decision.strategy_layer,
        policy_decision=decision,
        precheck_result={"passed": True},
        cooldown_result={"allowed": True},
        rollback_available=True,
        action_description=action_description,
    )

    assert guarded.strategy_layer in {"disabled", "manual_escalation"}
    assert not guarded.allowed_by_policy
    assert not guarded.would_execute
    assert guarded.downgrade_reason == "forbidden_action"


def test_controlled_validation_does_not_write_real_state_or_outputs() -> None:
    state_before = snapshot_files(PROJECT_ROOT / "state")
    outputs_before = snapshot_files(PROJECT_ROOT / "outputs")

    decision = resolve_event(
        event_type="network_port",
        fingerprint="test-network-port-no-write",
        candidate_fix_id=EXISTING_NETWORK_FIX_ID,
    )
    guarded = guarded_from_decision(decision)

    assert guarded.dry_run
    assert not guarded.would_execute
    assert snapshot_files(PROJECT_ROOT / "state") == state_before
    assert snapshot_files(PROJECT_ROOT / "outputs") == outputs_before


def test_controlled_validation_does_not_call_auto_recovery_runner(monkeypatch) -> None:
    from recovery.auto_recovery_runner import AutoRecoveryRunner

    def fail_if_called(*args, **kwargs):
        raise AssertionError("AutoRecoveryRunner.recover must not be called")

    monkeypatch.setattr(AutoRecoveryRunner, "recover", fail_if_called)

    decision = resolve_event(
        event_type="network_port",
        fingerprint="test-network-port-no-runner",
        candidate_fix_id=EXISTING_NETWORK_FIX_ID,
    )
    guarded = guarded_from_decision(decision)

    assert guarded.dry_run
    assert not guarded.would_execute
