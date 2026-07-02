from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from monitors.jsonl_store import append_jsonl, read_jsonl, rewrite_jsonl
from safe_recovery.registry import SAFE_FIX_BY_EVENT_TYPE, fix_id_for_event_type


TRACE_SCHEMA_VERSION = "trace.v1"
APPROVAL_SCHEMA_VERSION = "approval.v1"

TRACE_STAGE_DETECTED = "detected"
TRACE_STAGE_POLICY_DECIDED = "policy_decided"
TRACE_STAGE_PRECHECK_COMPLETED = "precheck_completed"
TRACE_STAGE_APPROVAL_REQUIRED = "approval_required"
TRACE_STAGE_APPROVED = "approved"
TRACE_STAGE_REJECTED = "rejected"
TRACE_STAGE_EXECUTION_STARTED = "execution_started"
TRACE_STAGE_EXECUTION_FINISHED = "execution_finished"
TRACE_STAGE_ROLLBACK_STARTED = "rollback_started"
TRACE_STAGE_ROLLBACK_FINISHED = "rollback_finished"

TRACE_STAGES = {
    TRACE_STAGE_DETECTED,
    TRACE_STAGE_POLICY_DECIDED,
    TRACE_STAGE_PRECHECK_COMPLETED,
    TRACE_STAGE_APPROVAL_REQUIRED,
    TRACE_STAGE_APPROVED,
    TRACE_STAGE_REJECTED,
    TRACE_STAGE_EXECUTION_STARTED,
    TRACE_STAGE_EXECUTION_FINISHED,
    TRACE_STAGE_ROLLBACK_STARTED,
    TRACE_STAGE_ROLLBACK_FINISHED,
}

APPROVAL_STATUS_PENDING = "pending"
APPROVAL_STATUS_APPROVED = "approved"
APPROVAL_STATUS_REJECTED = "rejected"
APPROVAL_STATUS_NOT_APPROVABLE = "not_approvable"
APPROVAL_STATUS_EXPIRED = "expired"
APPROVAL_STATUS_EXECUTION_SUCCEEDED = "execution_succeeded"
APPROVAL_STATUS_EXECUTION_BLOCKED = "execution_blocked"
APPROVAL_STATUS_EXECUTION_FAILED = "execution_failed"

