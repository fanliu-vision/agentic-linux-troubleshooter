from __future__ import annotations

import json
import os
import posixpath
import shlex
import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from monitors.jsonl_store import append_jsonl, read_jsonl, rewrite_jsonl
from monitors.project_registry import ProjectConfig, ProjectRegistry


JOB_SCHEMA_VERSION = "job.v1"
JOB_RECORD_UPDATE = "job_update"

JOB_STATUS_QUEUED = "queued"
JOB_STATUS_RUNNING = "running"
JOB_STATUS_CANCEL_REQUESTED = "cancel_requested"
JOB_STATUS_SUCCEEDED = "succeeded"
JOB_STATUS_FAILED = "failed"
JOB_STATUS_BLOCKED = "blocked"
JOB_STATUS_CANCELED = "canceled"
JOB_STATUS_TIMED_OUT = "timed_out"

JOB_STATUSES = {
    JOB_STATUS_QUEUED,
    JOB_STATUS_RUNNING,
    JOB_STATUS_CANCEL_REQUESTED,
    JOB_STATUS_SUCCEEDED,
    JOB_STATUS_FAILED,
    JOB_STATUS_BLOCKED,
    JOB_STATUS_CANCELED,
    JOB_STATUS_TIMED_OUT,
}

JOB_ACTIVE_STATUSES = {
    JOB_STATUS_QUEUED,
    JOB_STATUS_RUNNING,
    JOB_STATUS_CANCEL_REQUESTED,
}

JOB_TERMINAL_STATUSES = JOB_STATUSES - JOB_ACTIVE_STATUSES

CONNECTION_STATUS_DISCONNECTED = "disconnected"
CONNECTION_STATUS_CONNECTING = "connecting"
CONNECTION_STATUS_CONNECTED = "connected"
CONNECTION_STATUS_ERROR = "error"

RUNTIME_STATUS_DISCONNECTED = "disconnected"
RUNTIME_STATUS_CONNECTING = "connecting"
RUNTIME_STATUS_CONNECTED = "connected"
RUNTIME_STATUS_MONITOR_RUNNING = "monitor_running"
RUNTIME_STATUS_SERVICE_RUNNING = "service_running"
RUNTIME_STATUS_ERROR = "error"

RUNTIME_STATUSES = {
    RUNTIME_STATUS_DISCONNECTED,
    RUNTIME_STATUS_CONNECTING,
    RUNTIME_STATUS_CONNECTED,
    RUNTIME_STATUS_MONITOR_RUNNING,
    RUNTIME_STATUS_SERVICE_RUNNING,
    RUNTIME_STATUS_ERROR,
}

CHECK_OK = "ok"
CHECK_ERROR = "error"
CHECK_WARNING = "warning"
CHECK_SKIPPED = "skipped"

DEFAULT_CONFIG_PATH = "config.json"
SSH_OK_TOKEN = "AGENTIC_TRACE_SSH_OK"
SSH_TIMEOUT_SECONDS = 8
MONITOR_PROCESS_FILE = "monitor_process.json"
DEFAULT_JOB_LEASE_SECONDS = 60
DEFAULT_JOB_TIMEOUT_SECONDS = 300
DEFAULT_JOB_MAX_ATTEMPTS = 1


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value)
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso_after(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=max(1, int(seconds)))).isoformat()


