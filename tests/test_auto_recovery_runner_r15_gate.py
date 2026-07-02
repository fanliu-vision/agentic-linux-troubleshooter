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
from monitors.trace_store import (
    APPROVAL_STATUS_APPROVED,
    APPROVAL_STATUS_EXPIRED,
    APPROVAL_STATUS_REJECTED,
    TRACE_STAGE_APPROVAL_REQUIRED,
    TRACE_STAGE_EXECUTION_FINISHED,
    TRACE_STAGE_EXECUTION_STARTED,
    TRACE_STAGE_POLICY_DECIDED,
    TRACE_STAGE_PRECHECK_COMPLETED,
    ApprovalStore,
    TraceStore,
)
from policies import RemediationDecision
import recovery.auto_recovery_runtime_controls as runtime_controls
from recovery.auto_recovery_runner import AutoRecoveryResult, AutoRecoveryRunner


@pytest.fixture(autouse=True)
def assume_target_port_available(monkeypatch) -> None:
    monkeypatch.setattr(
        runtime_controls,
        "_is_tcp_port_available",
        lambda host, port: True,
    )


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
    require_human_approval_for_live_apply: bool = False,
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
                "prefetch_count": 64,
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
            require_human_approval_for_live_apply=(
                require_human_approval_for_live_apply
            ),
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


def make_raw_event(event_type: str, issue_type: str, raw_excerpt: str) -> ErrorEvent:
    return ErrorEvent(
        event_type=event_type,
        issue_type=issue_type,
        severity="high",
        summary=f"{event_type} summary",
        source="test",
        raw_excerpt=raw_excerpt,
        signature=f"runner-r15-{event_type}-raw",
    )


def make_runner(project: ProjectConfig, session: FakeSession) -> AutoRecoveryRunner:
    runner = AutoRecoveryRunner(project=project, session=session)  # type: ignore[arg-type]
    runner._generate_report = (  # type: ignore[method-assign]
        lambda result, evidence_items=None: result.report_paths.append("fake-report.md")
    )
    return runner


def attach_stores(
    runner: AutoRecoveryRunner,
    *,
    state_dir: str,
) -> tuple[TraceStore, ApprovalStore]:
    trace_store = TraceStore(project_id=runner.project.project_id, state_dir=state_dir)
    approval_store = ApprovalStore(
        project_id=runner.project.project_id,
        state_dir=state_dir,
        trace_store=trace_store,
    )
    runner.trace_store = trace_store
    runner.approval_store = approval_store
    return trace_store, approval_store


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
    assert "Runtime gate audit fields" in session.recorded_result
    assert "r15_execution_result" in session.recorded_result


def test_cross_domain_gate_block_stays_manual_even_in_dry_run() -> None:
    session = FakeSession()
    runner = make_runner(
        make_project(
            dry_run=True,
            allow_auto_apply=["fix-queue-backpressure-1"],
        ),
        session,
    )
    event = make_raw_event(
        "queue_backpressure",
        "queue_backpressure",
        """
[worker] worker overload caused by queue backpressure
[worker] worker pool exhausted; concurrency too high
""",
    )

    assert runner.is_auto_recover_candidate(event) is False

    result = runner.recover(event)
    audit = result.recovery_audit_record()

    assert result.r15_gate is not None
    assert result.r15_gate.downgrade_reason == "ambiguous_event_evidence"
    assert result.r15_gate.operator_required is True
    assert result.decision.action == "manual_escalation"
    assert audit["execution_result"] == "not_run_r15_gate_blocked"
    assert audit["operator_required"] is True
    assert session.apply_calls == []
    assert session.rerun_calls == 0


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
    assert "Runtime gate audit fields" in session.recorded_result


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


def test_runner_writes_trace_for_live_execution() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state_dir = str(Path(tmp) / "state")
        trace_store = TraceStore(project_id="runner_gate", state_dir=state_dir)
        session = FakeSession()
        runner = make_runner(make_project(dry_run=False), session)
        runner.trace_store = trace_store

        result = runner.recover(make_event("network_port", "network_port"))

        assert result.recovered
        stages = [record["stage"] for record in trace_store.read_all()]
        assert stages == [
            TRACE_STAGE_POLICY_DECIDED,
            TRACE_STAGE_PRECHECK_COMPLETED,
            TRACE_STAGE_EXECUTION_STARTED,
            TRACE_STAGE_EXECUTION_FINISHED,
        ]
        execution_finished = trace_store.read_all()[-1]
        assert execution_finished["payload"]["apply_success"] is True
        assert execution_finished["payload"]["rerun_success"] is True


