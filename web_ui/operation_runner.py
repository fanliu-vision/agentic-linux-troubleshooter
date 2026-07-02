from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import yaml

from fixers.apply_executor import SafeApplyExecutor
from fixers.remote_apply_executor import RemoteSafeApplyExecutor
from monitors.project_registry import ProjectConfig, ProjectRegistry
from monitors.recovery_history_store import RecoveryHistoryStore
from monitors.report_index_store import (
    REPORT_TYPE_AUTO_RECOVERY,
    REPORT_TYPE_DIAGNOSTIC,
    REPORT_TYPE_ROLLBACK,
    ReportIndexStore,
)
from monitors.trace_store import (
    TRACE_STAGE_EXECUTION_FINISHED,
    TRACE_STAGE_EXECUTION_STARTED,
    TRACE_STAGE_ROLLBACK_FINISHED,
    TRACE_STAGE_ROLLBACK_STARTED,
    TraceStore,
)
from tools.remote_ssh_executor import RemoteSSHProfile
from web_ui.approved_recovery_worker import (
    APPROVED_RECOVERY_JOB_ACTION,
    ApprovedRecoveryWorker,
)
from web_ui.recovery_history import RecoveryHistoryService
from web_ui.runtime_control import (
    JOB_STATUS_BLOCKED,
    JOB_STATUS_CANCELED,
    JOB_STATUS_FAILED,
    JOB_STATUS_RUNNING,
    JOB_STATUS_SUCCEEDED,
    JOB_STATUS_TIMED_OUT,
    DEFAULT_JOB_TIMEOUT_SECONDS,
    RUNTIME_STATUS_CONNECTED,
    RUNTIME_STATUS_DISCONNECTED,
    RUNTIME_STATUS_ERROR,
    RUNTIME_STATUS_MONITOR_RUNNING,
    RUNTIME_STATUS_SERVICE_RUNNING,
    CommandResult,
    CommandRunner,
    JobStore,
    _default_command_runner,
    _now_iso,
    _ssh_args,
    pid_is_alive,
    read_monitor_process,
    write_monitor_process,
)


OP_START_MONITOR = "start_monitor"
OP_STOP_MONITOR = "stop_monitor"
OP_REFRESH_LOGS = "refresh_logs"
OP_GENERATE_REPORT = "generate_report"
OP_DRY_RUN_RECOVERY = "dry_run_recovery"
OP_LIVE_APPLY = "live_apply"
OP_ROLLBACK_LATEST = "rollback_latest"

OPERATION_LABELS = {
    OP_START_MONITOR: "启动监控",
    OP_STOP_MONITOR: "停止监控",
    OP_REFRESH_LOGS: "刷新日志",
    OP_GENERATE_REPORT: "生成报告",
    OP_DRY_RUN_RECOVERY: "执行 dry-run 恢复",
    OP_LIVE_APPLY: "执行 live apply",
    OP_ROLLBACK_LATEST: "回滚最近一次修复",
}

ALLOWED_OPERATIONS = set(OPERATION_LABELS)
EXECUTABLE_JOB_ACTIONS = ALLOWED_OPERATIONS | {APPROVED_RECOVERY_JOB_ACTION}
SHORT_TIMEOUT_SECONDS = 90
MONITOR_DAEMON_LOG = "ui_monitor_daemon.log"


@dataclass
class BackgroundProcess:
    pid: int


PopenFactory = Callable[[list[str], Any], BackgroundProcess]


def _default_popen_factory(args: list[str], log_file: Any) -> BackgroundProcess:
    process = subprocess.Popen(
        args,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        text=True,
    )
    return BackgroundProcess(pid=int(process.pid))


