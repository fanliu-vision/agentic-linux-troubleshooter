from __future__ import annotations

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from monitors.recovery_history_store import RECOVERY_RECORD_APPLIED
from monitors.report_index_store import (
    REPORT_TYPE_AUDIT_JSON,
    REPORT_TYPE_AUTO_RECOVERY,
    ReportIndexStore,
)
from monitors.trace_store import (
    APPROVAL_STATUS_APPROVED,
    APPROVAL_STATUS_EXECUTION_SUCCEEDED,
    ApprovalStore,
    TraceStore,
)
from tests.web_ui_test_helpers import (
    call_handler,
    login_headers,
    make_server_context,
    seed_trace_and_pending_approval,
    write_project_config,
)
from web_ui.approved_recovery_worker import ApprovedRecoveryWorker
from web_ui.job_worker import AsyncJobWorker
import web_ui.operation_runner as operation_runner_module
from web_ui.runtime_control import JOB_STATUS_SUCCEEDED, JobStore
from web_ui.trace_data import TraceUiDataService


class FakeSession:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = str(output_dir)
        self.evidence: list[dict[str, Any]] = []

    def add_evidence(self, **kwargs: Any) -> str:
        self.evidence.append(dict(kwargs))
        return "ok"


class FakeRecoveryResult:
    def __init__(self, *, fingerprint: str, report_path: Path) -> None:
        self.event_type = "network_port"
        self.issue_type = "network_port"
        self.apply_success = True
        self.rerun_success = True
        self.rollback_executed = False
        self.rollback_success = False
        self.report_paths = [str(report_path)]
        self.messages = ["fake approved recovery completed"]
        self.r15_gate = SimpleNamespace(
            allowed_to_execute=True,
            downgrade_reason="",
        )
        self.apply_edit_summary = [
            {
                "field_path": "server.port",
                "old_value": 8000,
                "new_value": 8001,
                "config_path": "config.json",
                "backup_path": ".agent_backups/config.json.bak",
                "diff_path": ".agent_patches/config.json.diff",
                "success": True,
                "message": "updated controlled JSON field",
            }
        ]
        self._fingerprint = fingerprint

    @property
    def recovered(self) -> bool:
        return True

    def recovery_audit_record(self) -> dict[str, Any]:
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

    def recovery_audit_summary(self) -> dict[str, Any]:
        return {
            "action": "auto_recover",
            "execution_result": "executed_recovered",
            "recovered": True,
            "allowed_to_execute": True,
        }


