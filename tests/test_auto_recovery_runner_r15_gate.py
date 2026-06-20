from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from detectors import ErrorEvent
from monitors.project_registry import PolicyConfig, ProjectConfig
from policies import RemediationDecision
from recovery.auto_recovery_runner import AutoRecoveryResult, AutoRecoveryRunner


class FakeSession:
    def __init__(self) -> None:
        self.evidence_items: list[SimpleNamespace] = []
        self.apply_calls: list[str] = []
        self.rerun_calls = 0
        self.latest_apply_success = False
        self.latest_rerun_success = False
        self.latest_remote_apply_success = False
        self.latest_remote_rerun_success = False
        self.latest_apply_edit_records: list[dict] = []
        self.recorded_result = ""

    def add_evidence(
        self,
        *,
        content: str,
        source: str,
        title: str,
        issue_type: str,
    ) -> None:
        self.evidence_items.append(
            SimpleNamespace(
                content=content,
                source=source,
                title=title,
                issue_type=issue_type,
            )
        )

    def record_auto_recovery_result(
        self,
        *,
        result_text: str,
        action: str,
        fix_id: str,
        apply_success: bool,
        rerun_success: bool,
        rollback_executed: bool,
        rollback_success: bool = False,
        recovery_audit_record: dict | None = None,
        recovery_audit_summary: dict | None = None,
    ) -> None:
        self.recorded_result = result_text
        self.add_evidence(
            content=result_text,
            source="auto_recovery",
            title="Auto recovery result",
            issue_type="auto_recovery",
        )

    def generate_fix_plan(self) -> str:
        return "fake fix plan"

    def apply_fix(self, fix_id: str) -> str:
        self.apply_calls.append(fix_id)
        self.latest_apply_success = True
        self.latest_apply_edit_records = [
            {
                "success": True,
                "field_path": "metrics_port",
                "old_value": 9000,
                "new_value": 9101,
                "backup_path": "backup.json.bak",
                "diff_path": "change.diff",
            }
        ]
        return f"applied {fix_id}"

    def rerun_project(self) -> str:
        self.rerun_calls += 1
        self.latest_rerun_success = True
        return "rerun ok"

    def remote_apply_fix(self, fix_id: str, remote_project_dir: str) -> str:
        raise AssertionError("remote_apply_fix should not be used in local test")

    def rerun_remote_project(self, remote_project_dir: str = "") -> str:
        raise AssertionError("rerun_remote_project should not be used in local test")


def make_project(
    *,
    dry_run: bool = True,
    allow_auto_apply: list[str] | None = None,
    policy_enabled: bool = True,
) -> ProjectConfig:
    project_dir = Path(tempfile.mkdtemp(prefix="r15-runner-gate-"))
    (project_dir / "config.json").write_text(
        json.dumps(
            {
                "metrics_port": 9000,
                "batch_size": 16,
                "simulate_disk_full": True,
                "simulate_python_env_mismatch": True,
                "worker_concurrency": 8,
            }
        ),
        encoding="utf-8",
    )
    return ProjectConfig(
        project_id="runner_gate",
        name="Runner Gate",
        mode="local",
        project_dir=str(project_dir),
        run_command="python app.py",
        policy=PolicyConfig(
            auto_recover=True,
            allow_auto_apply=allow_auto_apply
            if allow_auto_apply is not None
            else ["fix-network-1", "fix-gpu-1"],
            rollback_on_failure=True,
            auto_recovery_policy_enabled=policy_enabled,
            auto_recovery_dry_run=dry_run,
        ),
    )


def make_event(event_type: str, issue_type: str) -> ErrorEvent:
    return ErrorEvent(
        event_type=event_type,
        issue_type=issue_type,
        severity="high",
        summary=f"{event_type} summary",
        source="test",
        raw_excerpt=f"{event_type} evidence",
        signature=f"runner-r15-{event_type}",
    )


def make_runner(project: ProjectConfig, session: FakeSession) -> AutoRecoveryRunner:
    runner = AutoRecoveryRunner(project=project, session=session)  # type: ignore[arg-type]
    runner._generate_report = (  # type: ignore[method-assign]
        lambda result, evidence_items=None: result.report_paths.append("fake-report.md")
    )
    return runner