class OperationRunner:
    def __init__(
        self,
        *,
        project_id: str,
        state_dir: str = "state",
        config_path: str = "configs/projects.yaml",
        output_root: str = "outputs/monitors",
        command_runner: CommandRunner | None = None,
        popen_factory: PopenFactory | None = None,
    ) -> None:
        self.project_id = project_id
        self.state_dir = state_dir
        self.config_path = config_path
        self.output_root = output_root
        self.command_runner = command_runner or _default_command_runner
        self.popen_factory = popen_factory or _default_popen_factory
        self.job_store = JobStore(project_id=project_id, state_dir=state_dir)
        self.report_store = ReportIndexStore(project_id=project_id, state_dir=state_dir)
        self.trace_store = TraceStore(project_id=project_id, state_dir=state_dir)
        self.recovery_history_store = RecoveryHistoryStore(
            project_id=project_id,
            state_dir=state_dir,
        )
        self.recovery_history_service = RecoveryHistoryService(
            project_id=project_id,
            state_dir=state_dir,
            config_path=config_path,
            output_root=output_root,
        )

    @property
    def project(self) -> ProjectConfig:
        return ProjectRegistry(self.config_path).get(self.project_id)

    @staticmethod
    def operations() -> list[dict[str, str]]:
        return [
            {"action": action, "label": label}
            for action, label in OPERATION_LABELS.items()
        ]

    def run(
        self,
        action: str,
        *,
        operator: str = "web-ui",
        role: str = "",
        request_audit: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = self.enqueue(
            action,
            operator=operator,
            role=role,
            request_audit=request_audit,
        )
        job = response.get("job") or {}
        if job.get("status") == JOB_STATUS_BLOCKED:
            return response
        return self._response(self.execute_leased_job(str(job.get("job_id", ""))))

    def enqueue(
        self,
        action: str,
        *,
        operator: str = "web-ui",
        role: str = "",
        payload: dict[str, Any] | None = None,
        request_audit: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        action = action.strip()
        job = self.job_store.create(
            action=action,
            operator=operator,
            role=role,
            payload={
                "label": OPERATION_LABELS.get(action, action),
                **dict(payload or {}),
            },
            request_audit=request_audit,
            runtime_status=self._current_runtime_status(),
            summary=f"{OPERATION_LABELS.get(action, action)} queued",
            timeout_seconds=_operation_timeout_seconds(action),
        )

        if action not in EXECUTABLE_JOB_ACTIONS:
            completed = self.job_store.complete(
                job["job_id"],
                status=JOB_STATUS_BLOCKED,
                runtime_status=self._current_runtime_status(),
                summary=f"不支持的操作：{action}",
                result={
                    "failure_reason": "unsupported_operation",
                    "output_summary": "该操作不在 UI 受控 allowlist 中。",
                },
            )
            return self._response(completed)

        return self._response(job)

    def enqueue_rollback_history(
        self,
        target_identity: str,
        *,
        operator: str = "web-ui",
        role: str = "",
        request_audit: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.enqueue(
            OP_ROLLBACK_LATEST,
            operator=operator,
            role=role,
            request_audit=request_audit,
            payload={
                "label": OPERATION_LABELS[OP_ROLLBACK_LATEST],
                "target_identity": target_identity.strip(),
            },
        )

    def enqueue_approved_recovery(
        self,
        request_id: str,
        *,
        operator: str = "web-ui",
        role: str = "",
        request_audit: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.enqueue(
            APPROVED_RECOVERY_JOB_ACTION,
            operator=operator,
            role=role,
            request_audit=request_audit,
            payload={
                "label": "审批后执行",
                "request_id": request_id.strip(),
            },
        )

    def execute_leased_job(self, job_id: str) -> dict[str, Any]:
        job = self.job_store.get(job_id)
        action = str(job.get("action", "")).strip()
        operator = str(job.get("operator") or "web-ui")

        if action not in EXECUTABLE_JOB_ACTIONS:
            return self.job_store.complete(
                job_id,
                status=JOB_STATUS_BLOCKED,
                runtime_status=self._current_runtime_status(),
                summary=f"不支持的操作：{action}",
                result={
                    "failure_reason": "unsupported_operation",
                    "output_summary": "该操作不在 UI 受控 allowlist 中。",
                },
            )

        if self.job_store.is_cancel_requested(job_id):
            return self.job_store.complete(
                job_id,
                status=JOB_STATUS_CANCELED,
                runtime_status=self._current_runtime_status(),
                summary="任务已取消",
                result={
                    "failure_reason": "canceled",
                    "output_summary": "任务在执行前被取消。",
                    "related_trace": _related_trace_paths(self.project_id, self.state_dir),
                },
            )

        if job.get("status") != JOB_STATUS_RUNNING:
            self.job_store.mark_running(
                job_id,
                runtime_status=self._running_runtime_status(action),
                summary=f"{display_operation(action)} running",
                increment_attempt=True,
            )

        payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
        try:
            if action == OP_START_MONITOR:
                completed = self._start_monitor(job_id)
            elif action == OP_STOP_MONITOR:
                completed = self._stop_monitor(job_id)
            elif action == OP_REFRESH_LOGS:
                completed = self._refresh_logs(job_id)
            elif action == OP_GENERATE_REPORT:
                completed = self._run_monitor_once(
                    job_id,
                    purpose="generate_report",
                    report_mode="rule",
                )
            elif action == OP_DRY_RUN_RECOVERY:
                completed = self._run_dry_run_recovery(job_id)
            elif action == OP_LIVE_APPLY:
                completed = self._run_live_apply_after_approval(
                    job_id,
                    operator=operator,
                )
            elif action == OP_ROLLBACK_LATEST:
                target_identity = str(payload.get("target_identity", ""))
                if target_identity:
                    completed = self._execute_rollback_history_job(
                        job_id,
                        target_identity=target_identity,
                        operator=operator,
                    )
                else:
                    completed = self._rollback_latest(job_id, operator=operator)
            elif action == APPROVED_RECOVERY_JOB_ACTION:
                completed = self._run_approved_recovery_job(
                    job_id,
                    request_id=str(payload.get("request_id", "")),
                    operator=operator,
                )
            else:
                raise AssertionError("operation allowlist drift")
            return completed
        except subprocess.TimeoutExpired as exc:
            return self.job_store.complete(
                job_id,
                status=JOB_STATUS_TIMED_OUT,
                runtime_status=RUNTIME_STATUS_ERROR,
                summary=f"任务执行超时：{action}",
                result={
                    "failure_reason": "timeout",
                    "output_summary": str(exc),
                    "related_trace": _related_trace_paths(self.project_id, self.state_dir),
                },
            )
        except Exception as exc:
            return self.job_store.complete(
                job_id,
                status=JOB_STATUS_FAILED,
                runtime_status=RUNTIME_STATUS_ERROR,
                summary=f"{type(exc).__name__}: {exc}",
                result={
                    "failure_reason": type(exc).__name__,
                    "output_summary": str(exc),
                },
            )

    def rollback_history(
        self,
        target_identity: str,
        *,
        operator: str = "web-ui",
    ) -> dict[str, Any]:
        response = self.enqueue_rollback_history(target_identity, operator=operator)
        job = response.get("job") or {}
        completed = self.execute_leased_job(str(job.get("job_id", "")))
        return self._response(completed)

    def _execute_rollback_history_job(
        self,
        job_id: str,
        *,
        target_identity: str,
        operator: str,
    ) -> dict[str, Any]:
        target = self.recovery_history_service.latest_rollback_target()
        if not target_identity or str(target.get("identity", "")) != target_identity:
            return self.job_store.complete(
                job_id,
                status=JOB_STATUS_BLOCKED,
                runtime_status=self._current_runtime_status(),
                summary="回滚目标已不可用",
                result={
                    "failure_reason": "rollback_target_identity_mismatch",
                    "output_summary": "只有当前最新可回滚修复记录允许从 UI 触发回滚。",
                    "requested_identity": target_identity,
                    "current_identity": str(target.get("identity", "")),
                    "related_trace": _related_trace_paths(self.project_id, self.state_dir),
                },
            )

        try:
            return self._rollback_latest(
                job_id,
                target=target,
                operator=operator,
            )
        except Exception as exc:
            return self.job_store.complete(
                job_id,
                status=JOB_STATUS_FAILED,
                runtime_status=RUNTIME_STATUS_ERROR,
                summary=f"{type(exc).__name__}: {exc}",
                result={
                    "failure_reason": type(exc).__name__,
                    "output_summary": str(exc),
                    "related_trace": _related_trace_paths(self.project_id, self.state_dir),
                },
            )

    def _response(self, job: dict[str, Any]) -> dict[str, Any]:
        return {
            "job": job,
            "jobs": self.job_store.jobs(limit=20),
        }

    def _start_monitor(self, job_id: str) -> dict[str, Any]:
        existing = read_monitor_process(
            project_id=self.project_id,
            state_dir=self.state_dir,
        )
        if pid_is_alive(existing.get("pid")):
            return self.job_store.complete(
                job_id,
                status=JOB_STATUS_BLOCKED,
                runtime_status=RUNTIME_STATUS_MONITOR_RUNNING,
                summary="监控已在运行",
                result={
                    "failure_reason": "monitor_already_running",
                    "output_summary": f"pid={existing.get('pid')}",
                    "monitor_process": existing,
                    "related_trace": _related_trace_paths(self.project_id, self.state_dir),
                },
            )

        log_path = Path(self.state_dir) / self.project_id / MONITOR_DAEMON_LOG
        log_path.parent.mkdir(parents=True, exist_ok=True)
        command = self._monitor_command(["--daemon"])
        with log_path.open("a", encoding="utf-8") as log_file:
            process = self.popen_factory(command, log_file)

        process_info = write_monitor_process(
            project_id=self.project_id,
            state_dir=self.state_dir,
            process_info={
                "pid": process.pid,
                "status": "running",
                "started_at": _now_iso(),
                "stopped_at": "",
                "command": command,
                "log_path": str(log_path),
            },
        )
        return self.job_store.complete(
            job_id,
            status=JOB_STATUS_SUCCEEDED,
            runtime_status=RUNTIME_STATUS_MONITOR_RUNNING,
            summary="监控已启动",
            result={
                "output_summary": f"monitor pid={process.pid}",
                "monitor_process": process_info,
                "log_path": str(log_path),
                "related_trace": _related_trace_paths(self.project_id, self.state_dir),
            },
        )

    def _stop_monitor(self, job_id: str) -> dict[str, Any]:
        process_info = read_monitor_process(
            project_id=self.project_id,
            state_dir=self.state_dir,
        )
        pid = process_info.get("pid")
        if not pid_is_alive(pid):
            stopped = write_monitor_process(
                project_id=self.project_id,
                state_dir=self.state_dir,
                process_info={
                    **process_info,
                    "status": "stopped",
                    "stopped_at": _now_iso(),
                },
            )
            return self.job_store.complete(
                job_id,
                status=JOB_STATUS_BLOCKED,
                runtime_status=RUNTIME_STATUS_CONNECTED,
                summary="没有正在运行的监控进程",
                result={
                    "failure_reason": "monitor_not_running",
                    "output_summary": "未找到由 UI 启动且仍存活的监控进程。",
                    "monitor_process": stopped,
                    "related_trace": _related_trace_paths(self.project_id, self.state_dir),
                },
            )

        os.kill(int(pid), signal.SIGTERM)
        stopped = write_monitor_process(
            project_id=self.project_id,
            state_dir=self.state_dir,
            process_info={
                **process_info,
                "status": "stopping",
                "stopped_at": _now_iso(),
            },
        )
        return self.job_store.complete(
            job_id,
            status=JOB_STATUS_SUCCEEDED,
            runtime_status=RUNTIME_STATUS_CONNECTED,
            summary="已发送停止监控信号",
            result={
                "output_summary": f"sent SIGTERM to pid={pid}",
                "monitor_process": stopped,
                "related_trace": _related_trace_paths(self.project_id, self.state_dir),
            },
        )

    def _refresh_logs(self, job_id: str) -> dict[str, Any]:
        project = self.project
        if project.is_remote:
            return self._refresh_remote_logs(job_id, project)
        return self._refresh_local_logs(job_id, project)

    def _refresh_local_logs(
        self,
        job_id: str,
        project: ProjectConfig,
    ) -> dict[str, Any]:
        if not project.log_files:
            return self.job_store.complete(
                job_id,
                status=JOB_STATUS_BLOCKED,
                runtime_status=self._current_runtime_status(),
                summary="没有配置日志文件",
                result={
                    "failure_reason": "log_files_missing",
                    "related_trace": _related_trace_paths(self.project_id, self.state_dir),
                },
            )

        project_dir = Path(project.project_dir or project.effective_project_dir)
        path = Path(project.log_files[0])
        if not path.is_absolute():
            path = project_dir / path
        if not path.exists():
            return self.job_store.complete(
                job_id,
                status=JOB_STATUS_FAILED,
                runtime_status=self._current_runtime_status(),
                summary="日志文件不存在",
                result={
                    "failure_reason": "log_file_missing",
                    "output_summary": str(path),
                    "related_trace": _related_trace_paths(self.project_id, self.state_dir),
                },
            )

        text = _tail_file(path, max_lines=max(20, int(project.monitor.tail_lines)))
        return self.job_store.complete(
            job_id,
            status=JOB_STATUS_SUCCEEDED,
            runtime_status=self._current_runtime_status(),
            summary="日志已刷新",
            result={
                "output_summary": _summarize_text(text),
                "log_path": str(path),
                "stdout_tail": text,
                "related_trace": _related_trace_paths(self.project_id, self.state_dir),
            },
        )

    def _refresh_remote_logs(
        self,
        job_id: str,
        project: ProjectConfig,
    ) -> dict[str, Any]:
        if not project.log_files:
            return self.job_store.complete(
                job_id,
                status=JOB_STATUS_BLOCKED,
                runtime_status=self._current_runtime_status(),
                summary="没有配置远程日志文件",
                result={
                    "failure_reason": "log_files_missing",
                    "related_trace": _related_trace_paths(self.project_id, self.state_dir),
                },
            )

        remote_path = project.log_files[0]
        lines = max(20, min(int(project.monitor.tail_lines), 2000))
        command = f"tail -n {lines} {shlex.quote(remote_path)}"
        result = self.command_runner(_ssh_args(project) + [command], SHORT_TIMEOUT_SECONDS)
        status = JOB_STATUS_SUCCEEDED if result.returncode == 0 else JOB_STATUS_FAILED
        return self.job_store.complete(
            job_id,
            status=status,
            runtime_status=self._current_runtime_status(),
            summary="远程日志已刷新" if status == JOB_STATUS_SUCCEEDED else "远程日志刷新失败",
            result={
                "command_kind": "remote_ssh_readonly",
                "command": command,
                "return_code": result.returncode,
                "output_summary": _summarize_text(result.stdout or result.stderr),
                "stdout_tail": _truncate(result.stdout),
                "stderr_tail": _truncate(result.stderr),
                "failure_reason": "" if status == JOB_STATUS_SUCCEEDED else "remote_tail_failed",
                "related_trace": _related_trace_paths(self.project_id, self.state_dir),
            },
        )

    def _run_monitor_once(
        self,
        job_id: str,
        *,
        purpose: str,
        report_mode: str = "auto",
        config_path: str | None = None,
    ) -> dict[str, Any]:
        command = self._monitor_command(
            [
                "--once",
                "--report-mode",
                report_mode,
            ],
            config_path=config_path,
        )
        result = self.command_runner(command, SHORT_TIMEOUT_SECONDS)
        status = JOB_STATUS_SUCCEEDED if result.returncode == 0 else JOB_STATUS_FAILED
        stdout = result.stdout or ""
        stderr = result.stderr or ""
        report_paths = _extract_report_paths(stdout)
        report_type = (
            REPORT_TYPE_AUTO_RECOVERY
            if purpose == "dry_run_recovery"
            else REPORT_TYPE_DIAGNOSTIC
        )
        indexed_reports = self.report_store.register_reports(
            report_paths,
            report_type=report_type,
            job_id=job_id,
            metadata={
                "operation": purpose,
                "return_code": result.returncode,
                "dry_run": purpose == "dry_run_recovery",
            },
        )
        return self.job_store.complete(
            job_id,
            status=status,
            runtime_status=self._current_runtime_status(),
            summary=_operation_summary(purpose, status),
            result={
                "command_kind": "local_python_entry",
                "command": command,
                "return_code": result.returncode,
                "output_summary": _summarize_text(stdout or stderr),
                "stdout_tail": _truncate(stdout),
                "stderr_tail": _truncate(stderr),
                "related_trace": _related_trace_paths(self.project_id, self.state_dir),
                "report_paths": report_paths,
                "indexed_reports": indexed_reports,
                "failure_reason": "" if status == JOB_STATUS_SUCCEEDED else "monitor_once_failed",
            },
        )

    def _run_dry_run_recovery(self, job_id: str) -> dict[str, Any]:
        temp_config = self._dry_run_config(job_id)
        return self._run_monitor_once(
            job_id,
            purpose="dry_run_recovery",
            report_mode="rule",
            config_path=str(temp_config),
        )

    def _run_live_apply_after_approval(
        self,
        job_id: str,
        *,
        operator: str,
    ) -> dict[str, Any]:
        worker = ApprovedRecoveryWorker(
            project_id=self.project_id,
            state_dir=self.state_dir,
            config_path=self.config_path,
            output_root=self.output_root,
        )
        request = worker.latest_approved_request()
        if not request:
            return self.job_store.complete(
                job_id,
                status=JOB_STATUS_BLOCKED,
                runtime_status=self._current_runtime_status(),
                summary="live apply 已阻断",
                result={
                    "failure_reason": "approved_request_not_found",
                    "output_summary": (
                        "没有已批准且未消费的审批请求。请先在审批面板批准，"
                        "系统会自动创建 approved_recovery_job 并重新跑安全 gate。"
                    ),
                    "related_trace": _related_trace_paths(self.project_id, self.state_dir),
                },
            )

        worker_response = _run_worker_for_request(
            worker,
            str(request.get("request_id", "")),
            operator=operator,
            job_id=job_id,
        )
        worker_job = worker_response.get("job") or {}
        worker_status = str(worker_job.get("status", JOB_STATUS_BLOCKED))
        if worker_status not in {JOB_STATUS_SUCCEEDED, JOB_STATUS_FAILED, JOB_STATUS_BLOCKED}:
            worker_status = JOB_STATUS_BLOCKED
        if str(worker_job.get("job_id", "")) == job_id:
            return dict(worker_job)

        return self.job_store.complete(
            job_id,
            status=worker_status,
            runtime_status=(
                RUNTIME_STATUS_ERROR
                if worker_status == JOB_STATUS_FAILED
                else self._current_runtime_status()
            ),
            summary="live apply 已转交审批后恢复 worker",
            result={
                "failure_reason": (worker_job.get("result") or {}).get("failure_reason", ""),
                "output_summary": (
                    f"approved_recovery_job={worker_job.get('job_id', '')}; "
                    f"status={worker_status}; summary={worker_job.get('summary', '')}"
                ),
                "approved_recovery_job": worker_job,
                "related_trace": _related_trace_paths(self.project_id, self.state_dir),
            },
        )

    def _run_approved_recovery_job(
        self,
        job_id: str,
        *,
        request_id: str,
        operator: str,
    ) -> dict[str, Any]:
        if not request_id:
            return self.job_store.complete(
                job_id,
                status=JOB_STATUS_BLOCKED,
                runtime_status=self._current_runtime_status(),
                summary="审批后执行任务缺少 request_id",
                result={
                    "failure_reason": "request_id_missing",
                    "output_summary": "approved_recovery_job 必须绑定一个已批准审批请求。",
                    "related_trace": _related_trace_paths(self.project_id, self.state_dir),
                },
            )

        worker = ApprovedRecoveryWorker(
            project_id=self.project_id,
            state_dir=self.state_dir,
            config_path=self.config_path,
            output_root=self.output_root,
        )
        response = _run_worker_for_request(
            worker,
            request_id,
            operator=operator,
            job_id=job_id,
        )
        return dict(response.get("job") or self.job_store.get(job_id))

    def _rollback_latest(
        self,
        job_id: str,
        *,
        target: dict[str, Any] | None = None,
        operator: str = "web-ui",
    ) -> dict[str, Any]:
        project = self.project
        target = target or self.recovery_history_service.latest_rollback_target()
        if not target:
            record_name = "remote_applied_fixes.json" if project.is_remote else "applied_fixes.json"
            return self.job_store.complete(
                job_id,
                status=JOB_STATUS_BLOCKED,
                runtime_status=self._current_runtime_status(),
                summary="没有可回滚的修复记录",
                result={
                    "failure_reason": "applied_fix_record_missing",
                    "output_summary": f"未在 {self.output_root}/{self.project_id} 下找到 {record_name}",
                    "related_trace": _related_trace_paths(self.project_id, self.state_dir),
                },
            )

        applied_history = self.recovery_history_store.register_applied(
            fix_id=str(target.get("fix_id", "")),
            edits=list(target.get("edits") or []),
            record_path=str(target.get("record_path", "")),
            record_index=int(target.get("record_index", -1)),
            fingerprint=str(target.get("fingerprint", "")),
            event_type=str(target.get("event_type", "")),
            job_id=str(target.get("job_id", "")),
            request_id=str(target.get("request_id", "")),
            mode="remote" if project.is_remote else "local",
            source=OP_ROLLBACK_LATEST,
            audit_json=dict(target.get("audit_json") or {}),
            metadata={"rollback_job_id": job_id},
        )
        if applied_history:
            target = {**target, **applied_history}

        event_type = str(target.get("event_type", ""))
        fingerprint = str(target.get("fingerprint", ""))
        trace_base = {
            "operation": OP_ROLLBACK_LATEST,
            "job_id": job_id,
            "target_history_id": target.get("history_id", ""),
            "target_identity": target.get("identity", ""),
            "target_fix_id": target.get("fix_id", ""),
            "record_path": target.get("record_path", ""),
            "record_index": target.get("record_index", -1),
            "mode": "remote" if project.is_remote else "local",
            "operator": operator,
        }
        self.trace_store.append(
            TRACE_STAGE_EXECUTION_STARTED,
            event_type=event_type,
            fingerprint=fingerprint,
            payload={**trace_base, "status": "rollback_job_started"},
        )
        self.trace_store.append(
            TRACE_STAGE_EXECUTION_FINISHED,
            event_type=event_type,
            fingerprint=fingerprint,
            payload={**trace_base, "status": "rollback_target_resolved"},
        )
        self.trace_store.append(
            TRACE_STAGE_ROLLBACK_STARTED,
            event_type=event_type,
            fingerprint=fingerprint,
            payload={
                **trace_base,
                "rollback_edit_target": list(target.get("edits") or []),
                "backup_record": target.get("backup_record", {}),
            },
        )
        rollback_started = self.recovery_history_store.record_rollback_started(
            target=target,
            job_id=job_id,
            operator=operator,
            metadata={"operation": OP_ROLLBACK_LATEST},
        )

        record_path = Path(str(target.get("record_path", "")))
        if project.is_remote:
            profile = RemoteSSHProfile(
                host=project.ssh.host,
                user=project.ssh.user,
                port=project.ssh.port,
                name=project.project_id,
                key_path=project.ssh.resolved_key_path(),
            )
            executor = RemoteSafeApplyExecutor(
                profile=profile,
                session_dir=str(record_path.parent),
            )
            result = executor.rollback_latest()
            text = result.to_markdown()
            success = bool(result.success)
        else:
            executor = SafeApplyExecutor(
                project_dir=project.effective_project_dir,
                session_dir=str(record_path.parent),
            )
            result = executor.rollback_latest()
            text = result.to_markdown()
            success = bool(result.success)

        rollback_edits = _edit_records_from_apply_result(result)
        audit_json = {
            "operation": OP_ROLLBACK_LATEST,
            "job_id": job_id,
            "project_id": self.project_id,
            "mode": "remote" if project.is_remote else "local",
            "target_history_id": target.get("history_id", ""),
            "target_identity": target.get("identity", ""),
            "target_fix_id": target.get("fix_id", ""),
            "fingerprint": fingerprint,
            "event_type": event_type,
            "record_path": str(record_path),
            "record_index": target.get("record_index", -1),
            "operator": operator,
            "backup_record": target.get("backup_record", {}),
            "planned_rollback_edits": list(target.get("edits") or []),
            "rollback_edit_summary": rollback_edits,
            "rollback_success": success,
            "rollback_started": rollback_started,
        }
        report = self.report_store.register_text_report(
            content=text,
            report_type=REPORT_TYPE_ROLLBACK,
            fingerprint=fingerprint,
            event_type=event_type,
            job_id=job_id,
            title="回滚最近一次修复报告",
            metadata={
                "operation": OP_ROLLBACK_LATEST,
                "success": success,
                "record_path": str(record_path),
                "mode": "remote" if project.is_remote else "local",
            },
        )
        audit_report = self.report_store.register_audit_json(
            audit_json=audit_json,
            fingerprint=fingerprint,
            event_type=event_type,
            job_id=job_id,
            title="回滚审计 JSON",
            metadata={
                "operation": OP_ROLLBACK_LATEST,
                "success": success,
                "record_path": str(record_path),
            },
        )
        rollback_finished = self.recovery_history_store.record_rollback_finished(
            target=target,
            job_id=job_id,
            operator=operator,
            success=success,
            rollback_edits=rollback_edits,
            report_id=str(report.get("report_id", "")),
            audit_json=audit_json,
            metadata={
                "operation": OP_ROLLBACK_LATEST,
                "audit_report_id": audit_report.get("report_id", ""),
            },
        )
        self.trace_store.append(
            TRACE_STAGE_ROLLBACK_FINISHED,
            event_type=event_type,
            fingerprint=fingerprint,
            payload={
                **trace_base,
                "rollback_success": success,
                "rollback_edit_summary": rollback_edits,
                "report_id": report.get("report_id", ""),
                "audit_report_id": audit_report.get("report_id", ""),
            },
        )
        return self.job_store.complete(
            job_id,
            status=JOB_STATUS_SUCCEEDED if success else JOB_STATUS_FAILED,
            runtime_status=self._current_runtime_status(),
            summary="回滚完成" if success else "回滚失败",
            result={
                "output_summary": _summarize_text(text),
                "stdout_tail": _truncate(text),
                "record_path": str(record_path),
                "indexed_reports": [report, audit_report],
                "recovery_history": {
                    "applied": applied_history,
                    "rollback_started": rollback_started,
                    "rollback_finished": rollback_finished,
                },
                "audit_json": audit_json,
                "failure_reason": "" if success else "rollback_failed",
                "related_trace": _related_trace_paths(self.project_id, self.state_dir),
            },
        )

    def _dry_run_config(self, job_id: str) -> Path:
        source = Path(self.config_path)
        data = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
        for project in data.get("projects", []) or []:
            if str(project.get("project_id", "")) != self.project_id:
                continue
            policy = project.setdefault("policy", {})
            policy["auto_recovery_dry_run"] = True
            policy["require_human_approval_for_live_apply"] = True
            break

        target = Path(self.state_dir) / self.project_id / "operation_configs" / f"{job_id}.dry_run.yaml"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
        return target

    def _monitor_command(
        self,
        extra_args: list[str],
        *,
        config_path: str | None = None,
    ) -> list[str]:
        return [
            sys.executable,
            "main_monitor.py",
            "--config",
            config_path or self.config_path,
            "--project",
            self.project_id,
            "--state-dir",
            self.state_dir,
            "--output-root",
            self.output_root,
            *extra_args,
        ]

    def _current_runtime_status(self) -> str:
        process = read_monitor_process(project_id=self.project_id, state_dir=self.state_dir)
        if pid_is_alive(process.get("pid")):
            return RUNTIME_STATUS_MONITOR_RUNNING
        latest = self.job_store.latest_job(actions={"connect", "health_check"})
        if latest.get("status") == JOB_STATUS_SUCCEEDED:
            return str(latest.get("runtime_status") or RUNTIME_STATUS_CONNECTED)
        return RUNTIME_STATUS_DISCONNECTED

    @staticmethod
    def _running_runtime_status(action: str) -> str:
        if action == OP_START_MONITOR:
            return RUNTIME_STATUS_MONITOR_RUNNING
        if action in {
            OP_REFRESH_LOGS,
            OP_GENERATE_REPORT,
            OP_DRY_RUN_RECOVERY,
            APPROVED_RECOVERY_JOB_ACTION,
        }:
            return RUNTIME_STATUS_SERVICE_RUNNING
        return RUNTIME_STATUS_CONNECTED


def display_operation(action: str) -> str:
    if action == APPROVED_RECOVERY_JOB_ACTION:
        return "审批后执行"
    return OPERATION_LABELS.get(action, action)


def _operation_timeout_seconds(action: str) -> int:
    if action in {OP_LIVE_APPLY, OP_ROLLBACK_LATEST, APPROVED_RECOVERY_JOB_ACTION}:
        return 10 * 60
    if action == OP_START_MONITOR:
        return 60
    if action in {OP_GENERATE_REPORT, OP_DRY_RUN_RECOVERY}:
        return max(2 * SHORT_TIMEOUT_SECONDS, DEFAULT_JOB_TIMEOUT_SECONDS)
    return DEFAULT_JOB_TIMEOUT_SECONDS


def _run_worker_for_request(
    worker: Any,
    request_id: str,
    *,
    operator: str,
    job_id: str,
) -> dict[str, Any]:
    try:
        return worker.run_for_request(request_id, operator=operator, job_id=job_id)
    except TypeError as exc:
        if "job_id" not in str(exc):
            raise
        return worker.run_for_request(request_id, operator=operator)


def _tail_file(path: Path, *, max_lines: int) -> str:
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    return "\n".join(lines[-max_lines:])


def _edit_records_from_apply_result(result: Any) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for item in list(getattr(result, "edit_results", []) or []):
        records.append(
            {
                "success": bool(getattr(item, "success", False)),
                "message": str(getattr(item, "message", "")),
                "config_path": str(getattr(item, "config_path", "")),
                "backup_path": str(getattr(item, "backup_path", "")),
                "diff_path": str(getattr(item, "diff_path", "")),
                "field_path": str(getattr(item, "field_path", "")),
                "old_value": getattr(item, "old_value", None),
                "new_value": getattr(item, "new_value", None),
                "no_op": bool(getattr(item, "no_op", False)),
                "semantic_status": str(getattr(item, "semantic_status", "")),
                "semantic_reason": str(getattr(item, "semantic_reason", "")),
            }
        )
    return records


def _truncate(text: str, limit: int = 6000) -> str:
    if len(text) <= limit:
        return text
    return text[-limit:]


def _summarize_text(text: str, limit: int = 280) -> str:
    cleaned = " ".join((text or "").split())
    if not cleaned:
        return "<empty>"
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3] + "..."


def _extract_report_paths(stdout: str) -> list[str]:
    paths: list[str] = []
    for line in stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            stripped = stripped[2:].strip()
        if stripped.endswith(".md") and ("/" in stripped or "\\" in stripped):
            paths.append(stripped)
        elif "summary_report:" in stripped:
            paths.append(stripped.split("summary_report:", 1)[1].strip())
    return paths


def _related_trace_paths(project_id: str, state_dir: str) -> dict[str, str]:
    base = Path(state_dir) / project_id
    return {
        "trace_events": str(base / "trace_events.jsonl"),
        "approval_requests": str(base / "approval_requests.jsonl"),
        "jobs": str(base / "jobs.jsonl"),
        "reports": str(base / "report_index.jsonl"),
        "recovery_history": str(base / "recovery_history.jsonl"),
    }


def _operation_summary(purpose: str, status: str) -> str:
    label = {
        "generate_report": "生成报告",
        "dry_run_recovery": "dry-run 恢复",
    }.get(purpose, purpose)
    if status == JOB_STATUS_SUCCEEDED:
        return f"{label}已完成"
    return f"{label}失败"


def _latest_apply_record_path(
    *,
    project_id: str,
    output_root: str,
    record_name: str,
) -> Path | None:
    root = Path(output_root) / project_id
    if not root.exists():
        return None
    records = [path for path in root.rglob(record_name) if path.is_file()]
    if not records:
        return None
    return sorted(records, key=lambda path: path.stat().st_mtime)[-1]