class FakeRunner:
    def __init__(self, session: FakeSession) -> None:
        self.session = session

    def recover_after_approval(self, event: Any, approval_request_id: str) -> FakeRecoveryResult:
        output_dir = Path(self.session.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        report_path = output_dir / "approved-recovery.md"
        report_path.write_text("# Approved Recovery\n\nRecovered safely.", encoding="utf-8")
        applied_record = [
            {
                "fix_id": "fix-network-1",
                "fingerprint": event.fingerprint,
                "event_type": event.event_type,
                "job_id": "",
                "request_id": approval_request_id,
                "edits": [
                    {
                        "field_path": "server.port",
                        "old_value": 8000,
                        "new_value": 8001,
                        "config_path": "config.json",
                        "backup_path": ".agent_backups/config.json.bak",
                        "diff_path": ".agent_patches/config.json.diff",
                        "success": True,
                        "message": "updated controlled JSON field",
                    }
                ],
            }
        ]
        (output_dir / "applied_fixes.json").write_text(
            json.dumps(applied_record, ensure_ascii=False),
            encoding="utf-8",
        )
        return FakeRecoveryResult(
            fingerprint=event.fingerprint,
            report_path=report_path,
        )


def test_seeded_api_approval_to_worker_execution_records_full_safety_loop(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        project_id = "e2e_ui_project"
        project_dir = root / "project"
        project_dir.mkdir()
        config_path = root / "projects.yaml"
        state_dir = root / "state"
        output_root = root / "outputs"
        write_project_config(
            config_path,
            project_id=project_id,
            project_dir=str(project_dir),
        )
        _, request = seed_trace_and_pending_approval(
            state_dir=state_dir,
            project_id=project_id,
            fingerprint="e2e-network-port-fp",
        )
        server = make_server_context(
            config_path=config_path,
            state_dir=state_dir,
            output_root=output_root,
        )
        headers = login_headers(server, operator="operator-e2e")

        class E2EApprovedRecoveryWorker:
            def __init__(self, **kwargs: Any) -> None:
                def session_factory(project: Any) -> FakeSession:
                    return FakeSession(
                        output_root / project_id / "session-approved-recovery"
                    )

                def runner_factory(
                    project: Any,
                    session: FakeSession,
                    trace_store: TraceStore,
                    approval_store: ApprovalStore,
                ) -> FakeRunner:
                    return FakeRunner(session)

                self.inner = ApprovedRecoveryWorker(
                    **kwargs,
                    runner_factory=runner_factory,
                    session_factory=session_factory,
                )

            def latest_approved_request(self) -> dict[str, Any]:
                return self.inner.latest_approved_request()

            def run_for_request(
                self,
                request_id: str,
                *,
                operator: str = "web-ui",
                job_id: str = "",
            ) -> dict[str, Any]:
                return self.inner.run_for_request(
                    request_id,
                    operator=operator,
                    job_id=job_id,
                )

        monkeypatch.setattr(
            operation_runner_module,
            "ApprovedRecoveryWorker",
            E2EApprovedRecoveryWorker,
        )

        approved = call_handler(
            server,
            "POST",
            f"/api/projects/{project_id}/approvals/{request['request_id']}/approve",
            headers=headers,
            body={"confirm": True, "confirmation_action": "approval_approve"},
        )

        assert approved.status == 200
        approved_body = approved.json()
        assert approved_body["approval"]["status"] == APPROVAL_STATUS_APPROVED
        approved_job = approved_body["approved_recovery"]["job"]
        assert approved_job["action"] == "approved_recovery_job"
        assert approved_job["payload"]["request_id"] == request["request_id"]

        worker_result = AsyncJobWorker(
            project_id=project_id,
            state_dir=str(state_dir),
            config_path=str(config_path),
            output_root=str(output_root),
            worker_id="e2e-worker",
        ).run_once()

        assert worker_result["ran"] is True
        completed_job = worker_result["job"]
        assert completed_job["job_id"] == approved_job["job_id"]
        assert completed_job["status"] == JOB_STATUS_SUCCEEDED
        assert completed_job["result"]["recovered"] is True
        assert completed_job["result"]["recovery_history"]

        approval_records = ApprovalStore(
            project_id=project_id,
            state_dir=str(state_dir),
        ).read_all()
        assert approval_records[-1]["record_type"] == "execution"
        assert approval_records[-1]["status"] == APPROVAL_STATUS_EXECUTION_SUCCEEDED
        assert approval_records[-1]["job_id"] == completed_job["job_id"]

        reports = ReportIndexStore(
            project_id=project_id,
            state_dir=str(state_dir),
        ).reports()
        assert {item["report_type"] for item in reports} >= {
            REPORT_TYPE_AUTO_RECOVERY,
            REPORT_TYPE_AUDIT_JSON,
        }

        history_records = [
            item for item in completed_job["result"]["recovery_history"]
            if item.get("record_type") == RECOVERY_RECORD_APPLIED
        ]
        assert history_records
        assert history_records[0]["request_id"] == request["request_id"]
        assert history_records[0]["fingerprint"] == request["fingerprint"]

        trace_records = TraceStore(
            project_id=project_id,
            state_dir=str(state_dir),
        ).read_all()
        stages = [item["stage"] for item in trace_records]
        assert "approval_required" in stages
        assert "approved" in stages
        assert stages[-1] == "execution_finished"
        assert trace_records[-1]["payload"]["job_status"] == JOB_STATUS_SUCCEEDED
        assert trace_records[-1]["payload"]["recovery_audit_summary"]["recovered"] is True

        ui_events = TraceUiDataService(
            project_id=project_id,
            state_dir=str(state_dir),
            config_path=str(config_path),
        ).events()
        assert ui_events[0]["status"] == "recovered"
        assert JobStore(project_id=project_id, state_dir=str(state_dir)).get(
            approved_job["job_id"]
        )["status"] == JOB_STATUS_SUCCEEDED
