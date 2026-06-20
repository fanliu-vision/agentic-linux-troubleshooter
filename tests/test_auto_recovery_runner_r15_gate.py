from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from detectors import ErrorEvent
from monitors.project_registry import PolicyConfig, ProjectConfig
from recovery.auto_recovery_runner import AutoRecoveryRunner


class FakeSession:
    def __init__(self) -> None:
        self.evidence_items: list[SimpleNamespace] = []
        self.apply_calls: list[str] = []
        self.rerun_calls = 0
        self.latest_apply_success = False
        self.latest_rerun_success = False
        self.latest_remote_apply_success = False
        self.latest_remote_rerun_success = False
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
) -> ProjectConfig:
    return ProjectConfig(
        project_id="runner_gate",
        name="Runner Gate",
        mode="local",
        project_dir=".",
        run_command="python app.py",
        policy=PolicyConfig(
            auto_recover=True,
            allow_auto_apply=allow_auto_apply
            if allow_auto_apply is not None
            else ["fix-network-1", "fix-gpu-1"],
            rollback_on_failure=True,
            auto_recovery_policy_enabled=True,
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
