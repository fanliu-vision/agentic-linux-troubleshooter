from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from detectors import ErrorEvent
from monitors.trace_store import (
    APPROVAL_STATUS_APPROVED,
    APPROVAL_STATUS_EXPIRED,
    APPROVAL_STATUS_NOT_APPROVABLE,
    APPROVAL_STATUS_REJECTED,
    TRACE_STAGE_APPROVAL_REQUIRED,
    TRACE_STAGE_APPROVED,
    TRACE_STAGE_DETECTED,
    TRACE_STAGE_REJECTED,
    ApprovalStore,
    TraceStore,
)


def make_event(event_type: str, issue_type: str, signature: str) -> ErrorEvent:
    return ErrorEvent(
        event_type=event_type,
        issue_type=issue_type,
        severity="medium",
        summary=f"{event_type} summary",
        source="test",
        raw_excerpt=f"{event_type} evidence",
        signature=signature,
    )


def make_gate(
    *,
    event: ErrorEvent,
    selected_fix_id: str,
    auto_recover_allowed: bool,
    strategy_layer: str = "manual_escalation",
    rollback_available: bool = True,
    reasons: list[str] | None = None,
    actionable_edit_count: int = 1,
) -> SimpleNamespace:
    precheck_result = {
        "passed": not reasons,
        "reasons": list(reasons or []),
        "actionable_edit_count": actionable_edit_count,
        "unsafe_planned_edits": [],
    }
    return SimpleNamespace(
        event_type=event.event_type,
        fingerprint=event.fingerprint,
        strategy_layer=strategy_layer,
        candidate_fix_id=selected_fix_id,
        selected_fix_id=selected_fix_id,
        dry_run=False,
        would_execute=False,
        allowed_to_execute=False,
        auto_recover_allowed=auto_recover_allowed,
        operator_required=True,
        rollback_available=rollback_available,
        downgrade_reason="ambiguous_event_evidence" if reasons else "",
        precheck_result=precheck_result,
        audit_record={
            "event_type": event.event_type,
            "fingerprint": event.fingerprint,
            "selected_fix_id": selected_fix_id,
            "auto_recover_allowed": auto_recover_allowed,
            "rollback_available": rollback_available,
            "forbidden_action": False,
            "precheck_result": precheck_result,
        },
    )


def test_trace_store_appends_detected_stage() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        event = make_event("network_port", "network_port", "trace-detected")
        store = TraceStore(project_id="trace_test", state_dir=str(Path(tmp) / "state"))

        record = store.append(
            TRACE_STAGE_DETECTED,
            event=event,
            payload={"line_number": 12},
        )

        assert record["stage"] == TRACE_STAGE_DETECTED
        assert record["event_type"] == "network_port"
        assert record["fingerprint"] == event.fingerprint
        assert store.trace_events_path.exists()
        assert store.read_all()[0]["payload"]["line_number"] == 12


def test_approval_store_approves_existing_safe_fix_and_traces_decision() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state_dir = str(Path(tmp) / "state")
        event = make_event(
            "queue_backpressure",
            "queue_backpressure",
            "safe-approval",
        )
        trace_store = TraceStore(project_id="approval_test", state_dir=state_dir)
        approval_store = ApprovalStore(
            project_id="approval_test",
            state_dir=state_dir,
            trace_store=trace_store,
        )
        gate = make_gate(
            event=event,
            selected_fix_id="fix-queue-backpressure-1",
            auto_recover_allowed=True,
            reasons=["ambiguous_event_evidence"],
        )

        request = approval_store.create_request_from_gate(event=event, gate=gate)
        decision = approval_store.approve(
            request["request_id"],
            operator="tester",
            comment="approved for controlled safe fix",
        )

        assert request["status"] == "pending"
        assert request["approval_scope"] == "existing_safe_fix"
        assert request["approvable"] is True
        assert decision["status"] == APPROVAL_STATUS_APPROVED

        records = approval_store.read_all()
        assert [record["record_type"] for record in records] == ["request", "decision"]

        trace_stages = [record["stage"] for record in trace_store.read_all()]
        assert trace_stages == [
            TRACE_STAGE_APPROVAL_REQUIRED,
            TRACE_STAGE_APPROVED,
        ]


def test_approval_store_rejects_high_risk_manual_escalation_approval() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state_dir = str(Path(tmp) / "state")
        event = make_event("process_crash", "process", "high-risk-manual")
        trace_store = TraceStore(project_id="approval_test", state_dir=state_dir)
        approval_store = ApprovalStore(
            project_id="approval_test",
            state_dir=state_dir,
            trace_store=trace_store,
        )
        gate = make_gate(
            event=event,
            selected_fix_id="",
            auto_recover_allowed=False,
            strategy_layer="manual_escalation",
            rollback_available=False,
            reasons=["event_type_policy_manual_escalation"],
            actionable_edit_count=0,
        )

        request = approval_store.create_request_from_gate(event=event, gate=gate)
        decision = approval_store.approve(
            request["request_id"],
            operator="tester",
            comment="attempted unsafe approval",
        )

        assert request["status"] == APPROVAL_STATUS_NOT_APPROVABLE
        assert request["approval_scope"] == "manual_review_only"
        assert request["approvable"] is False
        assert decision["status"] == APPROVAL_STATUS_REJECTED
        assert decision["decision_reason"] == "selected_fix_id_missing"

        trace_stages = [record["stage"] for record in trace_store.read_all()]
        assert trace_stages == [
            TRACE_STAGE_APPROVAL_REQUIRED,
            TRACE_STAGE_REJECTED,
        ]


def test_approval_store_can_expire_pending_request() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state_dir = str(Path(tmp) / "state")
        event = make_event("network_port", "network_port", "expire-approval")
        trace_store = TraceStore(project_id="approval_test", state_dir=state_dir)
        approval_store = ApprovalStore(
            project_id="approval_test",
            state_dir=state_dir,
            trace_store=trace_store,
        )
        gate = make_gate(
            event=event,
            selected_fix_id="fix-network-1",
            auto_recover_allowed=True,
        )

        request = approval_store.create_request_from_gate(event=event, gate=gate)
        expired = approval_store.expire(
            request["request_id"],
            operator="tester",
        )

        assert expired["status"] == APPROVAL_STATUS_EXPIRED
        assert approval_store.current_status(request["request_id"]) == (
            APPROVAL_STATUS_EXPIRED
        )

        trace_stages = [record["stage"] for record in trace_store.read_all()]
        assert trace_stages == [
            TRACE_STAGE_APPROVAL_REQUIRED,
            TRACE_STAGE_REJECTED,
        ]
