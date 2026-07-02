from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sys
import tempfile
import threading
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from web_ui.job_worker import AsyncJobWorker
from web_ui.job_worker import StreamingCommandRunner
from web_ui.operation_runner import OP_GENERATE_REPORT, OperationRunner
from web_ui.runtime_control import (
    JOB_STATUS_CANCELED,
    JOB_STATUS_FAILED,
    JOB_STATUS_QUEUED,
    JOB_STATUS_SUCCEEDED,
    JOB_STATUS_TIMED_OUT,
    CommandResult,
    JobStore,
    RuntimeControlService,
)


def write_config(
    path: Path,
    *,
    project_id: str,
    project_dir: str,
    log_files: list[str] | None = None,
) -> None:
    logs = "\n".join(f"      - {item}" for item in (log_files or []))
    path.write_text(
        f"""
projects:
  - project_id: {project_id}
    name: Async Worker Test Project
    mode: local
    owner: tests
    project_dir: {project_dir}
    run_command: python app.py --config config.json
    log_files:
{logs if logs else "      []"}
    policy:
      auto_recover: true
      auto_recovery_policy_enabled: true
      auto_recovery_dry_run: true
      rollback_on_failure: true
      allow_auto_apply:
        - fix-network-1
      escalation_required: []
""",
        encoding="utf-8",
    )


def test_async_worker_leases_and_runs_connection_job() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        project_dir = root / "project"
        project_dir.mkdir()
        (project_dir / "config.json").write_text("{}", encoding="utf-8")
        (project_dir / "service.log").write_text("ready", encoding="utf-8")
        config_path = root / "projects.yaml"
        state_dir = str(root / "state")
        write_config(
            config_path,
            project_id="worker_local",
            project_dir=str(project_dir),
            log_files=["service.log"],
        )
        service = RuntimeControlService(
            project_id="worker_local",
            state_dir=state_dir,
            config_path=str(config_path),
        )
        queued = service.enqueue_connection(
            action="connect",
            connection_mode="local",
            operator="tester",
        )["job"]

        result = AsyncJobWorker(
            project_id="worker_local",
            state_dir=state_dir,
            config_path=str(config_path),
            worker_id="worker-test",
        ).run_once()

        completed = result["job"]
        assert completed["job_id"] == queued["job_id"]
        assert completed["status"] == JOB_STATUS_SUCCEEDED
        assert completed["attempt"] == 1
        assert completed["lease_owner"] == ""
        log = JobStore("worker_local", state_dir=state_dir).job_log(queued["job_id"])
        assert "worker_lease_acquired" in log["text"]
        assert "worker_result" in log["text"]


def test_async_worker_canceled_queued_job_does_not_execute() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config_path = root / "projects.yaml"
        state_dir = str(root / "state")
        write_config(config_path, project_id="worker_local", project_dir=str(root))
        calls: list[list[str]] = []

        def fake_runner(args: list[str], timeout: int) -> CommandResult:
            calls.append(args)
            return CommandResult(returncode=0, stdout="should not run")

        runner = OperationRunner(
            project_id="worker_local",
            state_dir=state_dir,
            config_path=str(config_path),
            command_runner=fake_runner,
        )
        queued = runner.enqueue(OP_GENERATE_REPORT, operator="tester")["job"]
        runner.job_store.request_cancel(queued["job_id"], operator="tester")

        result = AsyncJobWorker(
            project_id="worker_local",
            state_dir=state_dir,
            config_path=str(config_path),
            command_runner=fake_runner,
        ).run_once()

        assert result["ran"] is False
        latest = runner.job_store.get(queued["job_id"])
        assert latest["status"] == JOB_STATUS_CANCELED
        assert calls == []


