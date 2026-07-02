from __future__ import annotations

import json
import signal
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from fixers.apply_executor import SafeApplyExecutor
from monitors.trace_store import TraceStore
import web_ui.operation_runner as operation_runner_module
import web_ui.runtime_control as runtime_control_module
from web_ui.operation_runner import (
    OP_GENERATE_REPORT,
    OP_LIVE_APPLY,
    OP_REFRESH_LOGS,
    OP_ROLLBACK_LATEST,
    OP_START_MONITOR,
    OP_STOP_MONITOR,
    BackgroundProcess,
    OperationRunner,
)
from web_ui.runtime_control import (
    JOB_STATUS_BLOCKED,
    JOB_STATUS_SUCCEEDED,
    RUNTIME_STATUS_MONITOR_RUNNING,
    CommandResult,
)


def write_config(
    path: Path,
    *,
    project_id: str,
    mode: str,
    project_dir: str = "",
    remote_project_dir: str = "",
    log_files: list[str] | None = None,
    run_command: str = "python app.py --config config.json",
) -> None:
    logs = "\n".join(f"      - {item}" for item in (log_files or []))
    path.write_text(
        f"""
projects:
  - project_id: {project_id}
    name: Operation UI Test Project
    mode: {mode}
    owner: tests
    project_dir: {project_dir}
    remote_project_dir: {remote_project_dir}
    run_command: {run_command}
    log_files:
{logs if logs else "      []"}
    ssh:
      user: lf
      host: localhost
      port: 22
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


def test_operation_generate_report_runs_controlled_python_entry() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config_path = root / "projects.yaml"
        state_dir = str(root / "state")
        write_config(
            config_path,
            project_id="op_local",
            mode="local",
            project_dir=str(root),
        )
        calls: list[list[str]] = []

        def fake_runner(args: list[str], timeout: int) -> CommandResult:
            calls.append(args)
            return CommandResult(
                returncode=0,
                stdout=(
                    "Monitor finished\n"
                    "reports_generated:\n"
                    "- outputs/monitors/op_local/report.md\n"
                ),
            )

        runner = OperationRunner(
            project_id="op_local",
            state_dir=state_dir,
            config_path=str(config_path),
            command_runner=fake_runner,
        )

        result = runner.run(OP_GENERATE_REPORT, operator="tester")

        assert result["job"]["status"] == JOB_STATUS_SUCCEEDED
        assert result["job"]["result"]["command_kind"] == "local_python_entry"
        assert "main_monitor.py" in result["job"]["result"]["command"]
        assert "--once" in calls[0]
        assert "--report-mode" in calls[0]
        assert result["job"]["result"]["report_paths"] == [
            "outputs/monitors/op_local/report.md"
        ]
        assert len(runner.job_store.read_all()) == 3


def test_operation_refresh_remote_logs_uses_ssh_tail_allowlisted_command() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config_path = root / "projects.yaml"
        state_dir = str(root / "state")
        write_config(
            config_path,
            project_id="op_remote",
            mode="remote",
            remote_project_dir="/srv/app",
            log_files=["/srv/app/service.log"],
        )
        calls: list[list[str]] = []

        def fake_runner(args: list[str], timeout: int) -> CommandResult:
            calls.append(args)
            return CommandResult(returncode=0, stdout="line1\nline2\n")

        runner = OperationRunner(
            project_id="op_remote",
            state_dir=state_dir,
            config_path=str(config_path),
            command_runner=fake_runner,
        )

        result = runner.run(OP_REFRESH_LOGS, operator="tester")

        assert result["job"]["status"] == JOB_STATUS_SUCCEEDED
        assert result["job"]["result"]["command_kind"] == "remote_ssh_readonly"
        assert calls[0][0] == "ssh"
        assert calls[0][-1].startswith("tail -n ")
        assert "/srv/app/service.log" in calls[0][-1]


def test_operation_live_apply_is_blocked_without_approved_request() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config_path = root / "projects.yaml"
        state_dir = str(root / "state")
        write_config(
            config_path,
            project_id="op_local",
            mode="local",
            project_dir=str(root),
        )
        runner = OperationRunner(
            project_id="op_local",
            state_dir=state_dir,
            config_path=str(config_path),
        )

        result = runner.run(OP_LIVE_APPLY, operator="tester")

        assert result["job"]["status"] == JOB_STATUS_BLOCKED
        assert result["job"]["result"]["failure_reason"] == "approved_request_not_found"


def test_operation_live_apply_delegates_to_approved_recovery_worker(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config_path = root / "projects.yaml"
        state_dir = str(root / "state")
        write_config(
            config_path,
            project_id="op_local",
            mode="local",
            project_dir=str(root),
        )

        class FakeWorker:
            def __init__(self, **kwargs) -> None:
                self.kwargs = kwargs

            def latest_approved_request(self) -> dict[str, str]:
                return {"request_id": "approved-1"}

            def run_for_request(
                self,
                request_id: str,
                *,
                operator: str = "web-ui",
            ) -> dict[str, object]:
                assert request_id == "approved-1"
                assert operator == "tester"
                return {
                    "job": {
                        "job_id": "worker-job-1",
                        "status": JOB_STATUS_SUCCEEDED,
                        "summary": "worker done",
                        "result": {"failure_reason": ""},
                    },
                    "jobs": [],
                }

        monkeypatch.setattr(operation_runner_module, "ApprovedRecoveryWorker", FakeWorker)
        runner = OperationRunner(
            project_id="op_local",
            state_dir=state_dir,
            config_path=str(config_path),
        )

        result = runner.run(OP_LIVE_APPLY, operator="tester")

        assert result["job"]["status"] == JOB_STATUS_SUCCEEDED
        assert result["job"]["result"]["approved_recovery_job"]["job_id"] == (
            "worker-job-1"
        )


def test_operation_start_and_stop_monitor_records_pid(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config_path = root / "projects.yaml"
        state_dir = str(root / "state")
        write_config(
            config_path,
            project_id="op_local",
            mode="local",
            project_dir=str(root),
        )

        def fake_popen(args: list[str], log_file) -> BackgroundProcess:
            assert "main_monitor.py" in args
            assert "--daemon" in args
            return BackgroundProcess(pid=4242)

        signals: list[tuple[int, int]] = []

        def fake_kill(pid: int, sig: int) -> None:
            signals.append((pid, sig))

        monkeypatch.setattr(runtime_control_module.os, "kill", fake_kill)
        monkeypatch.setattr(operation_runner_module.os, "kill", fake_kill)

        runner = OperationRunner(
            project_id="op_local",
            state_dir=state_dir,
            config_path=str(config_path),
            popen_factory=fake_popen,
        )

        started = runner.run(OP_START_MONITOR, operator="tester")
        stopped = runner.run(OP_STOP_MONITOR, operator="tester")

        assert started["job"]["status"] == JOB_STATUS_SUCCEEDED
        assert started["job"]["runtime_status"] == RUNTIME_STATUS_MONITOR_RUNNING
        assert started["job"]["result"]["monitor_process"]["pid"] == 4242
        assert stopped["job"]["status"] == JOB_STATUS_SUCCEEDED
        assert (4242, signal.SIGTERM) in signals


def test_rollback_history_records_job_trace_audit_and_history() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        project_id = "op_local"
        project_dir = root / "project"
        output_root = root / "outputs"
        session_dir = output_root / project_id / "session-1"
        config_path = root / "projects.yaml"
        state_dir = str(root / "state")
        project_dir.mkdir()
        (project_dir / "config.json").write_text(
            json.dumps({"batch_size": 8}),
            encoding="utf-8",
        )
        write_config(
            config_path,
            project_id=project_id,
            mode="local",
            project_dir=str(project_dir),
        )

        apply_result = SafeApplyExecutor(
            project_dir=str(project_dir),
            session_dir=str(session_dir),
        ).apply("fix-gpu-1")
        assert apply_result.success
        assert json.loads((project_dir / "config.json").read_text(encoding="utf-8"))[
            "batch_size"
        ] == 4

        runner = OperationRunner(
            project_id=project_id,
            state_dir=state_dir,
            config_path=str(config_path),
            output_root=str(output_root),
        )
        history = runner.recovery_history_service.history()
        target = history["rollback_target"]
        assert target["rollback_available"] is True

        response = runner.rollback_history(target["identity"], operator="tester")

        assert response["job"]["action"] == OP_ROLLBACK_LATEST
        assert response["job"]["status"] == JOB_STATUS_SUCCEEDED
        assert json.loads((project_dir / "config.json").read_text(encoding="utf-8"))[
            "batch_size"
        ] == 8
        assert response["job"]["result"]["audit_json"]["rollback_success"] is True
        assert len(response["job"]["result"]["indexed_reports"]) == 2

        trace_stages = [
            item["stage"]
            for item in TraceStore(project_id=project_id, state_dir=state_dir).read_all()
        ]
        assert trace_stages[-4:] == [
            "execution_started",
            "execution_finished",
            "rollback_started",
            "rollback_finished",
        ]

        rows = runner.recovery_history_service.history()["records"]
        assert rows[0]["rollback_status"] == JOB_STATUS_SUCCEEDED
        assert rows[0]["rollback_available"] is False


def test_rollback_history_blocks_stale_identity() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config_path = root / "projects.yaml"
        state_dir = str(root / "state")
        write_config(
            config_path,
            project_id="op_local",
            mode="local",
            project_dir=str(root),
        )
        runner = OperationRunner(
            project_id="op_local",
            state_dir=state_dir,
            config_path=str(config_path),
            output_root=str(root / "outputs"),
        )

        result = runner.rollback_history("stale-identity", operator="tester")

        assert result["job"]["status"] == JOB_STATUS_BLOCKED
        assert result["job"]["result"]["failure_reason"] == (
            "rollback_target_identity_mismatch"
        )
