from __future__ import annotations

import copy
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from policies.auto_recovery_policy_dry_run import (
    load_policy_schema,
    run_policy_dry_run,
    write_policy_dry_run_report,
)


EXAMPLE_POLICY_PATH = (
    PROJECT_ROOT / "docs" / "examples" / "r15_policy_schema_example.yaml"
)


def sample_events() -> list[dict[str, object]]:
    return [
        {
            "event_type": "network_port",
            "fingerprint": "fp-network-safe",
            "confidence": 0.95,
            "candidate_fix_id": "fix-network-1",
        },
        {
            "event_type": "network_port",
            "fingerprint": "fp-network-unknown",
            "confidence": 0.95,
            "candidate_fix_id": "unknown-fix",
        },
        {
            "event_type": "gpu_oom",
            "fingerprint": "fp-gpu-safe",
            "confidence": 0.95,
            "candidate_fix_id": "fix-gpu-1",
        },
        {
            "event_type": "process_crash",
            "fingerprint": "fp-process-crash",
            "confidence": 0.95,
            "candidate_fix_id": "",
        },
        {
            "event_type": "container_k8s",
            "fingerprint": "fp-container",
            "confidence": 0.95,
            "candidate_fix_id": "",
        },
        {
            "event_type": "disk_full",
            "fingerprint": "fp-disk",
            "confidence": 0.95,
            "candidate_fix_id": "",
        },
        {
            "event_type": "auth_cert",
            "fingerprint": "fp-auth",
            "confidence": 0.95,
            "candidate_fix_id": "",
        },
        {
            "event_type": "unknown_event_type",
            "fingerprint": "fp-unknown",
            "confidence": 0.95,
            "candidate_fix_id": "fix-network-1",
        },
        {
            "event_type": "network_port",
            "fingerprint": "fp-forbidden",
            "confidence": 0.95,
            "candidate_fix_id": "kill -9",
        },
    ]


def load_example_policy() -> dict[str, object]:
    return load_policy_schema(EXAMPLE_POLICY_PATH)


def decisions_by_fingerprint(result):
    return {decision.fingerprint: decision for decision in result.decisions}


def snapshot_files(path: Path) -> set[Path]:
    if not path.exists():
        return set()
    return {
        item.relative_to(path)
        for item in path.rglob("*")
        if item.is_file()
    }


def test_valid_policy_dry_run_succeeds() -> None:
    result = run_policy_dry_run(load_example_policy(), sample_events())

    assert result.policy_valid
    assert result.validation_errors == []
    assert len(result.decisions) == len(sample_events())
    assert "R15-4 Policy Schema Dry-Run Report" in result.report_markdown


def test_example_yaml_sample_events_can_be_used_for_dry_run() -> None:
    policy = load_example_policy()

    result = run_policy_dry_run(policy, policy["r15_dry_run_sample_events"])

    assert result.policy_valid
    assert result.summary["total_events"] == len(policy["r15_dry_run_sample_events"])


def test_invalid_policy_dry_run_returns_validation_errors() -> None:
    policy = load_example_policy()
    policy["auto_recovery_policy"]["default_strategy"] = "safe_auto_recover"

    result = run_policy_dry_run(policy, sample_events())

    assert not result.policy_valid
    assert result.validation_errors
    assert result.decisions == []


def test_network_port_allowed_fix_returns_safe_auto_recover_candidate() -> None:
    result = run_policy_dry_run(load_example_policy(), sample_events())
    decision = decisions_by_fingerprint(result)["fp-network-safe"]

    assert decision.strategy_layer == "safe_auto_recover"
    assert decision.auto_recover_allowed
    assert decision.dry_run
    assert decision.selected_fix_id == "fix-network-1"


def test_network_port_unknown_fix_ignored() -> None:
    result = run_policy_dry_run(load_example_policy(), sample_events())
    decision = decisions_by_fingerprint(result)["fp-network-unknown"]

    assert decision.strategy_layer == "manual_escalation"
    assert not decision.auto_recover_allowed
    assert decision.selected_fix_id == ""
    assert decision.downgrade_reason == "candidate_fix_id_not_allowed_for_event_type"


def test_process_crash_returns_manual_escalation() -> None:
    result = run_policy_dry_run(load_example_policy(), sample_events())
    decision = decisions_by_fingerprint(result)["fp-process-crash"]

    assert decision.strategy_layer == "manual_escalation"
    assert not decision.auto_recover_allowed
    assert decision.operator_required


def test_container_k8s_returns_manual_escalation() -> None:
    result = run_policy_dry_run(load_example_policy(), sample_events())
    decision = decisions_by_fingerprint(result)["fp-container"]

    assert decision.strategy_layer == "manual_escalation"
    assert not decision.auto_recover_allowed
    assert decision.operator_required


def test_unknown_event_type_does_not_auto_recover() -> None:
    result = run_policy_dry_run(load_example_policy(), sample_events())
    decision = decisions_by_fingerprint(result)["fp-unknown"]

    assert decision.strategy_layer == "diagnose_only"
    assert not decision.auto_recover_allowed


def test_forbidden_action_returns_disabled() -> None:
    result = run_policy_dry_run(load_example_policy(), sample_events())
    decision = decisions_by_fingerprint(result)["fp-forbidden"]

    assert decision.strategy_layer == "disabled"
    assert not decision.auto_recover_allowed
    assert decision.downgrade_reason == "candidate_fix_id_forbidden"


def test_dry_run_does_not_write_real_state_or_outputs() -> None:
    state_before = snapshot_files(PROJECT_ROOT / "state")
    outputs_before = snapshot_files(PROJECT_ROOT / "outputs")

    result = run_policy_dry_run(load_example_policy(), sample_events())

    assert result.policy_valid
    assert snapshot_files(PROJECT_ROOT / "state") == state_before
    assert snapshot_files(PROJECT_ROOT / "outputs") == outputs_before


def test_summary_counts_strategy_layers() -> None:
    result = run_policy_dry_run(load_example_policy(), sample_events())

    assert result.summary["total_events"] == len(sample_events())
    assert result.summary["auto_recover_allowed_count"] == 2
    assert result.summary["dry_run_auto_recover_count"] == 2
    assert result.summary["by_strategy_layer"]["safe_auto_recover"] == 2
    assert result.summary["by_strategy_layer"]["manual_escalation"] == 3
    assert result.summary["by_strategy_layer"]["diagnose_only"] == 3
    assert result.summary["by_strategy_layer"]["disabled"] == 1


def test_dry_run_report_can_write_to_tmp_only(tmp_path: Path) -> None:
    result = run_policy_dry_run(load_example_policy(), sample_events())

    report_path = write_policy_dry_run_report(result, tmp_path / "dry_run.md")

    assert report_path.read_text(encoding="utf-8").startswith(
        "# R15-4 Policy Schema Dry-Run Report"
    )


def test_dry_run_report_rejects_real_outputs_path() -> None:
    result = run_policy_dry_run(load_example_policy(), sample_events())

    output_path = PROJECT_ROOT / "outputs" / "r15_dry_run_report.md"
    try:
        write_policy_dry_run_report(result, output_path)
    except ValueError as exc:
        assert "only be written" in str(exc)
    else:
        raise AssertionError("expected real outputs path to be rejected")


def test_invalid_sample_event_returns_validation_error() -> None:
    events = copy.deepcopy(sample_events())
    del events[0]["event_type"]

    result = run_policy_dry_run(load_example_policy(), events)

    assert not result.policy_valid
    assert result.validation_errors