def test_r15_dry_run_gate_does_not_call_apply_or_rerun() -> None:
    session = FakeSession()
    runner = make_runner(make_project(dry_run=True), session)

    result = runner.recover(make_event("network_port", "network_port"))

    assert result.r15_gate is not None
    assert result.r15_gate.dry_run
    assert not result.r15_gate.allowed_to_execute
    assert result.decision.action == "report_only"
    assert session.apply_calls == []
    assert session.rerun_calls == 0
    assert "not_run_r15_dry_run" in session.recorded_result
    assert "R15 forced recovery audit fields" in session.recorded_result
    assert "r15_execution_result" in session.recorded_result


def test_r15_live_gate_calls_existing_apply_path_when_explicitly_enabled() -> None:
    session = FakeSession()
    runner = make_runner(make_project(dry_run=False), session)

    result = runner.recover(make_event("network_port", "network_port"))

    assert result.r15_gate is not None
    assert result.r15_gate.allowed_to_execute
    assert result.r15_gate.would_execute
    assert session.apply_calls == ["fix-network-1"]
    assert session.rerun_calls == 1
    assert result.recovered
    assert result.recovery_audit_record()["execution_result"] == "executed_recovered"
    assert result.recovery_audit_record()["allowed_to_execute"] is True
    assert result.r15_gate.cooldown_result["reserved"] is True
    assert result.recovery_audit_record()["apply_edit_summary"][0]["field_path"] == "metrics_port"


def test_r15_gate_blocks_python_env_before_apply_even_if_legacy_allows() -> None:
    session = FakeSession()
    runner = make_runner(
        make_project(
            dry_run=False,
            allow_auto_apply=["fix-network-1", "fix-gpu-1", "fix-python-1"],
        ),
        session,
    )

    result = runner.recover(make_event("python_env", "python_env"))

    assert result.r15_gate is not None
    assert not result.r15_gate.allowed_to_execute
    assert result.r15_gate.strategy_layer == "manual_escalation"
    assert result.decision.action == "manual_escalation"
    assert session.apply_calls == []


def test_r15_policy_disabled_blocks_legacy_auto_recovery_passthrough() -> None:
    session = FakeSession()
    runner = make_runner(make_project(dry_run=False, policy_enabled=False), session)

    result = runner.recover(make_event("network_port", "network_port"))

    assert result.r15_gate is not None
    assert result.r15_gate.strategy_layer == "disabled"
    assert result.r15_gate.downgrade_reason == "r15_policy_disabled"
    assert not result.r15_gate.allowed_to_execute
    assert result.decision.action == "manual_escalation"
    assert session.apply_calls == []
    assert session.rerun_calls == 0


def test_manual_events_still_receive_r15_audit_fields() -> None:
    session = FakeSession()
    runner = make_runner(make_project(dry_run=False), session)

    result = runner.recover(make_event("process_crash", "process"))

    audit = result.recovery_audit_record()
    assert result.r15_gate is not None
    assert audit["strategy_layer"] == "manual_escalation"
    assert audit["action"] == "manual_escalation"
    assert audit["auto_recover_allowed"] is False
    assert audit["execution_result"] == "not_run_r15_gate_blocked"
    assert "R15 forced recovery audit fields" in session.recorded_result


def test_local_recovery_method_requires_r15_gate_authorization() -> None:
    session = FakeSession()
    runner = make_runner(make_project(dry_run=False), session)
    decision = RemediationDecision(
        action="auto_recover",
        fix_id="fix-network-1",
        reason="test",
    )
    result = AutoRecoveryResult(
        event_type="network_port",
        issue_type="network_port",
        decision=decision,
    )

    with pytest.raises(RuntimeError, match="r15_runtime_gate_required"):
        runner._run_local_recovery(decision, result)

    assert session.apply_calls == []


def test_runner_cooldown_blocks_repeated_live_execution() -> None:
    session = FakeSession()
    runner = make_runner(make_project(dry_run=False), session)
    event = make_event("network_port", "network_port")

    first = runner.recover(event)
    second = runner.recover(event)

    assert first.recovered
    assert first.r15_gate is not None
    assert first.r15_gate.cooldown_result["reserved"] is True
    assert second.r15_gate is not None
    assert second.r15_gate.downgrade_reason.startswith("cooldown_active")
    assert not second.r15_gate.allowed_to_execute
    assert second.decision.action == "report_only"
    assert session.apply_calls == ["fix-network-1"]