APPROVAL_RECORD_REQUEST = "request"
APPROVAL_RECORD_DECISION = "decision"
APPROVAL_RECORD_EXECUTION = "execution"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_dumps(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


def _jsonable(data: Any) -> Any:
    try:
        json.dumps(data, ensure_ascii=False)
        return data
    except TypeError:
        return json.loads(json.dumps(data, ensure_ascii=False, default=str))


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _event_payload(event: Any | None) -> dict[str, Any]:
    if event is None:
        return {}

    return {
        "event_type": str(_get(event, "event_type", "")),
        "issue_type": str(_get(event, "issue_type", "")),
        "severity": str(_get(event, "severity", "")),
        "summary": str(_get(event, "summary", "")),
        "source": str(_get(event, "source", "")),
        "fingerprint": str(_get(event, "fingerprint", "")),
    }


class TraceStore:
    """
    Append-only event trace store for the monitor and recovery pipeline.

    This is the future UI's fact source. It intentionally records structured
    stages instead of asking the UI to parse reports or notification Markdown.
    """

    def __init__(self, project_id: str, state_dir: str = "state") -> None:
        self.project_id = project_id
        self.state_dir = Path(state_dir)
        self.project_state_dir = self.state_dir / project_id
        self.trace_events_path = self.project_state_dir / "trace_events.jsonl"
        self.project_state_dir.mkdir(parents=True, exist_ok=True)

    def append(
        self,
        stage: str,
        *,
        event: Any | None = None,
        event_type: str = "",
        fingerprint: str = "",
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if stage not in TRACE_STAGES:
            raise ValueError(f"unknown_trace_stage:{stage}")

        event_fields = _event_payload(event)
        record = {
            "schema_version": TRACE_SCHEMA_VERSION,
            "created_at": _now_iso(),
            "project_id": self.project_id,
            "stage": stage,
            "event_type": event_type or event_fields.get("event_type", ""),
            "issue_type": event_fields.get("issue_type", ""),
            "severity": event_fields.get("severity", ""),
            "summary": event_fields.get("summary", ""),
            "source": event_fields.get("source", ""),
            "fingerprint": fingerprint or event_fields.get("fingerprint", ""),
            "payload": _jsonable(dict(payload or {})),
        }

        append_jsonl(self.trace_events_path, record)

        return record

    def read_all(
        self,
        *,
        limit: int | None = None,
        offset: int = 0,
        reverse: bool = False,
    ) -> list[dict[str, Any]]:
        return read_jsonl(
            self.trace_events_path,
            limit=limit,
            offset=offset,
            reverse=reverse,
        )

    def compact(self, *, keep_latest: int | None = None) -> list[dict[str, Any]]:
        records = self.read_all()
        if keep_latest is not None:
            records = records[-max(0, int(keep_latest)) :]
        rewrite_jsonl(self.trace_events_path, records)
        return records


class ApprovalStore:
    """
    Append-only human approval request and decision store.

    Approval is deliberately narrower than manual escalation: it can approve
    only an existing safe fix registered for the same event_type. It cannot
    promote high-risk manual_escalation domains into automatic execution.
    """

    def __init__(
        self,
        project_id: str,
        state_dir: str = "state",
        trace_store: TraceStore | None = None,
    ) -> None:
        self.project_id = project_id
        self.state_dir = Path(state_dir)
        self.project_state_dir = self.state_dir / project_id
        self.approval_requests_path = (
            self.project_state_dir / "approval_requests.jsonl"
        )
        self.trace_store = trace_store
        self.project_state_dir.mkdir(parents=True, exist_ok=True)

    def create_request_from_gate(
        self,
        *,
        event: Any,
        gate: Any,
        audit_record: dict[str, Any] | None = None,
        reason: str = "",
    ) -> dict[str, Any]:
        audit_record = dict(audit_record or {})
        event_type = str(_get(event, "event_type", _get(gate, "event_type", "")))
        selected_fix_id = str(_get(gate, "selected_fix_id", ""))
        approvable, safety_reason = self._is_safe_fix_approval_candidate(
            event_type=event_type,
            selected_fix_id=selected_fix_id,
            auto_recover_allowed=bool(_get(gate, "auto_recover_allowed", False)),
            rollback_available=bool(_get(gate, "rollback_available", False)),
            forbidden_action=bool(audit_record.get("forbidden_action", False)),
            precheck_result=dict(_get(gate, "precheck_result", {}) or {}),
        )

        status = APPROVAL_STATUS_PENDING if approvable else APPROVAL_STATUS_NOT_APPROVABLE
        approval_scope = "existing_safe_fix" if approvable else "manual_review_only"
        request_id = uuid.uuid4().hex

        record = {
            "schema_version": APPROVAL_SCHEMA_VERSION,
            "record_type": APPROVAL_RECORD_REQUEST,
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
            "project_id": self.project_id,
            "request_id": request_id,
            "status": status,
            "approval_scope": approval_scope,
            "approvable": approvable,
            "safety_reason": safety_reason,
            "reason": reason or str(_get(gate, "downgrade_reason", "")),
            "event_type": event_type,
            "issue_type": str(_get(event, "issue_type", "")),
            "severity": str(_get(event, "severity", "")),
            "summary": str(_get(event, "summary", "")),
            "source": str(_get(event, "source", "")),
            "fingerprint": str(_get(event, "fingerprint", _get(gate, "fingerprint", ""))),
            "strategy_layer": str(_get(gate, "strategy_layer", "")),
            "candidate_fix_id": str(_get(gate, "candidate_fix_id", "")),
            "selected_fix_id": selected_fix_id,
            "auto_recover_allowed": bool(_get(gate, "auto_recover_allowed", False)),
            "dry_run": bool(_get(gate, "dry_run", True)),
            "would_execute": bool(_get(gate, "would_execute", False)),
            "allowed_to_execute": bool(_get(gate, "allowed_to_execute", False)),
            "operator_required": bool(_get(gate, "operator_required", False)),
            "rollback_available": bool(_get(gate, "rollback_available", False)),
            "precheck_result": _jsonable(dict(_get(gate, "precheck_result", {}) or {})),
            "audit_record": _jsonable(audit_record),
        }
        self._append(record)
        self._trace(
            TRACE_STAGE_APPROVAL_REQUIRED,
            event_type=event_type,
            fingerprint=record["fingerprint"],
            payload={
                "request_id": request_id,
                "status": status,
                "approval_scope": approval_scope,
                "approvable": approvable,
                "safety_reason": safety_reason,
                "selected_fix_id": selected_fix_id,
                "strategy_layer": record["strategy_layer"],
            },
        )
        return record

    def approve(
        self,
        request_id: str,
        *,
        operator: str = "",
        comment: str = "",
    ) -> dict[str, Any]:
        request = self._request_by_id(request_id)
        current_status = self.current_status(request_id)

        allowed, reason = self._request_can_be_approved(request, current_status)
        status = APPROVAL_STATUS_APPROVED if allowed else APPROVAL_STATUS_REJECTED
        decision = self._decision_record(
            request=request,
            status=status,
            operator=operator,
            comment=comment,
            reason=reason,
        )
        self._append(decision)
        self._trace_decision(decision)
        return decision

    def reject(
        self,
        request_id: str,
        *,
        operator: str = "",
        comment: str = "",
        reason: str = "operator_rejected",
    ) -> dict[str, Any]:
        request = self._request_by_id(request_id)
        decision = self._decision_record(
            request=request,
            status=APPROVAL_STATUS_REJECTED,
            operator=operator,
            comment=comment,
            reason=reason,
        )
        self._append(decision)
        self._trace_decision(decision)
        return decision

    def expire(
        self,
        request_id: str,
        *,
        operator: str = "",
        comment: str = "",
        reason: str = "approval_expired",
    ) -> dict[str, Any]:
        request = self._request_by_id(request_id)
        decision = self._decision_record(
            request=request,
            status=APPROVAL_STATUS_EXPIRED,
            operator=operator,
            comment=comment,
            reason=reason,
        )
        self._append(decision)
        self._trace_decision(decision)
        return decision

    def record_execution(
        self,
        request_id: str,
        *,
        status: str,
        operator: str = "",
        job_id: str = "",
        summary: str = "",
        reason: str = "",
        result: dict[str, Any] | None = None,
        audit_record: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if status not in {
            APPROVAL_STATUS_EXECUTION_SUCCEEDED,
            APPROVAL_STATUS_EXECUTION_BLOCKED,
            APPROVAL_STATUS_EXECUTION_FAILED,
        }:
            raise ValueError(f"unknown_approval_execution_status:{status}")

        request = self._request_by_id(request_id)
        record = {
            "schema_version": APPROVAL_SCHEMA_VERSION,
            "record_type": APPROVAL_RECORD_EXECUTION,
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
            "project_id": self.project_id,
            "request_id": request["request_id"],
            "status": status,
            "operator": operator,
            "job_id": job_id,
            "execution_summary": summary,
            "execution_reason": reason,
            "event_type": request.get("event_type", ""),
            "fingerprint": request.get("fingerprint", ""),
            "selected_fix_id": request.get("selected_fix_id", ""),
            "approval_scope": request.get("approval_scope", ""),
            "approvable": request.get("approvable", False),
            "result": _jsonable(dict(result or {})),
            "audit_record": _jsonable(dict(audit_record or {})),
        }
        self._append(record)
        return record

    def read_all(
        self,
        *,
        limit: int | None = None,
        offset: int = 0,
        reverse: bool = False,
    ) -> list[dict[str, Any]]:
        return read_jsonl(
            self.approval_requests_path,
            limit=limit,
            offset=offset,
            reverse=reverse,
        )

    def current_status(self, request_id: str) -> str:
        record = self.latest_record(request_id)
        return str(record.get("status", "")) if record else ""

    def get_request(self, request_id: str) -> dict[str, Any]:
        return self._request_by_id(request_id)

    def latest_record(self, request_id: str) -> dict[str, Any]:
        records = [
            record for record in self.read_all()
            if record.get("request_id") == request_id
        ]
        if not records:
            return {}
        return records[-1]

    def _append(self, record: dict[str, Any]) -> None:
        append_jsonl(self.approval_requests_path, record)

    def compact(self, *, keep_latest: int | None = None) -> list[dict[str, Any]]:
        records = self.read_all()
        if keep_latest is not None:
            records = records[-max(0, int(keep_latest)) :]
        rewrite_jsonl(self.approval_requests_path, records)
        return records

    def _request_by_id(self, request_id: str) -> dict[str, Any]:
        for record in self.read_all():
            if (
                record.get("request_id") == request_id
                and record.get("record_type") == APPROVAL_RECORD_REQUEST
            ):
                return record
        raise KeyError(f"approval_request_not_found:{request_id}")

    def _request_can_be_approved(
        self,
        request: dict[str, Any],
        current_status: str,
    ) -> tuple[bool, str]:
        if request.get("approvable") is not True:
            return False, str(request.get("safety_reason") or "request_not_approvable")

        if current_status != APPROVAL_STATUS_PENDING:
            return False, f"request_not_pending:{current_status or '<missing>'}"

        if request.get("approval_scope") != "existing_safe_fix":
            return False, "approval_scope_not_existing_safe_fix"

        return self._is_safe_fix_approval_candidate(
            event_type=str(request.get("event_type", "")),
            selected_fix_id=str(request.get("selected_fix_id", "")),
            auto_recover_allowed=bool(request.get("auto_recover_allowed", False)),
            rollback_available=bool(request.get("rollback_available", False)),
            forbidden_action=bool(
                (request.get("audit_record") or {}).get("forbidden_action", False)
            ),
            precheck_result=dict(request.get("precheck_result") or {}),
        )

    def _decision_record(
        self,
        *,
        request: dict[str, Any],
        status: str,
        operator: str,
        comment: str,
        reason: str,
    ) -> dict[str, Any]:
        return {
            "schema_version": APPROVAL_SCHEMA_VERSION,
            "record_type": APPROVAL_RECORD_DECISION,
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
            "project_id": self.project_id,
            "request_id": request["request_id"],
            "status": status,
            "operator": operator,
            "comment": comment,
            "decision_reason": reason,
            "event_type": request.get("event_type", ""),
            "fingerprint": request.get("fingerprint", ""),
            "selected_fix_id": request.get("selected_fix_id", ""),
            "approval_scope": request.get("approval_scope", ""),
            "approvable": request.get("approvable", False),
        }

    def _trace_decision(self, decision: dict[str, Any]) -> None:
        stage = (
            TRACE_STAGE_APPROVED
            if decision.get("status") == APPROVAL_STATUS_APPROVED
            else TRACE_STAGE_REJECTED
        )
        self._trace(
            stage,
            event_type=str(decision.get("event_type", "")),
            fingerprint=str(decision.get("fingerprint", "")),
            payload={
                "request_id": decision.get("request_id", ""),
                "status": decision.get("status", ""),
                "selected_fix_id": decision.get("selected_fix_id", ""),
                "decision_reason": decision.get("decision_reason", ""),
                "operator": decision.get("operator", ""),
            },
        )

    def _trace(
        self,
        stage: str,
        *,
        event_type: str,
        fingerprint: str,
        payload: dict[str, Any],
    ) -> None:
        if self.trace_store is None:
            return
        self.trace_store.append(
            stage,
            event_type=event_type,
            fingerprint=fingerprint,
            payload=payload,
        )

    @staticmethod
    def _is_safe_fix_approval_candidate(
        *,
        event_type: str,
        selected_fix_id: str,
        auto_recover_allowed: bool,
        rollback_available: bool,
        forbidden_action: bool,
        precheck_result: dict[str, Any],
    ) -> tuple[bool, str]:
        expected_fix_id = fix_id_for_event_type(event_type)
        safe_fix_ids = set(SAFE_FIX_BY_EVENT_TYPE.values())

        if not selected_fix_id:
            return False, "selected_fix_id_missing"

        if selected_fix_id not in safe_fix_ids:
            return False, "selected_fix_id_not_registered_safe_fix"

        if selected_fix_id != expected_fix_id:
            return False, "selected_fix_id_does_not_match_event_type"

        if not auto_recover_allowed:
            return False, "auto_recover_not_allowed_by_policy"

        if forbidden_action:
            return False, "forbidden_action_detected"

        if not rollback_available:
            return False, "rollback_unavailable"

        reasons = set(precheck_result.get("reasons") or [])
        blocking_reasons = reasons - {"ambiguous_event_evidence"}
        if blocking_reasons:
            return False, f"precheck_blocking_reasons:{','.join(sorted(blocking_reasons))}"

        try:
            actionable_edit_count = int(
                precheck_result.get("actionable_edit_count", 0) or 0
            )
        except (TypeError, ValueError):
            actionable_edit_count = 0

        if actionable_edit_count <= 0:
            return False, "no_actionable_planned_edit"

        unsafe_edits = precheck_result.get("unsafe_planned_edits") or []
        if unsafe_edits:
            return False, "unsafe_planned_edits_present"

        return True, "existing_safe_fix_approval_allowed"