def test_runner_creates_approval_request_for_operator_required_safe_fix() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state_dir = str(Path(tmp) / "state")
        trace_store = TraceStore(project_id="runner_gate", state_dir=state_dir)
        approval_store = ApprovalStore(
            project_id="runner_gate",
            state_dir=state_dir,
            trace_store=trace_store,
        )
        session = FakeSession()
        runner = make_runner(
            make_project(
                dry_run=False,
                allow_auto_apply=["fix-queue-backpressure-1"],
            ),
            session,
        )
        runner.trace_store = trace_store
        runner.approval_store = approval_store
        event = make_raw_event(
            "queue_backpressure",
            "queue_backpressure",
            """
[worker] worker overload caused by queue backpressure
[worker] worker pool exhausted; concurrency too high
""",
        )

        result = runner.recover(event)

        assert result.decision.action == "manual_escalation"
        assert session.apply_calls == []
        approval_records = approval_store.read_all()
        assert len(approval_records) == 1
        assert approval_records[0]["record_type"] == "request"
        assert approval_records[0]["approval_scope"] == "existing_safe_fix"
        assert approval_records[0]["approvable"] is True

        stages = [record["stage"] for record in trace_store.read_all()]
        assert stages == [
            TRACE_STAGE_POLICY_DECIDED,
            TRACE_STAGE_PRECHECK_COMPLETED,
            TRACE_STAGE_APPROVAL_REQUIRED,
        ]


def test_human_approval_config_blocks_live_apply_and_creates_pending_request() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        session = FakeSession()
        runner = make_runner(
            make_project(
                dry_run=False,
                require_human_approval_for_live_apply=True,
            ),
            session,
        )
        _, approval_store = attach_stores(runner, state_dir=str(Path(tmp) / "state"))

        result = runner.recover(make_event("network_port", "network_port"))

        assert result.r15_gate is not None
        assert result.r15_gate.downgrade_reason == "human_approval_required"
        assert not result.r15_gate.allowed_to_execute
        assert result.decision.action == "manual_escalation"
        assert result.recovery_audit_record()["execution_result"] == (
            "not_run_human_approval_required"
        )
        assert session.apply_calls == []
        assert session.rerun_calls == 0

        approval_records = approval_store.read_all()
        assert len(approval_records) == 1
        assert approval_records[0]["record_type"] == "request"
        assert approval_records[0]["status"] == "pending"
        assert approval_records[0]["approval_scope"] == "existing_safe_fix"
        assert approval_records[0]["selected_fix_id"] == "fix-network-1"


def test_recover_after_approval_reruns_gate_and_executes_when_still_safe() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        session = FakeSession()
        runner = make_runner(
            make_project(
                dry_run=False,
                require_human_approval_for_live_apply=True,
            ),
            session,
        )
        _, approval_store = attach_stores(runner, state_dir=str(Path(tmp) / "state"))
        event = make_event("network_port", "network_port")

        pending = runner.recover(event)
        request_id = pending.approval_request_record["request_id"]
        decision = approval_store.approve(request_id, operator="tester")

        assert decision["status"] == APPROVAL_STATUS_APPROVED

        approved_result = runner.recover_after_approval(event, request_id)

        assert approved_result.r15_gate is not None
        assert approved_result.r15_gate.allowed_to_execute
        assert approved_result.recovered
        assert session.apply_calls == ["fix-network-1"]
        assert session.rerun_calls == 1
        assert approved_result.approval_decision_record["status"] == (
            APPROVAL_STATUS_APPROVED
        )


