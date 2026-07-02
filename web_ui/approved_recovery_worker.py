from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from monitors.project_registry import ProjectConfig, ProjectRegistry
from monitors.recovery_history_store import RecoveryHistoryStore
from monitors.report_index_store import (
    REPORT_TYPE_AUTO_RECOVERY,
    REPORT_TYPE_ROLLBACK,
    ReportIndexStore,
)
from monitors.trace_store import (
    APPROVAL_RECORD_REQUEST,
    APPROVAL_STATUS_APPROVED,
    APPROVAL_STATUS_EXECUTION_BLOCKED,
    APPROVAL_STATUS_EXECUTION_FAILED,
    APPROVAL_STATUS_EXECUTION_SUCCEEDED,
    TRACE_STAGE_EXECUTION_FINISHED,
    ApprovalStore,
    TraceStore,
)
from recovery.auto_recovery_runner import AutoRecoveryRunner
from sessions import TroubleshootingSession
from web_ui.runtime_control import (
    JOB_STATUS_BLOCKED,
    JOB_STATUS_FAILED,
    JOB_STATUS_QUEUED,
    JOB_STATUS_RUNNING,
    JOB_STATUS_SUCCEEDED,
    RUNTIME_STATUS_CONNECTED,
    RUNTIME_STATUS_ERROR,
    RUNTIME_STATUS_SERVICE_RUNNING,
    JobStore,
)


APPROVED_RECOVERY_JOB_ACTION = "approved_recovery_job"

RunnerFactory = Callable[[ProjectConfig, Any, TraceStore, ApprovalStore], Any]
SessionFactory = Callable[[ProjectConfig], Any]


