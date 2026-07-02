from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from monitors.trace_store import (
    APPROVAL_STATUS_APPROVED,
    APPROVAL_STATUS_EXECUTION_SUCCEEDED,
    APPROVAL_STATUS_REJECTED,
    ApprovalStore,
    TraceStore,
)
from monitors.report_index_store import (
    REPORT_TYPE_AUDIT_JSON,
    REPORT_TYPE_AUTO_RECOVERY,
    ReportIndexStore,
)
from web_ui.approved_recovery_worker import (
    APPROVED_RECOVERY_JOB_ACTION,
    ApprovedRecoveryWorker,
)
from web_ui.runtime_control import JOB_STATUS_BLOCKED, JOB_STATUS_SUCCEEDED


def write_config(path: Path, *, project_id: str, project_dir: str) -> None:
    path.write_text(
        f"""
projects:
  - project_id: {project_id}
    name: Approved Worker Test
    mode: local
    owner: tests
    project_dir: {project_dir}
    run_command: python app.py
    log_files:
      []
    policy:
      auto_recover: true
      auto_recovery_policy_enabled: true
      auto_recovery_dry_run: false
      require_human_approval_for_live_apply: true
      rollback_on_failure: true
      allow_auto_apply:
        - fix-network-1
      escalation_required: []
""",
        encoding="utf-8",
    )


def create_request(
    *,
    state_dir: str,
    project_id: str,
) -> tuple[ApprovalStore, dict[str, str]]:
    trace_store = TraceStore(project_id=project_id, state_dir=state_dir)
    approval_store = ApprovalStore(
        project_id=project_id,
        state_dir=state_dir,
        trace_store=trace_store,
    )
    event = SimpleNamespace(
        event_type="network_port",
        issue_type="network_port",
        severity="medium",
        summary="端口被占用",
        source="test.log",
        fingerprint="approved-worker-fp",
    )
    precheck = {
        "passed": True,
        "reasons": [],
        "actionable_edit_count": 1,
        "unsafe_planned_edits": [],
        "actionable_planned_edits": [
            {"field_path": "server.port", "old_value": "8000", "new_value": "8001"}
        ],
        "rollback_plan": {"available": True},
    }
    gate = SimpleNamespace(
        event_type="network_port",
        fingerprint=event.fingerprint,
        strategy_layer="safe_auto_recover",
        candidate_fix_id="fix-network-1",
        selected_fix_id="fix-network-1",
        auto_recover_allowed=True,
        dry_run=False,
        would_execute=True,
        allowed_to_execute=True,
        operator_required=True,
        rollback_available=True,
        downgrade_reason="human_approval_required",
        precheck_result=precheck,
        audit_record={
            "event_type": "network_port",
            "fingerprint": event.fingerprint,
            "selected_fix_id": "fix-network-1",
            "auto_recover_allowed": True,
            "rollback_available": True,
            "forbidden_action": False,
            "precheck_result": precheck,
        },
    )
    request = approval_store.create_request_from_gate(event=event, gate=gate)
    return approval_store, request


class FakeRecoveryResult:
    def __init__(self, *, fingerprint: str) -> None:
        self.event_type = "network_port"
        self.issue_type = "network_port"
        self.apply_success = True
        self.rerun_success = True
        self.rollback_executed = False
        self.rollback_success = False
        self.report_paths = ["fake-report.md"]
        self.messages = ["fake recovery completed"]
        self.r15_gate = SimpleNamespace(
            allowed_to_execute=True,
            downgrade_reason="",
        )
        self._fingerprint = fingerprint

    @property
    def recovered(self) -> bool:
        return True

    def recovery_audit_record(self) -> dict[str, object]:
        return {
            "event_type": "network_port",
            "fingerprint": self._fingerprint,
            "selected_fix_id": "fix-network-1",
            "fix_id": "fix-network-1",
            "approval_status": APPROVAL_STATUS_APPROVED,
            "execution_result": "executed_recovered",
            "recovered": True,
            "apply_success": True,
            "rerun_success": True,
            "allowed_to_execute": True,
        }

    def recovery_audit_summary(self) -> dict[str, object]:
        return {
            "action": "auto_recover",
            "execution_result": "executed_recovered",
            "recovered": True,
            "allowed_to_execute": True,
        }