def test_recover_after_approval_reruns_gate_and_blocks_changed_policy() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        session = FakeSession()
        project = make_project(
            dry_run=False,
            require_human_approval_for_live_apply=True,
        )
        runner = make_runner(project, session)
        _, approval_store = attach_stores(runner, state_dir=str(Path(tmp) / "state"))
        event = make_event("network_port", "network_port")

        pending = runner.recover(event)
        request_id = pending.approval_request_record["request_id"]
        approval_store.approve(request_id, operator="tester")

        project.policy.rollback_on_failure = False
        approved_result = runner.recover_after_approval(event, request_id)

        assert approved_result.r15_gate is not None
        assert not approved_result.r15_gate.allowed_to_execute
        assert "rollback" in approved_result.r15_gate.downgrade_reason
        assert session.apply_calls == []
        assert session.rerun_calls == 0


def test_recover_after_rejected_approval_does_not_execute() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        session = FakeSession()
        runner = make_runner(
            make_project(
                dry_run=False,
                require_human_approval_for_live_apply=True,
            ),
            session,
        )
        _, approval_store = attach_stores(runner, state_dir=str(Path(tmp) / "state"))
        event = make_event("network_port", "network_port")

        pending = runner.recover(event)
        request_id = pending.approval_request_record["request_id"]
        approval_store.reject(request_id, operator="tester")
        rejected_result = runner.recover_after_approval(event, request_id)

        assert rejected_result.r15_gate is not None
        assert rejected_result.r15_gate.downgrade_reason == (
            f"approval_status_not_approved:{APPROVAL_STATUS_REJECTED}"
        )
        assert session.apply_calls == []
        assert session.rerun_calls == 0


def test_recover_after_expired_approval_does_not_execute() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        session = FakeSession()
        runner = make_runner(
            make_project(
                dry_run=False,
                require_human_approval_for_live_apply=True,
            ),
            session,
        )
        _, approval_store = attach_stores(runner, state_dir=str(Path(tmp) / "state"))
        event = make_event("network_port", "network_port")

        pending = runner.recover(event)
        request_id = pending.approval_request_record["request_id"]
        approval_store.expire(request_id, operator="tester")
        expired_result = runner.recover_after_approval(event, request_id)

        assert expired_result.r15_gate is not None
        assert expired_result.r15_gate.downgrade_reason == (
            f"approval_status_not_approved:{APPROVAL_STATUS_EXPIRED}"
        )
        assert session.apply_calls == []
        assert session.rerun_calls == 0


def test_recover_after_approval_fingerprint_mismatch_does_not_execute() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        session = FakeSession()
        runner = make_runner(
            make_project(
                dry_run=False,
                require_human_approval_for_live_apply=True,
            ),
            session,
        )
        _, approval_store = attach_stores(runner, state_dir=str(Path(tmp) / "state"))
        original_event = make_event("network_port", "network_port")
        other_event = make_raw_event(
            "network_port",
            "network_port",
            "network_port evidence for a different fingerprint",
        )

        pending = runner.recover(original_event)
        request_id = pending.approval_request_record["request_id"]
        approval_store.approve(request_id, operator="tester")
        mismatch_result = runner.recover_after_approval(other_event, request_id)

        assert mismatch_result.r15_gate is not None
        assert mismatch_result.r15_gate.downgrade_reason == (
            "approval_fingerprint_mismatch"
        )
        assert session.apply_calls == []
        assert session.rerun_calls == 0


def test_recover_after_approval_fix_id_mismatch_does_not_execute() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        session = FakeSession()
        runner = make_runner(
            make_project(
                dry_run=False,
                require_human_approval_for_live_apply=True,
            ),
            session,
        )
        _, approval_store = attach_stores(runner, state_dir=str(Path(tmp) / "state"))
        event = make_event("network_port", "network_port")

        pending = runner.recover(event)
        request_id = pending.approval_request_record["request_id"]
        approval_store.approve(request_id, operator="tester")

        records = approval_store.read_all()
        records[0]["selected_fix_id"] = "fix-gpu-1"
        approval_store.approval_requests_path.write_text(
            "\n".join(json.dumps(record, ensure_ascii=False) for record in records)
            + "\n",
            encoding="utf-8",
        )

        mismatch_result = runner.recover_after_approval(event, request_id)

        assert mismatch_result.r15_gate is not None
        assert mismatch_result.r15_gate.downgrade_reason == "approval_fix_id_mismatch"
        assert session.apply_calls == []
        assert session.rerun_calls == 0
