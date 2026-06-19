from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from policies.auto_recovery_policy import AutoRecoveryDecision, StrategyLayer
from recovery.guarded_auto_recover_dry_run import (
    evaluate_guarded_auto_recover_dry_run,
)


def policy_decision(
    *,
    event_type: str = "network_port",
    fingerprint: str = "fp",
    strategy_layer: StrategyLayer = StrategyLayer.SAFE_AUTO_RECOVER,
    auto_recover_allowed: bool = True,
    selected_fix_id: str = "fix-network-1",
) -> AutoRecoveryDecision:
    return AutoRecoveryDecision(
        event_type=event_type,
        fingerprint=fingerprint,
        strategy_layer=strategy_layer,
        auto_recover_allowed=auto_recover_allowed,
        dry_run=True,
        selected_fix_id=selected_fix_id,
        audit_required=True,
    )


def evaluate(
    *,
    event_type: str = "network_port",
    candidate_fix_id: str = "fix-network-1",
    strategy_layer: str | StrategyLayer = StrategyLayer.SAFE_AUTO_RECOVER,
    decision: AutoRecoveryDecision | None = None,
    precheck_result=True,
    cooldown_result=True,
    rollback_available: bool = True,
    action_description: str = "",
):
    return evaluate_guarded_auto_recover_dry_run(
        event_type=event_type,
        fingerprint=f"fp-{event_type}",
        candidate_fix_id=candidate_fix_id,
        strategy_layer=strategy_layer,
        policy_decision=decision
        or policy_decision(
            event_type=event_type,
            fingerprint=f"fp-{event_type}",
            selected_fix_id=candidate_fix_id,
        ),
        precheck_result=precheck_result,
        cooldown_result=cooldown_result,
        rollback_available=rollback_available,
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


def test_guarded_dry_run_never_executes_by_default() -> None:
    result = evaluate()

    assert result.strategy_layer == "guarded_auto_recover"
    assert result.allowed_by_policy
    assert result.dry_run
    assert result.would_execute is False
    assert result.audit_record["execution_result"] == "not_run_guarded_dry_run"


def test_process_crash_does_not_allow_guarded_execution() -> None:
    result = evaluate(event_type="process_crash", candidate_fix_id="")

    assert result.strategy_layer == "manual_escalation"
    assert not result.allowed_by_policy
    assert not result.would_execute


def test_container_k8s_does_not_allow_kubectl() -> None:
    result = evaluate(
        event_type="container_k8s",
        candidate_fix_id="kubectl delete pod/demo",
        action_description="kubectl delete pod/demo",
    )

    assert result.strategy_layer == "disabled"
    assert result.downgrade_reason == "forbidden_action"
    assert not result.allowed_by_policy


def test_disk_full_does_not_allow_rm() -> None:
    result = evaluate(
        event_type="disk_full",
        candidate_fix_id="cleanup",
        action_description="rm -rf /tmp/cache",
    )

    assert result.strategy_layer == "disabled"
    assert result.downgrade_reason == "forbidden_action"


def test_python_env_does_not_allow_pip_install() -> None:
    result = evaluate(
        event_type="python_env",
        candidate_fix_id="fix-python-1",
        action_description="pip install missing-package",
    )

    assert result.strategy_layer == "disabled"
    assert result.downgrade_reason == "forbidden_action"


def test_network_port_fix_network_can_generate_dry_run_candidate() -> None:
    result = evaluate(event_type="network_port", candidate_fix_id="fix-network-1")

    assert result.strategy_layer == "guarded_auto_recover"
    assert result.allowed_by_policy
    assert result.dry_run
    assert not result.would_execute


def test_gpu_oom_existing_fix_can_generate_dry_run_candidate() -> None:
    result = evaluate(
        event_type="gpu_oom",
        candidate_fix_id="fix-gpu-1",
        action_description="batch_size safe adjustment",
    )

    assert result.strategy_layer == "guarded_auto_recover"
    assert result.allowed_by_policy
    assert result.candidate_fix_id == "fix-gpu-1"
    assert not result.would_execute


def test_forbidden_action_is_blocked() -> None:
    result = evaluate(candidate_fix_id="systemctl restart demo.service")

    assert result.strategy_layer == "disabled"
    assert result.downgrade_reason == "forbidden_action"
    assert not result.allowed_by_policy


def test_rollback_unavailable_blocks_candidate() -> None:
    result = evaluate(rollback_available=False)

    assert result.strategy_layer == "manual_escalation"
    assert result.downgrade_reason == "rollback_unavailable"
    assert not result.allowed_by_policy


def test_precheck_failure_blocks_candidate() -> None:
    result = evaluate(precheck_result={"passed": False, "reason": "target_mismatch"})

    assert result.strategy_layer == "manual_escalation"
    assert result.downgrade_reason == "precheck_failed"
    assert not result.allowed_by_policy


def test_cooldown_failure_blocks_candidate() -> None:
    result = evaluate(cooldown_result={"allowed": False, "reason": "cooldown"})

    assert result.strategy_layer == "manual_escalation"
    assert result.downgrade_reason == "cooldown_not_satisfied"
    assert not result.allowed_by_policy


def test_audit_record_fields_are_complete() -> None:
    result = evaluate()
    expected_fields = {
        "event_type",
        "fingerprint",
        "strategy_layer",
        "candidate_fix_id",
        "would_execute",
        "dry_run",
        "allowed_by_policy",
        "precheck_passed",
        "precheck_result",
        "cooldown_allowed",
        "cooldown_result",
        "rollback_available",
        "operator_required",
        "downgrade_reason",
        "forbidden_action",
        "policy_decision",
        "execution_result",
        "rollback_result",
        "created_at",
    }

    assert expected_fields <= set(result.audit_record)
    assert result.audit_record["execution_result"] == "not_run_guarded_dry_run"


def test_guarded_dry_run_does_not_write_real_state_or_outputs() -> None:
    state_before = snapshot_files(PROJECT_ROOT / "state")
    outputs_before = snapshot_files(PROJECT_ROOT / "outputs")

    result = evaluate()

    assert result.dry_run
    assert snapshot_files(PROJECT_ROOT / "state") == state_before
    assert snapshot_files(PROJECT_ROOT / "outputs") == outputs_before


def test_guarded_dry_run_does_not_call_auto_recovery_runner(monkeypatch) -> None:
    from recovery.auto_recovery_runner import AutoRecoveryRunner

    def fail_if_called(*args, **kwargs):
        raise AssertionError("AutoRecoveryRunner.recover must not be called")

    monkeypatch.setattr(AutoRecoveryRunner, "recover", fail_if_called)

    result = evaluate()

    assert result.dry_run
    assert not result.would_execute


def test_unknown_event_type_is_diagnose_or_manual_only() -> None:
    result = evaluate(
        event_type="unknown_event_type",
        candidate_fix_id="fix-network-1",
        strategy_layer=StrategyLayer.DIAGNOSE_ONLY,
    )

    assert result.strategy_layer in {"diagnose_only", "manual_escalation"}
    assert not result.allowed_by_policy
    assert not result.would_execute