def test_job_retry_creates_new_queued_job_and_worker_runs_it() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config_path = root / "projects.yaml"
        state_dir = str(root / "state")
        write_config(config_path, project_id="worker_local", project_dir=str(root))
        calls: list[list[str]] = []

        def fake_runner(args: list[str], timeout: int) -> CommandResult:
            calls.append(args)
            return CommandResult(returncode=0, stdout="- outputs/monitors/worker_local/report.md\n")

        store = JobStore("worker_local", state_dir=state_dir)
        failed = store.create(action=OP_GENERATE_REPORT, operator="tester")
        store.complete(
            failed["job_id"],
            status=JOB_STATUS_FAILED,
            runtime_status="connected",
            summary="failed once",
            result={"failure_reason": "test_failure"},
        )

        retry = store.retry(failed["job_id"], operator="tester")
        assert retry["status"] == JOB_STATUS_QUEUED
        assert retry["payload"]["retry_of"] == failed["job_id"]

        result = AsyncJobWorker(
            project_id="worker_local",
            state_dir=state_dir,
            config_path=str(config_path),
            command_runner=fake_runner,
        ).run_once()

        assert result["job"]["job_id"] == retry["job_id"]
        assert result["job"]["status"] == JOB_STATUS_SUCCEEDED
        assert calls


def test_job_store_marks_running_job_timed_out() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state_dir = str(Path(tmp) / "state")
        store = JobStore("worker_local", state_dir=state_dir)
        queued = store.create(
            action=OP_GENERATE_REPORT,
            operator="tester",
            timeout_seconds=1,
        )
        running = store.mark_running(
            queued["job_id"],
            runtime_status="service_running",
            summary="running",
        )
        old = datetime.now(timezone.utc) - timedelta(seconds=5)
        running = dict(running)
        running["updated_at"] = old.isoformat()
        running["started_at"] = old.isoformat()
        store._append(running)

        updates = store.reap_expired_running()

        assert updates[-1]["status"] == JOB_STATUS_TIMED_OUT
        assert store.get(queued["job_id"])["status"] == JOB_STATUS_TIMED_OUT


def test_streaming_command_runner_writes_stdout_to_job_log() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state_dir = str(Path(tmp) / "state")
        store = JobStore("worker_local", state_dir=state_dir)
        job = store.create(action=OP_GENERATE_REPORT, operator="tester")
        store.mark_running(job["job_id"], runtime_status="service_running", summary="running")

        result = StreamingCommandRunner(
            job_store=store,
            job_id=job["job_id"],
        )(
            [
                sys.executable,
                "-c",
                "print('line-one')",
            ],
            5,
        )

        assert result.returncode == 0
        assert "line-one" in result.stdout
        log = store.job_log(job["job_id"])
        assert "subprocess_started" in log["text"]
        assert "line-one" in log["text"]
        assert "subprocess_finished" in log["text"]


def test_streaming_command_runner_terminates_on_cancel_request() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state_dir = str(Path(tmp) / "state")
        store = JobStore("worker_local", state_dir=state_dir)
        job = store.create(action=OP_GENERATE_REPORT, operator="tester")
        store.mark_running(job["job_id"], runtime_status="service_running", summary="running")
        results: list[CommandResult] = []

        def run_command() -> None:
            results.append(
                StreamingCommandRunner(
                    job_store=store,
                    job_id=job["job_id"],
                    cancel_grace_seconds=0.5,
                )(
                    [
                        sys.executable,
                        "-c",
                        (
                            "import time\n"
                            "print('started', flush=True)\n"
                            "time.sleep(10)\n"
                        ),
                    ],
                    20,
                )
            )

        thread = threading.Thread(target=run_command)
        thread.start()
        deadline = time.time() + 5
        while "started" not in store.job_log(job["job_id"])["text"] and time.time() < deadline:
            time.sleep(0.05)
        store.request_cancel(job["job_id"], operator="tester")
        thread.join(timeout=5)

        assert not thread.is_alive()
        assert results
        assert results[0].returncode != 0
        assert "process canceled by operator" in results[0].stderr
        assert "subprocess_cancel_signal" in store.job_log(job["job_id"])["text"]
