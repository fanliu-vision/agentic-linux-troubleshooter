from __future__ import annotations

import subprocess
import threading
import time
import uuid
from typing import Any

from monitors.project_registry import ProjectRegistry
from web_ui.operation_runner import OperationRunner, PopenFactory
from web_ui.runtime_control import (
    CommandResult,
    JOB_STATUS_BLOCKED,
    JOB_STATUS_CANCELED,
    JOB_STATUS_FAILED,
    JOB_STATUS_RUNNING,
    JOB_STATUS_SUCCEEDED,
    JOB_STATUS_TIMED_OUT,
    RUNTIME_STATUS_ERROR,
    CommandRunner,
    JobStore,
    RuntimeControlService,
)


CONNECTION_JOB_ACTIONS = {"connect", "health_check"}
TERMINAL_JOB_STATUSES = {
    JOB_STATUS_SUCCEEDED,
    JOB_STATUS_FAILED,
    JOB_STATUS_BLOCKED,
    JOB_STATUS_CANCELED,
    JOB_STATUS_TIMED_OUT,
}


class AsyncJobWorker:
    def __init__(
        self,
        *,
        project_id: str,
        state_dir: str = "state",
        config_path: str = "configs/projects.yaml",
        output_root: str = "outputs/monitors",
        worker_id: str = "",
        lease_seconds: int = 60,
        command_runner: CommandRunner | None = None,
        popen_factory: PopenFactory | None = None,
    ) -> None:
        self.project_id = project_id
        self.state_dir = state_dir
        self.config_path = config_path
        self.output_root = output_root
        self.worker_id = worker_id or f"worker-{uuid.uuid4().hex[:12]}"
        self.lease_seconds = lease_seconds
        self.command_runner = command_runner
        self.popen_factory = popen_factory
        self.job_store = JobStore(project_id=project_id, state_dir=state_dir)

    def run_once(self) -> dict[str, Any]:
        job = self.job_store.lease_next(
            worker_id=self.worker_id,
            lease_seconds=self.lease_seconds,
        )
        if not job:
            return {"ran": False, "project_id": self.project_id, "job": {}}

        job_id = str(job.get("job_id", ""))
        action = str(job.get("action", ""))
        self.job_store.append_log(
            job_id,
            "worker_lease_acquired",
            f"{self.worker_id} leased {action}",
            {"worker_id": self.worker_id, "attempt": job.get("attempt")},
        )

        try:
            if self.job_store.is_cancel_requested(job_id):
                completed = self.job_store.complete(
                    job_id,
                    status=JOB_STATUS_CANCELED,
                    runtime_status=str(job.get("runtime_status") or ""),
                    summary="任务已取消",
                    result={
                        "failure_reason": "canceled",
                        "output_summary": "worker 获取任务后发现取消请求，未执行。",
                    },
                )
            elif action in CONNECTION_JOB_ACTIONS:
                completed = self._runtime_service(job_id).execute_connection_job(job_id)["job"]
            else:
                completed = self._operation_runner(job_id).execute_leased_job(job_id)

            if (
                self.job_store.is_cancel_requested(job_id)
                and completed.get("status") in {JOB_STATUS_FAILED, JOB_STATUS_TIMED_OUT}
            ):
                completed = self.job_store.complete(
                    job_id,
                    status=JOB_STATUS_CANCELED,
                    runtime_status=str(completed.get("runtime_status") or ""),
                    summary="任务运行中已强制取消",
                    result={
                        "failure_reason": "canceled",
                        "output_summary": "任务运行过程中收到取消请求，worker 已终止底层进程。",
                        "previous_result": completed.get("result") or {},
                    },
                )

            self._log_result(completed)
            return {"ran": True, "project_id": self.project_id, "job": completed}
        except Exception as exc:
            completed = self.job_store.complete(
                job_id,
                status=JOB_STATUS_FAILED,
                runtime_status=RUNTIME_STATUS_ERROR,
                summary=f"{type(exc).__name__}: {exc}",
                result={
                    "failure_reason": type(exc).__name__,
                    "output_summary": str(exc),
                },
            )
            self._log_result(completed)
            return {"ran": True, "project_id": self.project_id, "job": completed}

    def _runtime_service(self, job_id: str) -> RuntimeControlService:
        return RuntimeControlService(
            project_id=self.project_id,
            state_dir=self.state_dir,
            config_path=self.config_path,
            command_runner=self._command_runner(job_id),
        )

    def _operation_runner(self, job_id: str) -> OperationRunner:
        return OperationRunner(
            project_id=self.project_id,
            state_dir=self.state_dir,
            config_path=self.config_path,
            output_root=self.output_root,
            command_runner=self._command_runner(job_id),
            popen_factory=self.popen_factory,
        )

    def _command_runner(self, job_id: str) -> CommandRunner:
        if self.command_runner is not None:
            return self.command_runner
        return StreamingCommandRunner(
            job_store=self.job_store,
            job_id=job_id,
        )

    def _log_result(self, job: dict[str, Any]) -> None:
        job_id = str(job.get("job_id", ""))
        result = job.get("result") if isinstance(job.get("result"), dict) else {}
        status = str(job.get("status", ""))
        self.job_store.append_log(
            job_id,
            "worker_result",
            f"{status}: {job.get('summary', '')}",
            {
                "worker_id": self.worker_id,
                "failure_reason": result.get("failure_reason", ""),
                "output_summary": result.get("output_summary", ""),
                "return_code": result.get("return_code", ""),
            },
        )