def test_worker_consumes_approved_request_and_records_all_stores() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        project_id = "approved_worker"
        config_path = root / "projects.yaml"
        state_dir = str(root / "state")
        write_config(config_path, project_id=project_id, project_dir=str(root))
        approval_store, request = create_request(
            state_dir=state_dir,
            project_id=project_id,
        )
        approval_store.approve(request["request_id"], operator="tester")
        calls: list[tuple[str, str]] = []

        class FakeRunner:
            def recover_after_approval(self, event, approval_request_id: str):
                calls.append((event.fingerprint, approval_request_id))
                return FakeRecoveryResult(fingerprint=event.fingerprint)

        def fake_runner_factory(project, session, trace_store, approval_store):
            return FakeRunner()

        worker = ApprovedRecoveryWorker(
            project_id=project_id,
            state_dir=state_dir,
            config_path=str(config_path),
            runner_factory=fake_runner_factory,
            session_factory=lambda project: SimpleNamespace(
                add_evidence=lambda **kwargs: "ok"
            ),
        )

        response = worker.run_for_request(request["request_id"], operator="tester")

        assert response["job"]["action"] == APPROVED_RECOVERY_JOB_ACTION
        assert response["job"]["status"] == JOB_STATUS_SUCCEEDED
        assert calls == [(request["fingerprint"], request["request_id"])]

        records = ApprovalStore(project_id=project_id, state_dir=state_dir).read_all()
        assert records[-1]["record_type"] == "execution"
        assert records[-1]["status"] == APPROVAL_STATUS_EXECUTION_SUCCEEDED
        assert records[-1]["job_id"] == response["job"]["job_id"]

        trace_records = TraceStore(project_id=project_id, state_dir=state_dir).read_all()
        assert trace_records[-1]["stage"] == "execution_finished"
        assert trace_records[-1]["payload"]["job_status"] == JOB_STATUS_SUCCEEDED
        assert trace_records[-1]["payload"]["recovery_audit_summary"]["recovered"] is True

        reports = ReportIndexStore(project_id=project_id, state_dir=state_dir).reports()
        assert {item["report_type"] for item in reports} == {
            REPORT_TYPE_AUDIT_JSON,
            REPORT_TYPE_AUTO_RECOVERY,
        }
        assert response["job"]["result"]["indexed_reports"]


def test_worker_does_not_consume_rejected_request_or_mutate_approval_status() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        project_id = "approved_worker"
        config_path = root / "projects.yaml"
        state_dir = str(root / "state")
        write_config(config_path, project_id=project_id, project_dir=str(root))
        approval_store, request = create_request(
            state_dir=state_dir,
            project_id=project_id,
        )
        approval_store.reject(request["request_id"], operator="tester")
        calls: list[str] = []

        def fake_runner_factory(project, session, trace_store, approval_store):
            calls.append("called")
            return object()

        worker = ApprovedRecoveryWorker(
            project_id=project_id,
            state_dir=state_dir,
            config_path=str(config_path),
            runner_factory=fake_runner_factory,
            session_factory=lambda project: SimpleNamespace(
                add_evidence=lambda **kwargs: "ok"
            ),
        )

        response = worker.run_for_request(request["request_id"], operator="tester")

        assert response["job"]["status"] == JOB_STATUS_BLOCKED
        assert response["job"]["result"]["failure_reason"] == (
            f"approval_status_not_approved:{APPROVAL_STATUS_REJECTED}"
        )
        assert calls == []
        latest = ApprovalStore(project_id=project_id, state_dir=state_dir).latest_record(
            request["request_id"]
        )
        assert latest["status"] == APPROVAL_STATUS_REJECTED