def _json_dumps(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


def _jsonable(data: Any) -> Any:
    try:
        json.dumps(data, ensure_ascii=False)
        return data
    except TypeError:
        return json.loads(json.dumps(data, ensure_ascii=False, default=str))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return read_jsonl(path)


def _latest_by_updated_at(records: list[dict[str, Any]]) -> dict[str, Any]:
    if not records:
        return {}
    return sorted(records, key=lambda item: str(item.get("updated_at", "")))[-1]


def _job_sort_key(item: dict[str, Any]) -> tuple[str, str]:
    return (
        str(item.get("updated_at", "")),
        str(item.get("job_id", "")),
    )


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


CommandRunner = Callable[[list[str], int], CommandResult]


def _default_command_runner(args: list[str], timeout: int) -> CommandResult:
    completed = subprocess.run(
        args,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    return CommandResult(
        returncode=int(completed.returncode),
        stdout=str(completed.stdout or ""),
        stderr=str(completed.stderr or ""),
    )


class JobStore:
    def __init__(self, project_id: str, state_dir: str = "state") -> None:
        self.project_id = project_id
        self.state_dir = Path(state_dir)
        self.project_state_dir = self.state_dir / project_id
        self.jobs_path = self.project_state_dir / "jobs.jsonl"
        self.logs_dir = self.project_state_dir / "job_logs"
        self.project_state_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)

    def create(
        self,
        *,
        action: str,
        operator: str = "web-ui",
        role: str = "",
        payload: dict[str, Any] | None = None,
        request_audit: dict[str, Any] | None = None,
        runtime_status: str = RUNTIME_STATUS_DISCONNECTED,
        summary: str = "",
        timeout_seconds: int = DEFAULT_JOB_TIMEOUT_SECONDS,
        max_attempts: int = DEFAULT_JOB_MAX_ATTEMPTS,
        queued_by: str = "ui",
    ) -> dict[str, Any]:
        now = _now_iso()
        job_id = uuid.uuid4().hex
        record = {
            "schema_version": JOB_SCHEMA_VERSION,
            "record_type": JOB_RECORD_UPDATE,
            "created_at": now,
            "updated_at": now,
            "started_at": "",
            "finished_at": "",
            "project_id": self.project_id,
            "job_id": job_id,
            "action": action,
            "status": JOB_STATUS_QUEUED,
            "runtime_status": runtime_status,
            "operator": operator,
            "role": role,
            "queued_by": queued_by,
            "summary": summary,
            "payload": _jsonable(dict(payload or {})),
            "result": {},
            "request_audit": _jsonable(dict(request_audit or {})),
            "attempt": 0,
            "max_attempts": max(1, int(max_attempts or DEFAULT_JOB_MAX_ATTEMPTS)),
            "timeout_seconds": max(1, int(timeout_seconds or DEFAULT_JOB_TIMEOUT_SECONDS)),
            "lease_owner": "",
            "lease_expires_at": "",
            "cancel_requested_at": "",
            "cancel_requested_by": "",
            "retry_of": "",
            "log_path": str(self.job_log_path(job_id)),
        }
        self._append(record)
        self.append_log(
            job_id,
            "queued",
            summary or f"{action} queued",
            {"operator": operator, "role": role, "request_audit": request_audit or {}},
        )
        return record

    def mark_running(
        self,
        job_id: str,
        *,
        runtime_status: str = RUNTIME_STATUS_CONNECTING,
        summary: str = "",
        lease_owner: str = "",
        lease_seconds: int = DEFAULT_JOB_LEASE_SECONDS,
        increment_attempt: bool = False,
    ) -> dict[str, Any]:
        latest = self.get(job_id)
        attempt = int(latest.get("attempt") or 0)
        if increment_attempt:
            attempt += 1
        record = self._update(
            job_id,
            status=JOB_STATUS_RUNNING,
            runtime_status=runtime_status,
            summary=summary,
            lease_owner=lease_owner,
            lease_expires_at=_iso_after(lease_seconds) if lease_owner else "",
            attempt=attempt,
        )
        record["started_at"] = record.get("started_at") or record["updated_at"]
        self._append(record)
        self.append_log(
            job_id,
            "running",
            summary or "job running",
            {"attempt": record.get("attempt"), "lease_owner": lease_owner},
        )
        return record

    def complete(
        self,
        job_id: str,
        *,
        status: str,
        runtime_status: str,
        summary: str = "",
        result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if status not in JOB_STATUSES:
            raise ValueError(f"unknown_job_status:{status}")
        record = self._update(
            job_id,
            status=status,
            runtime_status=runtime_status,
            summary=summary,
            result=result,
            lease_owner="",
            lease_expires_at="",
        )
        record["finished_at"] = record["updated_at"]
        self._append(record)
        self.append_log(
            job_id,
            status,
            summary or f"job {status}",
            {"result": record.get("result", {})},
        )
        return record

    def read_all(
        self,
        *,
        limit: int | None = None,
        offset: int = 0,
        reverse: bool = False,
    ) -> list[dict[str, Any]]:
        return read_jsonl(
            self.jobs_path,
            limit=limit,
            offset=offset,
            reverse=reverse,
        )

    def get(self, job_id: str) -> dict[str, Any]:
        latest = _latest_by_updated_at(
            [record for record in self.read_all() if record.get("job_id") == job_id]
        )
        if not latest:
            raise KeyError(f"job_not_found:{job_id}")
        return latest

    def jobs(
        self,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        latest: dict[str, dict[str, Any]] = {}
        for record in self.read_all():
            job_id = str(record.get("job_id", ""))
            if not job_id:
                continue
            latest[job_id] = record

        rows = sorted(latest.values(), key=_job_sort_key, reverse=True)
        if offset:
            rows = rows[max(0, int(offset)) :]
        if limit is not None:
            return rows[: max(0, int(limit))]
        return rows

    def latest_job(self, *, actions: set[str] | None = None) -> dict[str, Any]:
        rows = self.jobs()
        if actions is None:
            return rows[0] if rows else {}
        for row in rows:
            if row.get("action") in actions:
                return row
        return {}

    def request_cancel(
        self,
        job_id: str,
        *,
        operator: str = "web-ui",
        role: str = "",
        request_audit: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        latest = self.get(job_id)
        status = str(latest.get("status", ""))
        if status in JOB_TERMINAL_STATUSES:
            self.append_log(
                job_id,
                "cancel_ignored",
                "job is already terminal",
                {
                    "operator": operator,
                    "role": role,
                    "request_audit": request_audit or {},
                },
            )
            return latest

        now = _now_iso()
        if status == JOB_STATUS_QUEUED:
            record = self._update(
                job_id,
                status=JOB_STATUS_CANCELED,
                runtime_status=str(latest.get("runtime_status") or RUNTIME_STATUS_CONNECTED),
                summary="任务已取消",
                result={
                    **dict(latest.get("result") or {}),
                    "failure_reason": "canceled",
                    "output_summary": "任务在执行前被取消。",
                },
                cancel_requested_at=now,
                cancel_requested_by=operator,
                cancel_requested_role=role,
                request_audit=request_audit or latest.get("request_audit", {}),
                lease_owner="",
                lease_expires_at="",
            )
            record["finished_at"] = now
            self._append(record)
            self.append_log(
                job_id,
                "canceled",
                "job canceled before execution",
                {
                    "operator": operator,
                    "role": role,
                    "request_audit": request_audit or {},
                },
            )
            return record

        record = self._update(
            job_id,
            status=JOB_STATUS_CANCEL_REQUESTED,
            runtime_status=str(latest.get("runtime_status") or RUNTIME_STATUS_CONNECTED),
            summary="取消请求已记录",
            cancel_requested_at=now,
            cancel_requested_by=operator,
            cancel_requested_role=role,
            request_audit=request_audit or latest.get("request_audit", {}),
        )
        self._append(record)
        self.append_log(
            job_id,
            "cancel_requested",
            "cancel requested",
            {
                "operator": operator,
                "role": role,
                "request_audit": request_audit or {},
            },
        )
        return record

    def is_cancel_requested(self, job_id: str) -> bool:
        latest = self.get(job_id)
        return bool(latest.get("cancel_requested_at")) or latest.get("status") == JOB_STATUS_CANCEL_REQUESTED

    def retry(
        self,
        job_id: str,
        *,
        operator: str = "web-ui",
        role: str = "",
        request_audit: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        latest = self.get(job_id)
        status = str(latest.get("status", ""))
        if status in JOB_ACTIVE_STATUSES:
            raise ValueError(f"job_not_terminal:{job_id}")

        payload = dict(latest.get("payload") or {})
        payload["retry_of"] = job_id
        retry = self.create(
            action=str(latest.get("action", "")),
            operator=operator,
            role=role,
            payload=payload,
            request_audit=request_audit or dict(latest.get("request_audit") or {}),
            runtime_status=str(latest.get("runtime_status") or RUNTIME_STATUS_CONNECTED),
            summary=f"重试任务已排队：{latest.get('action', '')}",
            timeout_seconds=int(latest.get("timeout_seconds") or DEFAULT_JOB_TIMEOUT_SECONDS),
            max_attempts=int(latest.get("max_attempts") or DEFAULT_JOB_MAX_ATTEMPTS),
            queued_by="retry",
        )
        retry["retry_of"] = job_id
        self._append(retry)
        self.append_log(
            retry["job_id"],
            "retry",
            "retry queued",
            {
                "retry_of": job_id,
                "operator": operator,
                "role": role,
                "request_audit": request_audit or {},
            },
        )
        return retry

    def lease_next(
        self,
        *,
        worker_id: str,
        lease_seconds: int = DEFAULT_JOB_LEASE_SECONDS,
    ) -> dict[str, Any]:
        self.reap_expired_running()
        candidates = [
            job
            for job in self.jobs()
            if job.get("status") == JOB_STATUS_QUEUED
        ]
        if not candidates:
            return {}

        candidates = sorted(candidates, key=lambda item: str(item.get("created_at", "")))
        for job in candidates:
            attempt = int(job.get("attempt") or 0)
            max_attempts = int(job.get("max_attempts") or DEFAULT_JOB_MAX_ATTEMPTS)
            if attempt >= max_attempts:
                self.complete(
                    str(job.get("job_id", "")),
                    status=JOB_STATUS_FAILED,
                    runtime_status=str(job.get("runtime_status") or RUNTIME_STATUS_ERROR),
                    summary="任务超过最大重试次数",
                    result={
                        **dict(job.get("result") or {}),
                        "failure_reason": "max_attempts_exceeded",
                    },
                )
                continue
            return self.mark_running(
                str(job.get("job_id", "")),
                runtime_status=str(job.get("runtime_status") or RUNTIME_STATUS_CONNECTED),
                summary=f"worker leased by {worker_id}",
                lease_owner=worker_id,
                lease_seconds=lease_seconds,
                increment_attempt=True,
            )
        return {}

    def heartbeat(
        self,
        job_id: str,
        *,
        worker_id: str,
        lease_seconds: int = DEFAULT_JOB_LEASE_SECONDS,
    ) -> dict[str, Any]:
        latest = self.get(job_id)
        if latest.get("status") not in {JOB_STATUS_RUNNING, JOB_STATUS_CANCEL_REQUESTED}:
            return latest
        record = self._update(
            job_id,
            status=str(latest.get("status")),
            runtime_status=str(latest.get("runtime_status") or RUNTIME_STATUS_CONNECTED),
            summary=str(latest.get("summary") or ""),
            lease_owner=worker_id,
            lease_expires_at=_iso_after(lease_seconds),
        )
        self._append(record)
        return record

    def reap_expired_running(self) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc)
        updates: list[dict[str, Any]] = []
        for job in self.jobs():
            status = str(job.get("status", ""))
            if status not in {JOB_STATUS_RUNNING, JOB_STATUS_CANCEL_REQUESTED}:
                continue

            job_id = str(job.get("job_id", ""))
            timeout_seconds = int(job.get("timeout_seconds") or DEFAULT_JOB_TIMEOUT_SECONDS)
            started_at = _parse_iso(job.get("started_at"))
            if started_at and now >= started_at + timedelta(seconds=timeout_seconds):
                updates.append(
                    self.complete(
                        job_id,
                        status=JOB_STATUS_TIMED_OUT,
                        runtime_status=RUNTIME_STATUS_ERROR,
                        summary="任务执行超时",
                        result={
                            **dict(job.get("result") or {}),
                            "failure_reason": "job_timeout",
                            "output_summary": f"任务超过 timeout_seconds={timeout_seconds}。",
                        },
                    )
                )
                continue

            lease_expires_at = _parse_iso(job.get("lease_expires_at"))
            if lease_expires_at and now >= lease_expires_at:
                attempt = int(job.get("attempt") or 0)
                max_attempts = int(job.get("max_attempts") or DEFAULT_JOB_MAX_ATTEMPTS)
                if attempt < max_attempts:
                    record = self._update(
                        job_id,
                        status=JOB_STATUS_QUEUED,
                        runtime_status=str(job.get("runtime_status") or RUNTIME_STATUS_CONNECTED),
                        summary="任务租约过期，等待重试",
                        lease_owner="",
                        lease_expires_at="",
                    )
                    self._append(record)
                    self.append_log(job_id, "lease_expired", "lease expired; requeued")
                    updates.append(record)
                else:
                    updates.append(
                        self.complete(
                            job_id,
                            status=JOB_STATUS_TIMED_OUT,
                            runtime_status=RUNTIME_STATUS_ERROR,
                            summary="任务租约过期且无剩余重试",
                            result={
                                **dict(job.get("result") or {}),
                                "failure_reason": "lease_expired",
                                "output_summary": "worker 未在租约期内完成任务。",
                            },
                        )
                    )
        return updates

    def job_log_path(self, job_id: str) -> Path:
        return self.logs_dir / f"{job_id}.jsonl"

    def append_log(
        self,
        job_id: str,
        event: str,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        entry = {
            "created_at": _now_iso(),
            "project_id": self.project_id,
            "job_id": job_id,
            "event": event,
            "message": message,
            "metadata": _jsonable(dict(metadata or {})),
        }
        append_jsonl(self.job_log_path(job_id), entry)
        return entry

    def job_log(self, job_id: str, *, limit: int = 200) -> dict[str, Any]:
        self.get(job_id)
        path = self.job_log_path(job_id)
        entries = read_jsonl(path, limit=limit, reverse=True)
        entries = list(reversed(entries))
        lines = [
            f"{entry.get('created_at', '')} [{entry.get('event', '')}] {entry.get('message', '')}"
            for entry in entries
        ]
        return {
            "project_id": self.project_id,
            "job_id": job_id,
            "log_path": str(path),
            "entries": entries,
            "text": "\n".join(lines),
        }

    def _append(self, record: dict[str, Any]) -> None:
        append_jsonl(self.jobs_path, record)

    def compact(self) -> list[dict[str, Any]]:
        records = self.jobs()
        rewrite_jsonl(self.jobs_path, list(reversed(records)))
        return records

    def _update(
        self,
        job_id: str,
        *,
        status: str,
        runtime_status: str,
        summary: str,
        result: dict[str, Any] | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        latest = _latest_by_updated_at(
            [record for record in self.read_all() if record.get("job_id") == job_id]
        )
        if not latest:
            raise KeyError(f"job_not_found:{job_id}")

        record = dict(latest)
        record["updated_at"] = _now_iso()
        record["status"] = status
        record["runtime_status"] = runtime_status
        if summary:
            record["summary"] = summary
        if result is not None:
            record["result"] = _jsonable(result)
        for key, value in extra.items():
            record[key] = _jsonable(value)
        return record


class RuntimeControlService:
    def __init__(
        self,
        *,
        project_id: str,
        state_dir: str = "state",
        config_path: str = "configs/projects.yaml",
        command_runner: CommandRunner | None = None,
    ) -> None:
        self.project_id = project_id
        self.state_dir = state_dir
        self.config_path = config_path
        self.command_runner = command_runner or _default_command_runner
        self.job_store = JobStore(project_id=project_id, state_dir=state_dir)

    def runtime(self) -> dict[str, Any]:
        latest = self.job_store.latest_job(actions={"connect", "health_check"})
        process_info = read_monitor_process(
            project_id=self.project_id,
            state_dir=self.state_dir,
        )
        monitor_running = pid_is_alive(process_info.get("pid"))
        connection_status = CONNECTION_STATUS_DISCONNECTED
        runtime_status = RUNTIME_STATUS_DISCONNECTED

        if monitor_running:
            connection_status = CONNECTION_STATUS_CONNECTED
            runtime_status = RUNTIME_STATUS_MONITOR_RUNNING
        elif latest:
            status = str(latest.get("status", ""))
            if status in {JOB_STATUS_QUEUED, JOB_STATUS_RUNNING, JOB_STATUS_CANCEL_REQUESTED}:
                connection_status = CONNECTION_STATUS_CONNECTING
                runtime_status = RUNTIME_STATUS_CONNECTING
            elif status == JOB_STATUS_SUCCEEDED:
                connection_status = CONNECTION_STATUS_CONNECTED
                runtime_status = str(
                    latest.get("runtime_status") or RUNTIME_STATUS_CONNECTED
                )
            elif status in {
                JOB_STATUS_FAILED,
                JOB_STATUS_BLOCKED,
                JOB_STATUS_CANCELED,
                JOB_STATUS_TIMED_OUT,
            }:
                connection_status = CONNECTION_STATUS_ERROR
                runtime_status = RUNTIME_STATUS_ERROR

        result = latest.get("result") if isinstance(latest.get("result"), dict) else {}
        return {
            "project_id": self.project_id,
            "connection_status": connection_status,
            "runtime_status": runtime_status,
            "supported_statuses": sorted(RUNTIME_STATUSES),
            "connection_mode": str(result.get("connection_mode", "")),
            "target": str(result.get("target", "")),
            "checks": list(result.get("checks") or []),
            "last_job": latest,
            "monitor_process": process_info,
            "jobs_path": str(self.job_store.jobs_path),
        }

    def jobs(self, *, limit: int = 20, offset: int = 0) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "jobs_path": str(self.job_store.jobs_path),
            "jobs": self.job_store.jobs(limit=limit, offset=offset),
        }

    def connect(
        self,
        *,
        connection_mode: str = "",
        operator: str = "web-ui",
    ) -> dict[str, Any]:
        return self._run_connection_job(
            action="connect",
            connection_mode=connection_mode,
            operator=operator,
        )

    def health_check(
        self,
        *,
        connection_mode: str = "",
        operator: str = "web-ui",
    ) -> dict[str, Any]:
        return self._run_connection_job(
            action="health_check",
            connection_mode=connection_mode,
            operator=operator,
        )

    def enqueue_connection(
        self,
        *,
        action: str,
        connection_mode: str = "",
        operator: str = "web-ui",
        role: str = "",
        request_audit: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if action not in {"connect", "health_check"}:
            raise ValueError(f"unsupported_connection_action:{action}")
        mode = self._connection_mode(connection_mode)
        job = self.job_store.create(
            action=action,
            operator=operator,
            role=role,
            payload={"connection_mode": mode},
            request_audit=request_audit,
            runtime_status=RUNTIME_STATUS_CONNECTING,
            summary=f"{action}:{mode} queued",
            timeout_seconds=max(SSH_TIMEOUT_SECONDS * 4, 45),
        )
        return {"job": job, "runtime": self.runtime()}

    def execute_connection_job(self, job_id: str) -> dict[str, Any]:
        job = self.job_store.get(job_id)
        action = str(job.get("action", ""))
        if action not in {"connect", "health_check"}:
            completed = self.job_store.complete(
                job_id,
                status=JOB_STATUS_BLOCKED,
                runtime_status=self.runtime()["runtime_status"],
                summary=f"不支持的连接任务：{action}",
                result={"failure_reason": "unsupported_connection_action"},
            )
            return {"job": completed, "runtime": self.runtime()}

        if self.job_store.is_cancel_requested(job_id):
            completed = self.job_store.complete(
                job_id,
                status=JOB_STATUS_CANCELED,
                runtime_status=RUNTIME_STATUS_CONNECTED,
                summary="连接任务已取消",
                result={
                    "failure_reason": "canceled",
                    "output_summary": "任务在执行连接检查前被取消。",
                },
            )
            return {"job": completed, "runtime": self.runtime()}

        if job.get("status") != JOB_STATUS_RUNNING:
            self.job_store.mark_running(
                job_id,
                runtime_status=RUNTIME_STATUS_CONNECTING,
                summary=f"{action} running",
            )

        mode = self._connection_mode(str((job.get("payload") or {}).get("connection_mode", "")))
        return self._run_connection_job(
            action=action,
            connection_mode=mode,
            operator=str(job.get("operator") or "web-ui"),
            job_id=job_id,
        )

    def record_ui_action_job(
        self,
        *,
        action: str,
        operator: str,
        role: str = "",
        payload: dict[str, Any] | None = None,
        request_audit: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.job_store.create(
            action=action,
            operator=operator,
            role=role,
            payload=payload,
            request_audit=request_audit,
            runtime_status=self.runtime()["runtime_status"],
        )

    def mark_ui_action_running(self, job_id: str, *, summary: str = "") -> dict[str, Any]:
        return self.job_store.mark_running(
            job_id,
            runtime_status=self.runtime()["runtime_status"],
            summary=summary,
        )

    def complete_ui_action_job(
        self,
        job_id: str,
        *,
        status: str,
        summary: str,
        result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.job_store.complete(
            job_id,
            status=status,
            runtime_status=self.runtime()["runtime_status"],
            summary=summary,
            result=result,
        )

    @property
    def project(self) -> ProjectConfig:
        return ProjectRegistry(self.config_path).get(self.project_id)

    def _run_connection_job(
        self,
        *,
        action: str,
        connection_mode: str,
        operator: str,
        job_id: str | None = None,
    ) -> dict[str, Any]:
        mode = self._connection_mode(connection_mode)
        if job_id:
            job = self.job_store.get(job_id)
        else:
            job = self.job_store.create(
                action=action,
                operator=operator,
                payload={"connection_mode": mode},
                runtime_status=RUNTIME_STATUS_CONNECTING,
                summary=f"{action}:{mode}",
                timeout_seconds=max(SSH_TIMEOUT_SECONDS * 4, 45),
            )
            self.job_store.mark_running(
                job["job_id"],
                runtime_status=RUNTIME_STATUS_CONNECTING,
                summary=f"{action} running for {mode}",
            )

        try:
            result = self._connection_checks(mode)
            ok = bool(result["ok"])
            completed = self.job_store.complete(
                job["job_id"],
                status=JOB_STATUS_SUCCEEDED if ok else JOB_STATUS_FAILED,
                runtime_status=RUNTIME_STATUS_CONNECTED if ok else RUNTIME_STATUS_ERROR,
                summary=result["summary"],
                result=result,
            )
            return {"job": completed, "runtime": self.runtime()}
        except Exception as exc:
            result = {
                "ok": False,
                "connection_mode": mode,
                "target": "",
                "summary": f"{type(exc).__name__}: {exc}",
                "checks": [
                    _check(
                        "runtime_control",
                        CHECK_ERROR,
                        f"{type(exc).__name__}: {exc}",
                    )
                ],
            }
            completed = self.job_store.complete(
                job["job_id"],
                status=JOB_STATUS_FAILED,
                runtime_status=RUNTIME_STATUS_ERROR,
                summary=result["summary"],
                result=result,
            )
            return {"job": completed, "runtime": self.runtime()}

    def _connection_mode(self, requested: str) -> str:
        requested = requested.strip().lower()
        if requested in {"local", "remote"}:
            return requested
        return "remote" if self.project.is_remote else "local"

    def _connection_checks(self, mode: str) -> dict[str, Any]:
        project = self.project
        run_command = _parse_run_command(project.run_command)
        checks: list[dict[str, Any]] = []

        if mode == "remote":
            target = _remote_target(project)
            checks.extend(self._remote_checks(project, run_command))
        else:
            target = project.effective_project_dir or project.project_dir
            checks.extend(_local_checks(project, run_command))

        ok = all(item["status"] in {CHECK_OK, CHECK_SKIPPED} for item in checks)
        failed = [item for item in checks if item["status"] == CHECK_ERROR]
        warnings = [item for item in checks if item["status"] == CHECK_WARNING]

        if ok:
            summary = f"{mode} connection checks passed"
        elif failed:
            summary = f"{mode} connection checks failed: {len(failed)} error(s)"
        else:
            summary = f"{mode} connection checks completed with warnings"

        return {
            "ok": ok,
            "connection_mode": mode,
            "target": target,
            "summary": summary,
            "checks": checks,
            "warning_count": len(warnings),
            "error_count": len(failed),
        }

    def _remote_checks(
        self,
        project: ProjectConfig,
        run_command: dict[str, Any],
    ) -> list[dict[str, Any]]:
        checks: list[dict[str, Any]] = []
        ssh_ok = self._check_ssh(project)
        checks.append(ssh_ok)

        project_dir = project.remote_project_dir.strip()
        config_path = _remote_path(project_dir, str(run_command["config_path"]))

        if not ssh_ok["status"] == CHECK_OK:
            checks.append(_check("project_dir", CHECK_SKIPPED, "ssh is not available"))
            checks.append(_check("log_files", CHECK_SKIPPED, "ssh is not available"))
            checks.append(_check("config_file", CHECK_SKIPPED, "ssh is not available"))
            checks.append(_run_command_check(run_command))
            return checks

        if not project_dir:
            checks.append(_check("project_dir", CHECK_ERROR, "remote_project_dir is missing"))
        else:
            checks.append(
                self._remote_test(
                    project,
                    name="project_dir",
                    path=project_dir,
                    test_flag="-d",
                    ok_message=f"remote project directory exists: {project_dir}",
                    error_message=f"remote project directory missing: {project_dir}",
                )
            )

        log_paths = [
            _remote_path(project_dir, str(path))
            for path in project.log_files
        ]
        checks.append(
            self._remote_many_files_check(
                project,
                name="log_files",
                paths=log_paths,
                test_flag="-f",
                empty_message="no log files configured",
            )
        )
        checks.append(
            self._remote_test(
                project,
                name="config_file",
                path=config_path,
                test_flag="-r",
                ok_message=f"remote config file is readable: {config_path}",
                error_message=f"remote config file is not readable: {config_path}",
            )
        )
        checks.append(_run_command_check(run_command))
        return checks

    def _check_ssh(self, project: ProjectConfig) -> dict[str, Any]:
        if not project.ssh.user or not project.ssh.host:
            return _check("ssh_reachable", CHECK_ERROR, "ssh user/host is not configured")

        args = _ssh_args(project) + [f"echo {SSH_OK_TOKEN}"]
        try:
            result = self.command_runner(args, SSH_TIMEOUT_SECONDS)
        except FileNotFoundError:
            return _check("ssh_reachable", CHECK_ERROR, "ssh command not found")
        except subprocess.TimeoutExpired:
            return _check("ssh_reachable", CHECK_ERROR, "ssh connection timed out")

        if result.returncode == 0 and SSH_OK_TOKEN in result.stdout:
            return _check("ssh_reachable", CHECK_OK, f"ssh reachable: {_remote_target(project)}")
        return _check(
            "ssh_reachable",
            CHECK_ERROR,
            f"ssh failed: return_code={result.returncode}; {result.stderr.strip()}",
        )

    def _remote_test(
        self,
        project: ProjectConfig,
        *,
        name: str,
        path: str,
        test_flag: str,
        ok_message: str,
        error_message: str,
    ) -> dict[str, Any]:
        if not path:
            return _check(name, CHECK_ERROR, f"{name} path is missing")

        command = f"test {test_flag} {shlex.quote(path)}"
        try:
            result = self.command_runner(_ssh_args(project) + [command], SSH_TIMEOUT_SECONDS)
        except FileNotFoundError:
            return _check(name, CHECK_ERROR, "ssh command not found")
        except subprocess.TimeoutExpired:
            return _check(name, CHECK_ERROR, f"remote check timed out: {path}")

        if result.returncode == 0:
            return _check(name, CHECK_OK, ok_message, target=path)
        return _check(name, CHECK_ERROR, error_message, target=path)

    def _remote_many_files_check(
        self,
        project: ProjectConfig,
        *,
        name: str,
        paths: list[str],
        test_flag: str,
        empty_message: str,
    ) -> dict[str, Any]:
        if not paths:
            return _check(name, CHECK_WARNING, empty_message)

        missing: list[str] = []
        for path in paths:
            item = self._remote_test(
                project,
                name=name,
                path=path,
                test_flag=test_flag,
                ok_message=f"remote file exists: {path}",
                error_message=f"remote file missing: {path}",
            )
            if item["status"] != CHECK_OK:
                missing.append(path)

        if missing:
            return _check(
                name,
                CHECK_ERROR,
                "missing remote log files: " + ", ".join(missing),
                target=", ".join(paths),
            )
        return _check(name, CHECK_OK, "remote log files exist", target=", ".join(paths))


def _local_checks(
    project: ProjectConfig,
    run_command: dict[str, Any],
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = [
        _check("ssh_reachable", CHECK_SKIPPED, "local connection does not require ssh"),
    ]
    project_dir_text = project.project_dir or project.effective_project_dir
    project_dir = Path(project_dir_text).expanduser() if project_dir_text else Path("")

    if not project_dir_text:
        checks.append(_check("project_dir", CHECK_ERROR, "project_dir is missing"))
    elif project_dir.is_dir():
        checks.append(
            _check(
                "project_dir",
                CHECK_OK,
                f"local project directory exists: {project_dir}",
                target=str(project_dir),
            )
        )
    else:
        checks.append(
            _check(
                "project_dir",
                CHECK_ERROR,
                f"local project directory missing: {project_dir}",
                target=str(project_dir),
            )
        )

    checks.append(_local_log_files_check(project=project, project_dir=project_dir))
    checks.append(
        _local_config_file_check(
            project_dir=project_dir,
            config_path=str(run_command["config_path"]),
        )
    )
    checks.append(_run_command_check(run_command))
    return checks


def _local_log_files_check(
    *,
    project: ProjectConfig,
    project_dir: Path,
) -> dict[str, Any]:
    if not project.log_files:
        return _check("log_files", CHECK_WARNING, "no log files configured")

    missing: list[str] = []
    resolved: list[str] = []
    for log_file in project.log_files:
        path = Path(log_file).expanduser()
        if not path.is_absolute():
            path = project_dir / path
        resolved.append(str(path))
        if not path.is_file():
            missing.append(str(path))

    if missing:
        return _check(
            "log_files",
            CHECK_ERROR,
            "missing local log files: " + ", ".join(missing),
            target=", ".join(resolved),
        )
    return _check("log_files", CHECK_OK, "local log files exist", target=", ".join(resolved))


def _local_config_file_check(
    *,
    project_dir: Path,
    config_path: str,
) -> dict[str, Any]:
    path = Path(config_path).expanduser()
    if not path.is_absolute():
        path = project_dir / path

    if path.is_file() and os.access(path, os.R_OK):
        return _check(
            "config_file",
            CHECK_OK,
            f"local config file is readable: {path}",
            target=str(path),
        )
    return _check(
        "config_file",
        CHECK_ERROR,
        f"local config file is not readable: {path}",
        target=str(path),
    )


def _parse_run_command(command: str) -> dict[str, Any]:
    command = command.strip()
    config_path = DEFAULT_CONFIG_PATH

    if not command:
        return {
            "ok": False,
            "argv": [],
            "config_path": config_path,
            "message": "run_command is missing",
        }

    try:
        argv = shlex.split(command)
    except ValueError as exc:
        return {
            "ok": False,
            "argv": [],
            "config_path": config_path,
            "message": f"run_command is not parseable: {exc}",
        }

    forbidden = {";", "&&", "||", "|", ">", "<"}
    if not argv:
        return {
            "ok": False,
            "argv": [],
            "config_path": config_path,
            "message": "run_command is empty",
        }
    if any(token in forbidden for token in argv):
        return {
            "ok": False,
            "argv": argv,
            "config_path": config_path,
            "message": "run_command contains shell control operators",
        }

    allowed, reason = _run_command_allowed(argv)
    if not allowed:
        return {
            "ok": False,
            "argv": argv,
            "config_path": config_path,
            "message": reason,
        }

    for index, token in enumerate(argv):
        if token == "--config" and index + 1 < len(argv):
            config_path = argv[index + 1]
            break
        if token.startswith("--config="):
            config_path = token.split("=", 1)[1]
            break

    return {
        "ok": True,
        "argv": argv,
        "config_path": config_path or DEFAULT_CONFIG_PATH,
        "message": "run_command is parseable",
    }


def _run_command_allowed(argv: list[str]) -> tuple[bool, str]:
    executable = Path(str(argv[0])).name
    if executable not in {"python", "python3"} and not executable.startswith("python3."):
        return False, "run_command executable is not allowlisted"

    if len(argv) >= 3 and argv[1] == "-m":
        module = str(argv[2])
        if module and all(part.replace("_", "").isalnum() for part in module.split(".")):
            return True, "run_command matches python module allowlist"
        return False, "run_command python module is not allowlisted"

    if len(argv) >= 2 and str(argv[1]).endswith(".py"):
        return True, "run_command matches python script allowlist"

    return False, "run_command must run a python script or module"


def _run_command_check(run_command: dict[str, Any]) -> dict[str, Any]:
    return _check(
        "run_command",
        CHECK_OK if run_command.get("ok") else CHECK_ERROR,
        str(run_command.get("message", "")),
        target=" ".join(str(item) for item in run_command.get("argv", [])),
        details={
            "argv": run_command.get("argv", []),
            "config_path": run_command.get("config_path", DEFAULT_CONFIG_PATH),
        },
    )


def _remote_path(project_dir: str, path: str) -> str:
    if not path:
        return path
    if path.startswith("/"):
        return path
    return posixpath.join(project_dir, path) if project_dir else path


def _remote_target(project: ProjectConfig) -> str:
    if not project.ssh.user or not project.ssh.host:
        return ""
    return f"{project.ssh.user}@{project.ssh.host}:{project.ssh.port}"


def _ssh_args(project: ProjectConfig) -> list[str]:
    args = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=5",
        "-p",
        str(project.ssh.port),
    ]
    key_path = project.ssh.resolved_key_path()
    if key_path:
        args.extend(["-i", key_path])
    args.append(f"{project.ssh.user}@{project.ssh.host}")
    return args


def _check(
    name: str,
    status: str,
    message: str,
    *,
    target: str = "",
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "status": status,
        "message": message,
        "target": target,
        "details": _jsonable(dict(details or {})),
    }


def monitor_process_path(*, project_id: str, state_dir: str = "state") -> Path:
    return Path(state_dir) / project_id / MONITOR_PROCESS_FILE


def read_monitor_process(
    *,
    project_id: str,
    state_dir: str = "state",
) -> dict[str, Any]:
    path = monitor_process_path(project_id=project_id, state_dir=state_dir)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def write_monitor_process(
    *,
    project_id: str,
    state_dir: str = "state",
    process_info: dict[str, Any],
) -> dict[str, Any]:
    path = monitor_process_path(project_id=project_id, state_dir=state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _jsonable(dict(process_info))
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return data


def pid_is_alive(pid: Any) -> bool:
    try:
        pid_int = int(pid)
    except (TypeError, ValueError):
        return False
    if pid_int <= 0:
        return False
    try:
        os.kill(pid_int, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