class StreamingCommandRunner:
    def __init__(
        self,
        *,
        job_store: JobStore,
        job_id: str,
        cancel_grace_seconds: float = 3.0,
    ) -> None:
        self.job_store = job_store
        self.job_id = job_id
        self.cancel_grace_seconds = max(0.5, float(cancel_grace_seconds))

    def __call__(self, args: list[str], timeout: int) -> CommandResult:
        stdout_parts: list[str] = []
        stderr_parts: list[str] = []
        process = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        started = time.monotonic()
        self.job_store.append_log(
            self.job_id,
            "subprocess_started",
            " ".join(str(item) for item in args),
            {"pid": process.pid, "timeout_seconds": timeout},
        )

        readers = [
            threading.Thread(
                target=self._read_stream,
                args=(process.stdout, "stdout", stdout_parts),
                daemon=True,
            ),
            threading.Thread(
                target=self._read_stream,
                args=(process.stderr, "stderr", stderr_parts),
                daemon=True,
            ),
        ]
        for reader in readers:
            reader.start()

        canceled = False
        while process.poll() is None:
            if self.job_store.is_cancel_requested(self.job_id):
                canceled = True
                self.job_store.append_log(
                    self.job_id,
                    "subprocess_cancel_signal",
                    "cancel requested; terminating subprocess",
                    {"pid": process.pid},
                )
                process.terminate()
                try:
                    process.wait(timeout=self.cancel_grace_seconds)
                except subprocess.TimeoutExpired:
                    self.job_store.append_log(
                        self.job_id,
                        "subprocess_kill_signal",
                        "subprocess did not exit after terminate; killing",
                        {"pid": process.pid},
                    )
                    process.kill()
                    process.wait(timeout=self.cancel_grace_seconds)
                break

            if time.monotonic() - started >= max(1, int(timeout)):
                self.job_store.append_log(
                    self.job_id,
                    "subprocess_timeout",
                    "subprocess timeout reached; terminating",
                    {"pid": process.pid, "timeout_seconds": timeout},
                )
                process.terminate()
                try:
                    process.wait(timeout=self.cancel_grace_seconds)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=self.cancel_grace_seconds)
                for reader in readers:
                    reader.join(timeout=1.0)
                stdout = "".join(stdout_parts)
                stderr = "".join(stderr_parts)
                raise subprocess.TimeoutExpired(
                    args,
                    timeout,
                    output=stdout,
                    stderr=stderr,
                )
            time.sleep(0.15)

        for reader in readers:
            reader.join(timeout=1.0)

        stdout = "".join(stdout_parts)
        stderr = "".join(stderr_parts)
        returncode = int(process.returncode or 0)
        if canceled:
            if returncode == 0:
                returncode = -15
            stderr = (stderr + "\nprocess canceled by operator\n").lstrip()

        self.job_store.append_log(
            self.job_id,
            "subprocess_finished",
            f"return_code={returncode}",
            {
                "pid": process.pid,
                "return_code": returncode,
                "canceled": canceled,
            },
        )
        return CommandResult(
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
        )

    def _read_stream(
        self,
        stream: Any,
        event: str,
        target: list[str],
    ) -> None:
        if stream is None:
            return
        for line in iter(stream.readline, ""):
            target.append(line)
            self.job_store.append_log(
                self.job_id,
                event,
                line.rstrip("\n"),
            )
        stream.close()


class JobWorkerDaemon:
    def __init__(
        self,
        *,
        state_dir: str = "state",
        config_path: str = "configs/projects.yaml",
        output_root: str = "outputs/monitors",
        worker_id: str = "",
        poll_interval_seconds: float = 1.5,
        lease_seconds: int = 60,
    ) -> None:
        self.state_dir = state_dir
        self.config_path = config_path
        self.output_root = output_root
        self.worker_id = worker_id or f"daemon-{uuid.uuid4().hex[:12]}"
        self.poll_interval_seconds = max(0.25, float(poll_interval_seconds))
        self.lease_seconds = lease_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> "JobWorkerDaemon":
        if self._thread and self._thread.is_alive():
            return self
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name=f"agentic-job-worker-{self.worker_id}",
            daemon=True,
        )
        self._thread.start()
        return self

    def stop(self, *, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)

    def run_once(self) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for project_id in self._project_ids():
            worker = AsyncJobWorker(
                project_id=project_id,
                state_dir=self.state_dir,
                config_path=self.config_path,
                output_root=self.output_root,
                worker_id=self.worker_id,
                lease_seconds=self.lease_seconds,
            )
            result = worker.run_once()
            if result.get("ran"):
                results.append(result)
        return results

    def status(self) -> dict[str, Any]:
        return {
            "worker_id": self.worker_id,
            "running": bool(self._thread and self._thread.is_alive()),
            "poll_interval_seconds": self.poll_interval_seconds,
            "lease_seconds": self.lease_seconds,
        }

    def _loop(self) -> None:
        while not self._stop.is_set():
            results = self.run_once()
            if results:
                continue
            self._stop.wait(self.poll_interval_seconds)

    def _project_ids(self) -> list[str]:
        try:
            return [
                project.project_id
                for project in ProjectRegistry(self.config_path).load_all()
                if project.project_id
            ]
        except Exception:
            return []
