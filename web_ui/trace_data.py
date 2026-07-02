from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from monitors.jsonl_store import read_jsonl
from monitors.project_registry import ProjectRegistry
from monitors.report_index_store import ReportIndexStore
from monitors.trace_store import (
    APPROVAL_RECORD_REQUEST,
    APPROVAL_STATUS_APPROVED,
    APPROVAL_STATUS_EXECUTION_BLOCKED,
    APPROVAL_STATUS_EXECUTION_FAILED,
    APPROVAL_STATUS_EXECUTION_SUCCEEDED,
    APPROVAL_STATUS_EXPIRED,
    APPROVAL_STATUS_PENDING,
    APPROVAL_STATUS_REJECTED,
    ApprovalStore,
    TraceStore,
)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _truthy(value: Any) -> bool:
    return bool(value) is True


def _latest_by_created_at(records: list[dict[str, Any]]) -> dict[str, Any]:
    if not records:
        return {}
    return sorted(records, key=lambda item: str(item.get("created_at", "")))[-1]


def _event_sort_key(item: dict[str, Any]) -> tuple[str, str]:
    return (
        str(item.get("last_seen_at", "")),
        str(item.get("fingerprint", "")),
    )


@dataclass
class TraceUiDataService:
    project_id: str
    state_dir: str = "state"
    config_path: str = "configs/projects.yaml"

    @property
    def project_state_dir(self) -> Path:
        return Path(self.state_dir) / self.project_id

    @property
    def trace_path(self) -> Path:
        return self.project_state_dir / "trace_events.jsonl"

    @property
    def approval_path(self) -> Path:
        return self.project_state_dir / "approval_requests.jsonl"

    @property
    def report_index_path(self) -> Path:
        return self.project_state_dir / "report_index.jsonl"

    def projects(self) -> list[dict[str, Any]]:
        projects = []
        for project in ProjectRegistry(self.config_path).load_all():
            projects.append(
                {
                    "project_id": project.project_id,
                    "name": project.name,
                    "mode": project.mode,
                    "owner": project.owner,
                    "require_human_approval_for_live_apply": bool(
                        getattr(
                            project.policy,
                            "require_human_approval_for_live_apply",
                            False,
                        )
                    ),
                }
            )
        return projects

    def overview(self) -> dict[str, Any]:
        events = self.events()
        pending = [item for item in events if item["pending_approval"]]
        recovered = [item for item in events if item["status"] == "recovered"]
        blocked = [
            item for item in events
            if item["status"] in {
                "blocked",
                "manual_escalation",
                "report_only",
                "approval_rejected",
                "approval_expired",
                "rollback_failed",
            }
        ]
        return {
            "project_id": self.project_id,
            "events_total": len(events),
            "pending_approvals": len(pending),
            "recovered": len(recovered),
            "blocked": len(blocked),
            "trace_path": str(self.trace_path),
            "approval_path": str(self.approval_path),
            "report_index_path": str(self.report_index_path),
            "reports_total": len(self._report_store().reports()),
        }

    def events(self) -> list[dict[str, Any]]:
        traces = self._trace_records()
        approvals = self._approval_records()
        approvals_by_fingerprint = self._approvals_by_fingerprint(approvals)

        grouped: dict[str, list[dict[str, Any]]] = {}
        for record in traces:
            fingerprint = str(record.get("fingerprint", ""))
            if not fingerprint:
                continue
            grouped.setdefault(fingerprint, []).append(record)

        for record in approvals:
            fingerprint = str(record.get("fingerprint", ""))
            if fingerprint and fingerprint not in grouped:
                grouped[fingerprint] = []

        rows = [
            self._event_summary(
                fingerprint=fingerprint,
                traces=items,
                approval_records=approvals_by_fingerprint.get(fingerprint, []),
            )
            for fingerprint, items in grouped.items()
        ]

        return sorted(rows, key=_event_sort_key, reverse=True)

    def event_detail(self, fingerprint: str) -> dict[str, Any]:
        traces = [
            item for item in self._trace_records()
            if item.get("fingerprint") == fingerprint
        ]
        approvals = [
            item for item in self._approval_records()
            if item.get("fingerprint") == fingerprint
        ]
        if not traces and not approvals:
            raise KeyError(f"event_not_found:{fingerprint}")

        summary = self._event_summary(
            fingerprint=fingerprint,
            traces=traces,
            approval_records=approvals,
        )
        policy_record = self._latest_stage(traces, "policy_decided")
        precheck_record = self._latest_stage(traces, "precheck_completed")
        approval_request = self._latest_approval_request(approvals)
        approval_latest = _latest_by_created_at(approvals)

        precheck = self._precheck_from_records(
            precheck_record=precheck_record,
            approval_request=approval_request,
        )
        planned_edits = _as_list(
            precheck.get("actionable_planned_edits")
            or precheck.get("planned_edits")
        )
        rollback_plan = _as_dict(precheck.get("rollback_plan"))

        audit_json = self._audit_json(
            policy_record=policy_record,
            precheck_record=precheck_record,
            approval_request=approval_request,
        )

        return {
            "summary": summary,
            "evidence": self._evidence(traces=traces, approvals=approvals),
            "policy_decision": _as_dict(policy_record.get("payload")),
            "planned_edits": planned_edits,
            "rollback_plan": rollback_plan,
            "approval": {
                "request": approval_request,
                "latest": approval_latest,
                "pending": approval_latest.get("status") == APPROVAL_STATUS_PENDING,
            },
            "audit_json": audit_json,
            "trace": sorted(traces, key=lambda item: str(item.get("created_at", ""))),
            "report_center": self._report_store().grouped_for_event(fingerprint),
        }

    def approve(
        self,
        request_id: str,
        operator: str = "web-ui",
        role: str = "",
        request_audit: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return ApprovalStore(
            project_id=self.project_id,
            state_dir=self.state_dir,
            trace_store=TraceStore(self.project_id, self.state_dir),
        ).approve(
            request_id,
            operator=operator,
            role=role,
            request_audit=request_audit,
        )

    def reject(
        self,
        request_id: str,
        operator: str = "web-ui",
        comment: str = "",
        role: str = "",
        request_audit: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return ApprovalStore(
            project_id=self.project_id,
            state_dir=self.state_dir,
            trace_store=TraceStore(self.project_id, self.state_dir),
        ).reject(
            request_id,
            operator=operator,
            comment=comment,
            role=role,
            request_audit=request_audit,
        )

    def expire(
        self,
        request_id: str,
        operator: str = "web-ui",
        comment: str = "",
        role: str = "",
        request_audit: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return ApprovalStore(
            project_id=self.project_id,
            state_dir=self.state_dir,
            trace_store=TraceStore(self.project_id, self.state_dir),
        ).expire(
            request_id,
            operator=operator,
            comment=comment,
            role=role,
            request_audit=request_audit,
        )

    def _trace_records(self) -> list[dict[str, Any]]:
        return read_jsonl(self.trace_path)

    def _approval_records(self) -> list[dict[str, Any]]:
        return read_jsonl(self.approval_path)

    def _report_store(self) -> ReportIndexStore:
        return ReportIndexStore(project_id=self.project_id, state_dir=self.state_dir)

    @staticmethod
    def _approvals_by_fingerprint(
        records: list[dict[str, Any]],
    ) -> dict[str, list[dict[str, Any]]]:
        result: dict[str, list[dict[str, Any]]] = {}
        for record in records:
            fingerprint = str(record.get("fingerprint", ""))
            if fingerprint:
                result.setdefault(fingerprint, []).append(record)
        return result

    def _event_summary(
        self,
        *,
        fingerprint: str,
        traces: list[dict[str, Any]],
        approval_records: list[dict[str, Any]],
    ) -> dict[str, Any]:
        latest_trace = _latest_by_created_at(traces)
        approval_latest = _latest_by_created_at(approval_records)
        approval_request = self._latest_approval_request(approval_records)

        policy_record = self._latest_stage(traces, "policy_decided")
        policy_payload = _as_dict(policy_record.get("payload"))
        final_decision = _as_dict(policy_payload.get("final_decision"))
        gate = _as_dict(policy_payload.get("gate"))
        recovery = _as_dict(_as_dict(latest_trace.get("payload")).get("recovery_audit_summary"))

        event_type = (
            str(latest_trace.get("event_type", ""))
            or str(approval_request.get("event_type", ""))
        )
        severity = (
            str(latest_trace.get("severity", ""))
            or str(approval_request.get("severity", ""))
        )
        action = (
            str(recovery.get("action", ""))
            or str(final_decision.get("action", ""))
            or str(approval_request.get("strategy_layer", ""))
        )
        dry_run = bool(
            gate.get("dry_run", approval_request.get("dry_run", True))
        )
        status = self._status(
            latest_trace=latest_trace,
            approval_latest=approval_latest,
            final_decision=final_decision,
            recovery=recovery,
            gate=gate,
        )

        return {
            "project_id": self.project_id,
            "fingerprint": fingerprint,
            "event_type": event_type,
            "severity": severity or "unknown",
            "summary": (
                str(latest_trace.get("summary", ""))
                or str(approval_request.get("summary", ""))
            ),
            "source": (
                str(latest_trace.get("source", ""))
                or str(approval_request.get("source", ""))
            ),
            "status": status,
            "action": action or "unknown",
            "dry_run": dry_run,
            "pending_approval": approval_latest.get("status") == APPROVAL_STATUS_PENDING,
            "approval_status": str(approval_latest.get("status", "")),
            "request_id": str(approval_request.get("request_id", "")),
            "last_stage": str(latest_trace.get("stage", "")),
            "last_seen_at": (
                str(latest_trace.get("created_at", ""))
                or str(approval_latest.get("created_at", ""))
            ),
        }

    @staticmethod
    def _status(
        *,
        latest_trace: dict[str, Any],
        approval_latest: dict[str, Any],
        final_decision: dict[str, Any],
        recovery: dict[str, Any],
        gate: dict[str, Any],
    ) -> str:
        approval_status = str(approval_latest.get("status", ""))
        if approval_status == APPROVAL_STATUS_PENDING:
            return "pending_approval"
        if approval_status == APPROVAL_STATUS_REJECTED:
            return "approval_rejected"
        if approval_status == APPROVAL_STATUS_EXPIRED:
            return "approval_expired"
        if approval_status == APPROVAL_STATUS_APPROVED:
            if latest_trace.get("stage") not in {"execution_finished", "rollback_finished"}:
                return "approved"
        if approval_status == APPROVAL_STATUS_EXECUTION_BLOCKED:
            return "blocked"
        if approval_status == APPROVAL_STATUS_EXECUTION_FAILED:
            return "execution_failed"
        if approval_status == APPROVAL_STATUS_EXECUTION_SUCCEEDED:
            if _truthy(recovery.get("recovered")):
                return "recovered"

        if _truthy(recovery.get("recovered")):
            return "recovered"
        if latest_trace.get("stage") == "rollback_finished":
            return (
                "rollback_done"
                if _truthy(_as_dict(latest_trace.get("payload")).get("rollback_success"))
                else "rollback_failed"
            )
        action = str(final_decision.get("action", ""))
        if action in {"manual_escalation", "report_only"}:
            return action
        if gate.get("allowed_to_execute") is False:
            return "blocked"
        return str(latest_trace.get("stage", "")) or "unknown"

    @staticmethod
    def _latest_stage(
        traces: list[dict[str, Any]],
        stage: str,
    ) -> dict[str, Any]:
        return _latest_by_created_at(
            [item for item in traces if item.get("stage") == stage]
        )

    @staticmethod
    def _latest_approval_request(records: list[dict[str, Any]]) -> dict[str, Any]:
        return _latest_by_created_at(
            [
                item for item in records
                if item.get("record_type") == APPROVAL_RECORD_REQUEST
            ]
        )

    @staticmethod
    def _precheck_from_records(
        *,
        precheck_record: dict[str, Any],
        approval_request: dict[str, Any],
    ) -> dict[str, Any]:
        precheck_payload = _as_dict(precheck_record.get("payload"))
        precheck = _as_dict(precheck_payload.get("precheck_result"))
        if precheck:
            return precheck

        request_precheck = _as_dict(approval_request.get("precheck_result"))
        return request_precheck

    @staticmethod
    def _audit_json(
        *,
        policy_record: dict[str, Any],
        precheck_record: dict[str, Any],
        approval_request: dict[str, Any],
    ) -> dict[str, Any]:
        approval_audit = _as_dict(approval_request.get("audit_record"))
        if approval_audit:
            return approval_audit

        return {
            "policy": _as_dict(policy_record.get("payload")),
            "precheck": _as_dict(precheck_record.get("payload")),
        }

    @staticmethod
    def _evidence(
        *,
        traces: list[dict[str, Any]],
        approvals: list[dict[str, Any]],
    ) -> dict[str, Any]:
        detected = _latest_by_created_at(
            [item for item in traces if item.get("stage") == "detected"]
        )
        approval_request = TraceUiDataService._latest_approval_request(approvals)
        detected_payload = _as_dict(detected.get("payload"))
        return {
            "summary": (
                str(detected.get("summary", ""))
                or str(approval_request.get("summary", ""))
            ),
            "source": (
                str(detected.get("source", ""))
                or str(approval_request.get("source", ""))
            ),
            "signature": str(detected_payload.get("signature", "")),
            "matched_keywords": _as_list(detected_payload.get("matched_keywords")),
            "raw_excerpt_present": bool(detected_payload.get("raw_excerpt_present", False)),
        }