class ApprovedRecoveryWorker:
    """
    Consume approved human approval requests through the existing recovery runner.

    This worker is intentionally thin: it creates a job and reconstructs the
    event snapshot, but execution authorization remains inside
    AutoRecoveryRunner.recover_after_approval(), where precheck, cooldown,
    rollback gate, policy gate, fingerprint, and fix_id are all re-evaluated.
    """

    def __init__(
        self,
        *,
        project_id: str,
        state_dir: str = "state",
        config_path: str = "configs/projects.yaml",
        output_root: str = "outputs/monitors",
        runner_factory: RunnerFactory | None = None,
        session_factory: SessionFactory | None = None,
    ) -> None:
        self.project_id = project_id
        self.state_dir = state_dir
        self.config_path = config_path
        self.output_root = output_root
        self.runner_factory = runner_factory or self._default_runner_factory
        self.session_factory = session_factory or self._build_session
        self.job_store = JobStore(project_id=project_id, state_dir=state_dir)
        self.trace_store = TraceStore(project_id=project_id, state_dir=state_dir)
        self.approval_store = ApprovalStore(
            project_id=project_id,
            state_dir=state_dir,
            trace_store=self.trace_store,
        )
        self.report_store = ReportIndexStore(project_id=project_id, state_dir=state_dir)
        self.recovery_history_store = RecoveryHistoryStore(
            project_id=project_id,
            state_dir=state_dir,
        )

    @property
    def project(self) -> ProjectConfig:
        return ProjectRegistry(self.config_path).get(self.project_id)

    def latest_approved_request(self) -> dict[str, Any]:
        requests: dict[str, dict[str, Any]] = {}
        latest: dict[str, dict[str, Any]] = {}

        for record in self.approval_store.read_all():
            request_id = str(record.get("request_id", ""))
            if not request_id:
                continue
            latest[request_id] = record
            if record.get("record_type") == APPROVAL_RECORD_REQUEST:
                requests[request_id] = record

        approved = [
            request
            for request_id, request in requests.items()
            if str(latest.get(request_id, {}).get("status", "")) == APPROVAL_STATUS_APPROVED
        ]
        if not approved:
            return {}

        return sorted(
            approved,
            key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""),
        )[-1]

    def run_latest_approved(self, *, operator: str = "web-ui") -> dict[str, Any]:
        request = self.latest_approved_request()
        if not request:
            job = self.job_store.create(
                action=APPROVED_RECOVERY_JOB_ACTION,
                operator=operator,
                payload={"request_id": ""},
                runtime_status=RUNTIME_STATUS_CONNECTED,
                summary="未找到已批准且未消费的审批请求",
            )
            completed = self.job_store.complete(
                job["job_id"],
                status=JOB_STATUS_BLOCKED,
                runtime_status=RUNTIME_STATUS_CONNECTED,
                summary="未找到已批准且未消费的审批请求",
                result={
                    "failure_reason": "approved_request_not_found",
                    "output_summary": "没有 status=approved 的审批请求可供 worker 消费。",
                    "related_trace": _related_trace_paths(self.project_id, self.state_dir),
                },
            )
            return self._response(completed)

        return self.run_for_request(
            str(request.get("request_id", "")),
            operator=operator,
        )

    def run_for_request(
        self,
        request_id: str,
        *,
        operator: str = "web-ui",
        job_id: str = "",
    ) -> dict[str, Any]:
        active = self._active_job_for_request(request_id, ignore_job_id=job_id)
        if active:
            return self._response(active)

        if job_id:
            job = self.job_store.get(job_id)
            if job.get("status") != JOB_STATUS_RUNNING:
                job = self.job_store.mark_running(
                    job_id,
                    runtime_status=RUNTIME_STATUS_SERVICE_RUNNING,
                    summary="审批后恢复任务执行中",
                )
        else:
            job = self.job_store.create(
                action=APPROVED_RECOVERY_JOB_ACTION,
                operator=operator,
                payload={"request_id": request_id},
                runtime_status=RUNTIME_STATUS_SERVICE_RUNNING,
                summary="审批后恢复任务已排队",
            )
            self.job_store.mark_running(
                job["job_id"],
                runtime_status=RUNTIME_STATUS_SERVICE_RUNNING,
                summary="审批后恢复任务执行中",
            )

        try:
            request = self.approval_store.get_request(request_id)
            latest = self.approval_store.latest_record(request_id)
        except KeyError as exc:
            completed = self.job_store.complete(
                job["job_id"],
                status=JOB_STATUS_BLOCKED,
                runtime_status=RUNTIME_STATUS_CONNECTED,
                summary="审批请求不存在",
                result={
                    "failure_reason": "approval_request_not_found",
                    "output_summary": str(exc),
                    "request_id": request_id,
                    "related_trace": _related_trace_paths(self.project_id, self.state_dir),
                },
            )
            return self._response(completed)

        current_status = str(latest.get("status", ""))
        if current_status != APPROVAL_STATUS_APPROVED:
            completed = self.job_store.complete(
                job["job_id"],
                status=JOB_STATUS_BLOCKED,
                runtime_status=RUNTIME_STATUS_CONNECTED,
                summary="审批请求不是 approved 状态",
                result={
                    "failure_reason": f"approval_status_not_approved:{current_status or '<missing>'}",
                    "output_summary": "worker 不会消费 rejected、expired、pending 或已执行过的审批请求。",
                    "request_id": request_id,
                    "approval_status": current_status,
                    "related_trace": _related_trace_paths(self.project_id, self.state_dir),
                },
            )
            return self._response(completed)

        block_reason = self._worker_preflight_block_reason(request)
        if block_reason:
            result = {
                "failure_reason": block_reason,
                "output_summary": "审批请求不满足 worker 消费前置条件，未进入恢复 runner。",
                "request_id": request_id,
                "approval_status": current_status,
                "related_trace": _related_trace_paths(self.project_id, self.state_dir),
            }
            self._record_execution(
                request=request,
                job_id=job["job_id"],
                status=APPROVAL_STATUS_EXECUTION_BLOCKED,
                operator=operator,
                summary="审批后恢复任务被 worker 前置检查阻断",
                reason=block_reason,
                result=result,
                audit_record={},
            )
            self._trace_worker_finished(
                request=request,
                job_id=job["job_id"],
                job_status=JOB_STATUS_BLOCKED,
                reason=block_reason,
                audit_summary={},
            )
            completed = self.job_store.complete(
                job["job_id"],
                status=JOB_STATUS_BLOCKED,
                runtime_status=RUNTIME_STATUS_CONNECTED,
                summary="审批后恢复任务被阻断",
                result=result,
            )
            return self._response(completed)

        try:
            project = self.project
            session = self.session_factory(project)
            self._attach_event_evidence(session, request)
            runner = self.runner_factory(
                project,
                session,
                self.trace_store,
                self.approval_store,
            )
            event = _event_from_request(request)
            recovery_result = runner.recover_after_approval(event, request_id)
            audit_record = _audit_record(recovery_result)
            audit_summary = _audit_summary(recovery_result)
            job_status = _job_status_from_recovery_result(recovery_result, audit_record)
            approval_execution_status = _approval_execution_status(job_status)
            failure_reason = _failure_reason(recovery_result, audit_record, job_status)
            output_summary = _output_summary(recovery_result, audit_record)
            indexed_reports = self._index_recovery_reports(
                request=request,
                recovery_result=recovery_result,
                audit_record=audit_record,
                job_id=job["job_id"],
                job_status=job_status,
            )
            recovery_history = self._record_recovery_history(
                request=request,
                recovery_result=recovery_result,
                audit_record=audit_record,
                indexed_reports=indexed_reports,
                job_id=job["job_id"],
                job_status=job_status,
                session=session,
                operator=operator,
            )
            result_payload = _result_payload(
                request=request,
                recovery_result=recovery_result,
                audit_record=audit_record,
                indexed_reports=indexed_reports,
                recovery_history=recovery_history,
                output_summary=output_summary,
                failure_reason=failure_reason,
                state_dir=self.state_dir,
            )
            self._record_execution(
                request=request,
                job_id=job["job_id"],
                status=approval_execution_status,
                operator=operator,
                summary=output_summary,
                reason=failure_reason,
                result=result_payload,
                audit_record=audit_record,
            )
            self._trace_worker_finished(
                request=request,
                job_id=job["job_id"],
                job_status=job_status,
                reason=failure_reason,
                audit_summary=audit_summary,
            )
            completed = self.job_store.complete(
                job["job_id"],
                status=job_status,
                runtime_status=(
                    RUNTIME_STATUS_CONNECTED
                    if job_status in {JOB_STATUS_SUCCEEDED, JOB_STATUS_BLOCKED}
                    else RUNTIME_STATUS_ERROR
                ),
                summary=_job_summary(job_status),
                result=result_payload,
            )
            return self._response(completed)
        except Exception as exc:
            failure_reason = f"{type(exc).__name__}: {exc}"
            result = {
                "failure_reason": type(exc).__name__,
                "output_summary": failure_reason,
                "request_id": request_id,
                "related_trace": _related_trace_paths(self.project_id, self.state_dir),
            }
            self._record_execution(
                request=request,
                job_id=job["job_id"],
                status=APPROVAL_STATUS_EXECUTION_FAILED,
                operator=operator,
                summary=failure_reason,
                reason=type(exc).__name__,
                result=result,
                audit_record={},
            )
            self._trace_worker_finished(
                request=request,
                job_id=job["job_id"],
                job_status=JOB_STATUS_FAILED,
                reason=type(exc).__name__,
                audit_summary={},
            )
            completed = self.job_store.complete(
                job["job_id"],
                status=JOB_STATUS_FAILED,
                runtime_status=RUNTIME_STATUS_ERROR,
                summary=failure_reason,
                result=result,
            )
            return self._response(completed)

    def _active_job_for_request(
        self,
        request_id: str,
        *,
        ignore_job_id: str = "",
    ) -> dict[str, Any]:
        for job in self.job_store.jobs():
            if ignore_job_id and job.get("job_id") == ignore_job_id:
                continue
            if job.get("action") != APPROVED_RECOVERY_JOB_ACTION:
                continue
            payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
            if str(payload.get("request_id", "")) != request_id:
                continue
            if job.get("status") in {JOB_STATUS_QUEUED, JOB_STATUS_RUNNING}:
                return job
        return {}

    def _worker_preflight_block_reason(self, request: dict[str, Any]) -> str:
        if request.get("approvable") is not True:
            return "approval_request_not_approvable"
        if request.get("approval_scope") != "existing_safe_fix":
            return "approval_scope_not_existing_safe_fix"
        if not str(request.get("fingerprint", "")):
            return "approval_fingerprint_missing"
        if not str(request.get("event_type", "")):
            return "approval_event_type_missing"
        if not str(request.get("selected_fix_id", "")):
            return "approval_fix_id_missing"
        return ""

    def _build_session(self, project: ProjectConfig) -> TroubleshootingSession:
        session = TroubleshootingSession(
            output_root=str(Path(self.output_root) / self.project_id),
            agent_depth="balanced",
            report_mode="rule",
            project_dir=project.project_dir,
            run_command=project.run_command,
        )
        if project.is_remote:
            session.set_remote_profile(
                user=project.ssh.user,
                host=project.ssh.host,
                port=project.ssh.port,
                name=project.project_id,
                key_path=project.ssh.resolved_key_path(),
            )
        return session

    @staticmethod
    def _default_runner_factory(
        project: ProjectConfig,
        session: TroubleshootingSession,
        trace_store: TraceStore,
        approval_store: ApprovalStore,
    ) -> AutoRecoveryRunner:
        return AutoRecoveryRunner(
            project=project,
            session=session,
            trace_store=trace_store,
            approval_store=approval_store,
        )

    @staticmethod
    def _attach_event_evidence(session: Any, request: dict[str, Any]) -> None:
        if not hasattr(session, "add_evidence"):
            return
        session.add_evidence(
            content=_event_evidence_text(request),
            source="approval_request",
            title=f"审批后恢复请求：{request.get('event_type', '')}",
            issue_type=str(request.get("issue_type", "")),
        )

    def _record_execution(
        self,
        *,
        request: dict[str, Any],
        job_id: str,
        status: str,
        operator: str,
        summary: str,
        reason: str,
        result: dict[str, Any],
        audit_record: dict[str, Any],
    ) -> None:
        self.approval_store.record_execution(
            str(request.get("request_id", "")),
            status=status,
            operator=operator,
            job_id=job_id,
            summary=summary,
            reason=reason,
            result=result,
            audit_record=audit_record,
        )

    def _index_recovery_reports(
        self,
        *,
        request: dict[str, Any],
        recovery_result: Any,
        audit_record: dict[str, Any],
        job_id: str,
        job_status: str,
    ) -> list[dict[str, Any]]:
        fingerprint = str(request.get("fingerprint", ""))
        event_type = str(request.get("event_type", ""))
        metadata = {
            "request_id": request.get("request_id", ""),
            "job_status": job_status,
            "recovered": bool(getattr(recovery_result, "recovered", False)),
            "apply_success": bool(getattr(recovery_result, "apply_success", False)),
            "rerun_success": bool(getattr(recovery_result, "rerun_success", False)),
            "rollback_executed": bool(getattr(recovery_result, "rollback_executed", False)),
            "rollback_success": bool(getattr(recovery_result, "rollback_success", False)),
        }
        report_paths = list(getattr(recovery_result, "report_paths", []) or [])
        records = self.report_store.register_reports(
            report_paths,
            report_type=REPORT_TYPE_AUTO_RECOVERY,
            fingerprint=fingerprint,
            event_type=event_type,
            job_id=job_id,
            metadata=metadata,
        )
        if bool(getattr(recovery_result, "rollback_executed", False)):
            records.extend(
                self.report_store.register_reports(
                    report_paths,
                    report_type=REPORT_TYPE_ROLLBACK,
                    fingerprint=fingerprint,
                    event_type=event_type,
                    job_id=job_id,
                    metadata=metadata,
                )
            )
        if audit_record:
            records.append(
                self.report_store.register_audit_json(
                    audit_json=audit_record,
                    fingerprint=fingerprint,
                    event_type=event_type,
                    job_id=job_id,
                    metadata=metadata,
                )
            )
        return records

    def _record_recovery_history(
        self,
        *,
        request: dict[str, Any],
        recovery_result: Any,
        audit_record: dict[str, Any],
        indexed_reports: list[dict[str, Any]],
        job_id: str,
        job_status: str,
        session: Any,
        operator: str,
    ) -> list[dict[str, Any]]:
        if not bool(getattr(recovery_result, "apply_success", False)):
            return []

        apply_edits = list(getattr(recovery_result, "apply_edit_summary", []) or [])
        if not apply_edits:
            return []

        record_path, record_index = _latest_apply_record_location(
            session=session,
            remote=self.project.is_remote,
        )
        applied = self.recovery_history_store.register_applied(
            fix_id=str(audit_record.get("selected_fix_id") or audit_record.get("fix_id") or request.get("selected_fix_id", "")),
            edits=apply_edits,
            record_path=record_path,
            record_index=record_index,
            fingerprint=str(request.get("fingerprint", "")),
            event_type=str(request.get("event_type", "")),
            job_id=job_id,
            request_id=str(request.get("request_id", "")),
            mode="remote" if self.project.is_remote else "local",
            source=APPROVED_RECOVERY_JOB_ACTION,
            audit_json=audit_record,
            metadata={
                "job_status": job_status,
                "indexed_report_ids": [
                    str(item.get("report_id", "")) for item in indexed_reports
                ],
            },
        )
        if not applied:
            return []

        records = [applied]
        if bool(getattr(recovery_result, "rollback_executed", False)):
            records.append(
                self.recovery_history_store.record_rollback_started(
                    target=applied,
                    job_id=job_id,
                    operator=operator,
                    metadata={"source": APPROVED_RECOVERY_JOB_ACTION},
                )
            )
            records.append(
                self.recovery_history_store.record_rollback_finished(
                    target=applied,
                    job_id=job_id,
                    operator=operator,
                    success=bool(getattr(recovery_result, "rollback_success", False)),
                    rollback_edits=list(getattr(recovery_result, "rollback_edit_summary", []) or []),
                    report_id=_first_report_id(indexed_reports, REPORT_TYPE_ROLLBACK),
                    audit_json=audit_record,
                    metadata={"source": APPROVED_RECOVERY_JOB_ACTION},
                )
            )
        return records

    def _trace_worker_finished(
        self,
        *,
        request: dict[str, Any],
        job_id: str,
        job_status: str,
        reason: str,
        audit_summary: dict[str, Any],
    ) -> None:
        self.trace_store.append(
            TRACE_STAGE_EXECUTION_FINISHED,
            event_type=str(request.get("event_type", "")),
            fingerprint=str(request.get("fingerprint", "")),
            payload={
                "status": "worker_finished",
                "job_id": job_id,
                "job_status": job_status,
                "request_id": request.get("request_id", ""),
                "selected_fix_id": request.get("selected_fix_id", ""),
                "failure_reason": reason,
                "recovery_audit_summary": audit_summary,
            },
        )

    def _response(self, job: dict[str, Any]) -> dict[str, Any]:
        return {
            "job": job,
            "jobs": self.job_store.jobs(limit=20),
        }


