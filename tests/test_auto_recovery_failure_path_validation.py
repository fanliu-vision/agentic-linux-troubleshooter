from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from detectors import ErrorEvent
from monitors.cycle_summary_reporter import CycleEventRecord, CycleSummaryReporter
from monitors.project_registry import PolicyConfig, ProjectConfig
from notifiers.notification_manager import NotificationManager
from recovery.auto_recovery_runner import AutoRecoveryRunner


class FailurePathSession:
    def __init__(self, *, rollback_success: bool) -> None:
        self.rollback_success = rollback_success
        self.evidence_items: list[SimpleNamespace] = []
        self.apply_calls: list[str] = []
        self.rerun_calls = 0
        self.rollback_calls = 0
        self.latest_apply_success = False
        self.latest_rerun_success = False
        self.latest_remote_apply_success = False
        self.latest_remote_rerun_success = False
        self.latest_rollback_success = False
        self.latest_apply_edit_records: list[dict] = []
        self.latest_rollback_edit_records: list[dict] = []
        self.recorded_audit_record: dict = {}
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
        self.recorded_audit_record = dict(recovery_audit_record or {})
        self.add_evidence(
            content=result_text,
            source="auto_recovery",
            title="Auto recovery result",
            issue_type="auto_recovery",
        )

    def generate_fix_plan(self) -> str:
        return "fake failure-path fix plan"

    def apply_fix(self, fix_id: str) -> str:
        self.apply_calls.append(fix_id)
        self.latest_apply_success = True
        self.latest_apply_edit_records = [
            {
                "success": True,
                "field_path": "metrics_port",
                "old_value": 9000,
                "new_value": 9101,
                "backup_path": "config.json.bak",
                "diff_path": "config.diff",
            }
        ]
        return "apply ok"

    def rerun_project(self) -> str:
        self.rerun_calls += 1
        self.latest_rerun_success = False
        return "rerun failed"

    def rollback_latest_apply(self) -> str:
        self.rollback_calls += 1
        self.latest_rollback_success = self.rollback_success
        self.latest_rollback_edit_records = [
            {
                "success": self.rollback_success,
                "field_path": "metrics_port",
                "old_value": 9101,
                "new_value": 9000,
                "backup_path": "config.json.bak",
                "diff_path": "rollback.diff" if self.rollback_success else "",
            }
        ]
        return "rollback ok" if self.rollback_success else "rollback failed"

    def remote_apply_fix(self, fix_id: str, remote_project_dir: str) -> str:
        raise AssertionError("remote_apply_fix should not be used")

    def rerun_remote_project(self, remote_project_dir: str = "") -> str:
        raise AssertionError("rerun_remote_project should not be used")

    def remote_rollback_latest_apply(self) -> str:
        raise AssertionError("remote_rollback_latest_apply should not be used")


def make_project() -> ProjectConfig:
    project_dir = Path(tempfile.mkdtemp(prefix="r15-failure-path-"))
    (project_dir / "config.json").write_text(
        json.dumps({"metrics_port": 9000}),
        encoding="utf-8",
    )
    return ProjectConfig(
        project_id="failure_path",
        name="Failure Path",
        mode="local",
        project_dir=str(project_dir),
        run_command="python app.py",
        policy=PolicyConfig(
            auto_recover=True,
            allow_auto_apply=["fix-network-1"],
            rollback_on_failure=True,
            auto_recovery_policy_enabled=True,
            auto_recovery_dry_run=False,
        ),
    )


def make_event() -> ErrorEvent:
    return ErrorEvent(
        event_type="network_port",
        issue_type="network_port",
        severity="medium",
        summary="port conflict",
        source="test",
        raw_excerpt="OSError: [Errno 98] Address already in use",
        signature="r15-failure-path-network-port",
    )


def make_runner(project: ProjectConfig, session: FailurePathSession) -> AutoRecoveryRunner:
    runner = AutoRecoveryRunner(project=project, session=session)  # type: ignore[arg-type]
    runner._generate_report = (  # type: ignore[method-assign]
        lambda result, evidence_items=None: result.report_paths.append("fake-report.md")
    )
    return runner


def test_rerun_failure_rolls_back_and_records_successful_rollback_audit() -> None:
    session = FailurePathSession(rollback_success=True)
    runner = make_runner(make_project(), session)

    result = runner.recover(make_event())

    audit = result.recovery_audit_record()
    assert session.apply_calls == ["fix-network-1"]
    assert session.rerun_calls == 1
    assert session.rollback_calls == 1
    assert result.apply_success
    assert not result.rerun_success
    assert result.rollback_executed
    assert result.rollback_success
    assert not result.recovered
    assert result.event_recovery_status == "rollback_done"
    assert audit["execution_result"] == "executed_rerun_failed"
    assert audit["rollback_result"] == "rollback_succeeded"
    assert audit["apply_edit_summary"][0]["old_value"] == 9000
    assert audit["rollback_edit_summary"][0]["new_value"] == 9000
    assert session.recorded_audit_record["rollback_success"] is True


def test_rerun_failure_records_failed_rollback_and_alert_status() -> None:
    session = FailurePathSession(rollback_success=False)
    project = make_project()
    runner = make_runner(project, session)

    result = runner.recover(make_event())
    message = NotificationManager(project).build_message_from_recovery(make_event(), result)

    audit = result.recovery_audit_record()
    assert result.rollback_executed
    assert not result.rollback_success
    assert result.event_recovery_status == "rollback_failed"
    assert audit["rollback_result"] == "rollback_failed"
    assert audit["residual_risk_status"] == "requires_manual_review"
    assert message.status == "rollback_failed"
    assert message.rollback_success is False
    assert message.rollback_result == "rollback_failed"
    assert "[ROLLBACK_FAILED]" in message.message


def test_failed_recovery_reserves_cooldown_and_blocks_immediate_repeat() -> None:
    session = FailurePathSession(rollback_success=True)
    runner = make_runner(make_project(), session)
    event = make_event()

    first = runner.recover(event)
    second = runner.recover(event)

    assert first.rollback_executed
    assert first.r15_gate is not None
    assert first.r15_gate.cooldown_result["reserved"] is True
    assert second.r15_gate is not None
    assert second.r15_gate.downgrade_reason.startswith("cooldown_active")
    assert second.decision.action == "report_only"
    assert session.apply_calls == ["fix-network-1"]
    assert session.rollback_calls == 1


def test_cycle_summary_marks_rollback_failure_as_highest_priority() -> None:
    project = make_project()
    reporter = CycleSummaryReporter(project)
    records = [
        CycleEventRecord(
            event_type="network_port",
            issue_type="network_port",
            severity="medium",
            summary="port recovered",
            source="test",
            fingerprint="ok",
            action="auto_recover",
            fix_id="fix-network-1",
            apply_success=True,
            rerun_success=True,
            recovered=True,
        ),
        CycleEventRecord(
            event_type="network_port",
            issue_type="network_port",
            severity="medium",
            summary="port rollback failed",
            source="test",
            fingerprint="failed",
            action="auto_recover",
            fix_id="fix-network-1",
            apply_success=True,
            rerun_success=False,
            rollback_executed=True,
            rollback_success=False,
            recovered=False,
        ),
    ]

    text = reporter.to_markdown(records)

    assert reporter.compute_overall_status(records) == "rollback_failed"
    assert "overall_status: `rollback_failed`" in text
    assert "rollback_failed_count: `1`" in text
