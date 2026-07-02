from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from detectors import ErrorEvent
from monitors.report_index_store import REPORT_TYPE_AUTO_RECOVERY, ReportIndexStore
from monitors.trace_store import (
    APPROVAL_STATUS_APPROVED,
    APPROVAL_STATUS_PENDING,
    APPROVAL_STATUS_REJECTED,
    TRACE_STAGE_DETECTED,
    TRACE_STAGE_POLICY_DECIDED,
    TRACE_STAGE_PRECHECK_COMPLETED,
    ApprovalStore,
    TraceStore,
)
from web_ui.trace_data import TraceUiDataService


def make_event() -> ErrorEvent:
    return ErrorEvent(
        event_type="network_port",
        issue_type="network_port",
        severity="medium",
        summary="metrics port is already in use",
        source="service.log",
        matched_keywords=["address already in use", "metrics_port"],
        raw_excerpt="OSError: address already in use for metrics_port",
        signature="ui-trace-network-port",
    )


def make_config(path: Path, project_id: str) -> None:
    path.write_text(
        f"""
projects:
  - project_id: {project_id}
    name: Trace UI Test Project
    mode: local
    owner: tests
    project_dir: /tmp/trace-ui-test
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


def make_precheck() -> dict:
    planned_edit = {
        "field_path": "metrics_port",
        "current_value": 9000,
        "new_value": 9101,
        "semantic_status": "safe",
        "actionable": True,
    }
    return {
        "passed": False,
        "reasons": ["ambiguous_event_evidence"],
        "target_fix_id": "fix-network-1",
        "planned_edits": [planned_edit],
        "actionable_planned_edits": [planned_edit],
        "unsafe_planned_edits": [],
        "actionable_edit_count": 1,
        "rollback_plan": {
            "available": True,
            "record_name": "applied_fixes.json",
            "backup_created_before_write": True,
            "rollback_method": "rollback_latest_apply",
        },
    }


def make_gate(event: ErrorEvent, precheck: dict) -> SimpleNamespace:
    return SimpleNamespace(
        event_type=event.event_type,
        fingerprint=event.fingerprint,
        strategy_layer="safe_auto_recover",
        candidate_fix_id="fix-network-1",
        selected_fix_id="fix-network-1",
        dry_run=False,
        would_execute=True,
        allowed_to_execute=False,
        auto_recover_allowed=True,
        operator_required=True,
        rollback_available=True,
        downgrade_reason="human_approval_required",
        precheck_result=precheck,
        audit_record={
            "event_type": event.event_type,
            "fingerprint": event.fingerprint,
            "selected_fix_id": "fix-network-1",
            "auto_recover_allowed": True,
            "rollback_available": True,
            "forbidden_action": False,
            "precheck_result": precheck,
        },
    )


def seed_pending_approval(
    *,
    state_dir: str,
    project_id: str,
) -> tuple[ErrorEvent, dict]:
    event = make_event()
    trace_store = TraceStore(project_id=project_id, state_dir=state_dir)
    approval_store = ApprovalStore(
        project_id=project_id,
        state_dir=state_dir,
        trace_store=trace_store,
    )
    precheck = make_precheck()

    trace_store.append(
        TRACE_STAGE_DETECTED,
        event=event,
        payload={
            "signature": event.signature,
            "matched_keywords": event.matched_keywords,
            "raw_excerpt_present": True,
        },
    )
    trace_store.append(
        TRACE_STAGE_POLICY_DECIDED,
        event=event,
        payload={
            "final_decision": {
                "action": "safe_auto_recover",
                "fix_id": "fix-network-1",
            },
            "gate": {
                "allowed_to_execute": False,
                "dry_run": False,
                "downgrade_reason": "human_approval_required",
            },
        },
    )
    trace_store.append(
        TRACE_STAGE_PRECHECK_COMPLETED,
        event=event,
        payload={"precheck_result": precheck},
    )
    request = approval_store.create_request_from_gate(
        event=event,
        gate=make_gate(event, precheck),
        audit_record={
            "gate": {
                "allowed_to_execute": False,
                "downgrade_reason": "human_approval_required",
            },
            "precheck_result": precheck,
        },
        reason="human_approval_required",
    )
    return event, request


def test_trace_ui_data_service_builds_event_detail_for_pending_approval() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        project_id = "trace_ui_project"
        config_path = root / "projects.yaml"
        state_dir = str(root / "state")
        make_config(config_path, project_id)
        event, request = seed_pending_approval(
            state_dir=state_dir,
            project_id=project_id,
        )

        service = TraceUiDataService(
            project_id=project_id,
            state_dir=state_dir,
            config_path=str(config_path),
        )

        assert service.projects()[0]["require_human_approval_for_live_apply"] is True
        assert service.overview()["pending_approvals"] == 1

        rows = service.events()
        assert rows[0]["fingerprint"] == event.fingerprint
        assert rows[0]["status"] == "pending_approval"
        assert rows[0]["severity"] == "medium"
        assert rows[0]["event_type"] == "network_port"
        assert rows[0]["action"] == "safe_auto_recover"
        assert rows[0]["dry_run"] is False
        assert rows[0]["pending_approval"] is True
        assert rows[0]["request_id"] == request["request_id"]

        detail = service.event_detail(event.fingerprint)
        assert detail["evidence"]["signature"] == event.signature
        assert detail["evidence"]["raw_excerpt_present"] is True
        assert detail["approval"]["pending"] is True
        assert detail["approval"]["request"]["status"] == APPROVAL_STATUS_PENDING
        assert detail["planned_edits"][0]["field_path"] == "metrics_port"
        assert detail["planned_edits"][0]["current_value"] == 9000
        assert detail["planned_edits"][0]["new_value"] == 9101
        assert detail["rollback_plan"]["available"] is True
        assert detail["policy_decision"]["final_decision"]["fix_id"] == "fix-network-1"
        assert [item["stage"] for item in detail["trace"]] == [
            "detected",
            "policy_decided",
            "precheck_completed",
            "approval_required",
        ]
        assert detail["report_center"]["event"] == []


def test_trace_ui_data_service_includes_event_report_center() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        project_id = "trace_ui_project"
        config_path = root / "projects.yaml"
        state_dir = str(root / "state")
        make_config(config_path, project_id)
        event, _ = seed_pending_approval(
            state_dir=state_dir,
            project_id=project_id,
        )
        report = ReportIndexStore(
            project_id=project_id,
            state_dir=state_dir,
        ).register_text_report(
            content="# 恢复报告",
            report_type=REPORT_TYPE_AUTO_RECOVERY,
            fingerprint=event.fingerprint,
            event_type=event.event_type,
            job_id="job-1",
        )
        service = TraceUiDataService(
            project_id=project_id,
            state_dir=state_dir,
            config_path=str(config_path),
        )

        detail = service.event_detail(event.fingerprint)

        assert detail["report_center"]["event"][0]["report_id"] == report["report_id"]
        assert detail["report_center"]["auto_recovery"][0]["job_id"] == "job-1"
        assert service.overview()["reports_total"] == 1


def test_trace_ui_data_service_approval_updates_status_and_trace() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        project_id = "trace_ui_project"
        config_path = root / "projects.yaml"
        state_dir = str(root / "state")
        make_config(config_path, project_id)
        event, request = seed_pending_approval(
            state_dir=state_dir,
            project_id=project_id,
        )
        service = TraceUiDataService(
            project_id=project_id,
            state_dir=state_dir,
            config_path=str(config_path),
        )

        decision = service.approve(request["request_id"], operator="tester")

        assert decision["status"] == APPROVAL_STATUS_APPROVED
        detail = service.event_detail(event.fingerprint)
        assert detail["approval"]["pending"] is False
        assert detail["approval"]["latest"]["status"] == APPROVAL_STATUS_APPROVED
        assert service.events()[0]["status"] == "approved"
        assert detail["trace"][-1]["stage"] == "approved"


def test_trace_ui_data_service_counts_rejected_approval_as_blocked() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        project_id = "trace_ui_project"
        config_path = root / "projects.yaml"
        state_dir = str(root / "state")
        make_config(config_path, project_id)
        event, request = seed_pending_approval(
            state_dir=state_dir,
            project_id=project_id,
        )
        service = TraceUiDataService(
            project_id=project_id,
            state_dir=state_dir,
            config_path=str(config_path),
        )

        decision = service.reject(
            request["request_id"],
            operator="tester",
            comment="not safe enough right now",
        )

        assert decision["status"] == APPROVAL_STATUS_REJECTED
        assert service.events()[0]["status"] == "approval_rejected"
        assert service.overview()["blocked"] == 1
        assert service.event_detail(event.fingerprint)["trace"][-1]["stage"] == "rejected"