def _event_from_request(request: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(
        event_type=str(request.get("event_type", "")),
        issue_type=str(request.get("issue_type", "")) or str(request.get("event_type", "")),
        severity=str(request.get("severity", "")) or "medium",
        summary=str(request.get("summary", "")),
        source=str(request.get("source", "")) or "approval_request",
        matched_keywords=[],
        raw_excerpt=_event_evidence_text(request),
        signature=f"approval:{request.get('fingerprint', '')}",
        fingerprint=str(request.get("fingerprint", "")),
        line_number=0,
    )


def _event_evidence_text(request: dict[str, Any]) -> str:
    return (
        "[APPROVED_RECOVERY_REQUEST]\n"
        f"request_id: {request.get('request_id', '')}\n"
        f"event_type: {request.get('event_type', '')}\n"
        f"issue_type: {request.get('issue_type', '')}\n"
        f"severity: {request.get('severity', '')}\n"
        f"summary: {request.get('summary', '')}\n"
        f"source: {request.get('source', '')}\n"
        f"fingerprint: {request.get('fingerprint', '')}\n"
        f"selected_fix_id: {request.get('selected_fix_id', '')}\n"
    )


def _job_status_from_recovery_result(
    recovery_result: Any,
    audit_record: dict[str, Any],
) -> str:
    if bool(getattr(recovery_result, "recovered", False)):
        return JOB_STATUS_SUCCEEDED

    gate = getattr(recovery_result, "r15_gate", None)
    if gate is not None and getattr(gate, "allowed_to_execute", False) is not True:
        return JOB_STATUS_BLOCKED

    execution_result = str(audit_record.get("execution_result", ""))
    if execution_result.startswith("not_run_"):
        return JOB_STATUS_BLOCKED

    return JOB_STATUS_FAILED


def _approval_execution_status(job_status: str) -> str:
    if job_status == JOB_STATUS_SUCCEEDED:
        return APPROVAL_STATUS_EXECUTION_SUCCEEDED
    if job_status == JOB_STATUS_BLOCKED:
        return APPROVAL_STATUS_EXECUTION_BLOCKED
    return APPROVAL_STATUS_EXECUTION_FAILED


def _failure_reason(
    recovery_result: Any,
    audit_record: dict[str, Any],
    job_status: str,
) -> str:
    if job_status == JOB_STATUS_SUCCEEDED:
        return ""

    gate = getattr(recovery_result, "r15_gate", None)
    if gate is not None and getattr(gate, "downgrade_reason", ""):
        return str(getattr(gate, "downgrade_reason", ""))

    execution_result = str(audit_record.get("execution_result", ""))
    if execution_result:
        return execution_result

    messages = getattr(recovery_result, "messages", [])
    if isinstance(messages, list) and messages:
        return _truncate(" ".join(str(item) for item in messages))

    return "approved_recovery_not_recovered"


def _output_summary(recovery_result: Any, audit_record: dict[str, Any]) -> str:
    execution_result = str(audit_record.get("execution_result", ""))
    fix_id = str(audit_record.get("selected_fix_id") or audit_record.get("fix_id") or "")
    recovered = bool(getattr(recovery_result, "recovered", False))
    if execution_result or fix_id:
        return f"execution_result={execution_result or '<unknown>'}; fix_id={fix_id or '<none>'}; recovered={recovered}"
    return f"approved recovery finished; recovered={recovered}"


def _result_payload(
    *,
    request: dict[str, Any],
    recovery_result: Any,
    audit_record: dict[str, Any],
    indexed_reports: list[dict[str, Any]],
    recovery_history: list[dict[str, Any]] | None = None,
    output_summary: str,
    failure_reason: str,
    state_dir: str,
) -> dict[str, Any]:
    return {
        "request_id": request.get("request_id", ""),
        "event_type": request.get("event_type", ""),
        "fingerprint": request.get("fingerprint", ""),
        "selected_fix_id": request.get("selected_fix_id", ""),
        "approval_status": str(audit_record.get("approval_status", "")),
        "execution_result": str(audit_record.get("execution_result", "")),
        "output_summary": output_summary,
        "failure_reason": failure_reason,
        "recovered": bool(getattr(recovery_result, "recovered", False)),
        "apply_success": bool(getattr(recovery_result, "apply_success", False)),
        "rerun_success": bool(getattr(recovery_result, "rerun_success", False)),
        "rollback_executed": bool(getattr(recovery_result, "rollback_executed", False)),
        "rollback_success": bool(getattr(recovery_result, "rollback_success", False)),
        "report_paths": list(getattr(recovery_result, "report_paths", []) or []),
        "indexed_reports": indexed_reports,
        "recovery_history": list(recovery_history or []),
        "audit_json": audit_record,
        "related_trace": _related_trace_paths(
            str(request.get("project_id", "")),
            state_dir,
        ),
    }


def _audit_record(recovery_result: Any) -> dict[str, Any]:
    method = getattr(recovery_result, "recovery_audit_record", None)
    if callable(method):
        try:
            return dict(method())
        except Exception:
            return {}
    return {}


def _audit_summary(recovery_result: Any) -> dict[str, Any]:
    method = getattr(recovery_result, "recovery_audit_summary", None)
    if callable(method):
        try:
            return dict(method())
        except Exception:
            return {}
    audit = _audit_record(recovery_result)
    return {
        "action": audit.get("action", ""),
        "execution_result": audit.get("execution_result", ""),
        "recovered": audit.get("recovered", False),
        "allowed_to_execute": audit.get("allowed_to_execute", False),
        "downgrade_reason": audit.get("downgrade_reason", ""),
    }


def _job_summary(job_status: str) -> str:
    if job_status == JOB_STATUS_SUCCEEDED:
        return "审批后恢复任务已完成"
    if job_status == JOB_STATUS_BLOCKED:
        return "审批后恢复任务被安全规则阻断"
    return "审批后恢复任务失败"


def _truncate(text: str, limit: int = 280) -> str:
    cleaned = " ".join(str(text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3] + "..."


def _related_trace_paths(project_id: str, state_dir: str) -> dict[str, str]:
    base = Path(state_dir) / project_id
    return {
        "trace_events": str(base / "trace_events.jsonl"),
        "approval_requests": str(base / "approval_requests.jsonl"),
        "jobs": str(base / "jobs.jsonl"),
    }


def _latest_apply_record_location(*, session: Any, remote: bool) -> tuple[str, int]:
    output_dir = Path(str(getattr(session, "output_dir", "")))
    if not str(output_dir):
        return "", -1
    record_name = "remote_applied_fixes.json" if remote else "applied_fixes.json"
    record_path = output_dir / record_name
    if not record_path.exists():
        return str(record_path), -1

    try:
        data = json.loads(record_path.read_text(encoding="utf-8"))
    except Exception:
        return str(record_path), -1

    if isinstance(data, list) and data:
        return str(record_path), len(data) - 1
    return str(record_path), -1


def _first_report_id(records: list[dict[str, Any]], report_type: str) -> str:
    for record in records:
        if record.get("report_type") == report_type:
            return str(record.get("report_id", ""))
    return ""
