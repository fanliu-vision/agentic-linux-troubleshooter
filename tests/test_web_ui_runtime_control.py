from __future__ import annotations

import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from web_ui.runtime_control import (
    CHECK_ERROR,
    CHECK_OK,
    CHECK_SKIPPED,
    CONNECTION_STATUS_CONNECTED,
    CONNECTION_STATUS_ERROR,
    JOB_STATUS_FAILED,
    JOB_STATUS_SUCCEEDED,
    RUNTIME_STATUS_CONNECTED,
    RUNTIME_STATUS_ERROR,
    CommandResult,
    RuntimeControlService,
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
    name: Runtime UI Test Project
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
      auto_recovery_dry_run: false
      rollback_on_failure: true
      allow_auto_apply:
        - fix-network-1
      escalation_required: []
""",
        encoding="utf-8",
    )


def test_runtime_connect_local_success_records_job_and_checks() -> None:
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
            project_id="runtime_local",
            mode="local",
            project_dir=str(project_dir),
            log_files=["service.log"],
        )
        service = RuntimeControlService(
            project_id="runtime_local",
            state_dir=state_dir,
            config_path=str(config_path),
        )

        result = service.connect(connection_mode="local", operator="tester")

        assert result["job"]["status"] == JOB_STATUS_SUCCEEDED
        assert result["runtime"]["connection_status"] == CONNECTION_STATUS_CONNECTED
        assert result["runtime"]["runtime_status"] == RUNTIME_STATUS_CONNECTED
        checks = {item["name"]: item for item in result["runtime"]["checks"]}
        assert checks["ssh_reachable"]["status"] == CHECK_SKIPPED
        assert checks["project_dir"]["status"] == CHECK_OK
        assert checks["log_files"]["status"] == CHECK_OK
        assert checks["config_file"]["status"] == CHECK_OK
        assert checks["run_command"]["status"] == CHECK_OK

        jobs = service.jobs()["jobs"]
        assert jobs[0]["action"] == "connect"
        assert jobs[0]["status"] == JOB_STATUS_SUCCEEDED
        assert len(service.job_store.read_all()) == 3


def test_runtime_connect_local_missing_config_fails_runtime() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        project_dir = root / "project"
        project_dir.mkdir()
        (project_dir / "service.log").write_text("ready", encoding="utf-8")
        config_path = root / "projects.yaml"
        state_dir = str(root / "state")
        write_config(
            config_path,
            project_id="runtime_local",
            mode="local",
            project_dir=str(project_dir),
            log_files=["service.log"],
        )
        service = RuntimeControlService(
            project_id="runtime_local",
            state_dir=state_dir,
            config_path=str(config_path),
        )

        result = service.health_check(connection_mode="local", operator="tester")

        assert result["job"]["status"] == JOB_STATUS_FAILED
        assert result["runtime"]["connection_status"] == CONNECTION_STATUS_ERROR
        assert result["runtime"]["runtime_status"] == RUNTIME_STATUS_ERROR
        checks = {item["name"]: item for item in result["runtime"]["checks"]}
        assert checks["config_file"]["status"] == CHECK_ERROR


def test_runtime_connect_remote_uses_ssh_runner_for_readonly_checks() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config_path = root / "projects.yaml"
        state_dir = str(root / "state")
        write_config(
            config_path,
            project_id="runtime_remote",
            mode="remote",
            remote_project_dir="/srv/runtime-app",
            log_files=["/srv/runtime-app/service.log"],
            run_command="python app.py --config=config.json",
        )
        calls: list[list[str]] = []

        def fake_runner(args: list[str], timeout: int) -> CommandResult:
            calls.append(args)
            if args[-1].startswith("echo "):
                return CommandResult(returncode=0, stdout="AGENTIC_TRACE_SSH_OK\n")
            return CommandResult(returncode=0, stdout="")

        service = RuntimeControlService(
            project_id="runtime_remote",
            state_dir=state_dir,
            config_path=str(config_path),
            command_runner=fake_runner,
        )

        result = service.connect(connection_mode="remote", operator="tester")

        assert result["job"]["status"] == JOB_STATUS_SUCCEEDED
        checks = {item["name"]: item for item in result["runtime"]["checks"]}
        assert checks["ssh_reachable"]["status"] == CHECK_OK
        assert checks["project_dir"]["status"] == CHECK_OK
        assert checks["log_files"]["status"] == CHECK_OK
        assert checks["config_file"]["status"] == CHECK_OK
        assert checks["config_file"]["target"] == "/srv/runtime-app/config.json"
        assert len(calls) == 4
        assert all(call[0] == "ssh" for call in calls)
