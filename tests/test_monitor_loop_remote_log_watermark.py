from __future__ import annotations

import json
import tempfile
from pathlib import Path

from monitors import MonitorLoop
from monitors.project_registry import (
    MonitorConfig,
    NotificationConfig,
    PolicyConfig,
    ProjectConfig,
    ProjectRegistry,
    SSHConfig,
)
from tools.remote_ssh_executor import RemoteCommandResult, RemoteSSHProfile


def _profile() -> RemoteSSHProfile:
    return RemoteSSHProfile(user="lf", host="localhost", port=22)


def _result(
    stdout: str = "",
    return_code: int | None = 0,
    command: str = "remote-test",
) -> RemoteCommandResult:
    return RemoteCommandResult(
        command=command,
        profile=_profile(),
        allowed=True,
        return_code=return_code,
        stdout=stdout,
        stderr="",
        reason="test",
    )


def _stat(inode: str, size: int, mtime: int) -> RemoteCommandResult:
    return _result(stdout=f"{inode} {size} {mtime}", command="stat")


class FakeRemoteExecutor:
    def __init__(
        self,
        stats: list[RemoteCommandResult],
        tails: list[RemoteCommandResult] | None = None,
        ranges: list[RemoteCommandResult] | None = None,
    ) -> None:
        self.stats = list(stats)
        self.tails = list(tails or [])
        self.ranges = list(ranges or [])
        self.calls: list[tuple] = []

    def stat_remote_log(
        self,
        profile: RemoteSSHProfile,
        remote_path: str,
    ) -> RemoteCommandResult:
        self.calls.append(("stat", remote_path))
        return self.stats.pop(0)

    def read_remote_log_tail(
        self,
        profile: RemoteSSHProfile,
        remote_path: str,
        lines: int,
    ) -> RemoteCommandResult:
        self.calls.append(("tail", remote_path, lines))
        return self.tails.pop(0)

    def read_remote_log_range(
        self,
        profile: RemoteSSHProfile,
        remote_path: str,
        offset: int,
        max_bytes: int,
    ) -> RemoteCommandResult:
        self.calls.append(("range", remote_path, offset, max_bytes))
        return self.ranges.pop(0)


def make_remote_project(project_id: str, log_path: str) -> ProjectConfig:
    return ProjectConfig(
        project_id=project_id,
        name="Remote Watermark Test",
        mode="remote",
        owner="tester",
        owner_contact="console",
        remote_project_dir="/tmp/project",
        run_command="python app.py",
        log_files=[log_path],
        ssh=SSHConfig(user="lf", host="localhost", port=22),
        monitor=MonitorConfig(
            tail_lines=50,
            auto_report=False,
            max_events_per_run=5,
        ),
        policy=PolicyConfig(auto_recover=False),
        notification=NotificationConfig(enabled=False, channels=[]),
    )


def test_monitor_loop_loads_persisted_remote_log_watermark_before_watcher_build() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        state_dir = tmp_path / "state"
        project_id = "remote_watermark_persisted"
        log_path = "/tmp/service.log"
        status_path = state_dir / project_id / "project_status.json"
        status_path.parent.mkdir(parents=True)
        status_path.write_text(
            json.dumps(
                {
                    "project_id": project_id,
                    "remote_log_watermarks": {
                        log_path: {
                            "inode": "12345",
                            "size": 100,
                            "mtime": 1780000000,
                            "offset": 100,
                            "last_read_at": "2026-06-30 12:00:00",
                            "last_strategy": "tail_bootstrap",
                            "fallback_reason": "",
                        }
                    },
                }
            ),
            encoding="utf-8",
        )

        loop = MonitorLoop(
            project=make_remote_project(project_id, log_path),
            state_dir=str(state_dir),
            output_root=str(tmp_path / "outputs"),
            enable_persistent_state=True,
        )
        fake_executor = FakeRemoteExecutor(
            stats=[_stat("12345", 120, 1780000001)],
            ranges=[_result(stdout="new healthy line", command="range")],
        )
        loop.session.remote_executor = fake_executor

        chunks = loop.watcher.poll()

        loaded = loop.state_store.load()
        assert len(chunks) == 1
        assert fake_executor.calls == [
            ("stat", log_path),
            ("range", log_path, 100, 20),
        ]
        assert loaded.remote_log_watermarks[log_path]["offset"] == 120
        assert loaded.remote_log_watermarks[log_path]["last_strategy"] == "incremental"


def test_monitor_loop_remote_log_watermark_stays_in_memory_when_persistence_disabled() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        log_path = "/tmp/service.log"
        loop = MonitorLoop(
            project=make_remote_project("remote_watermark_memory", log_path),
            state_dir=str(tmp_path / "state"),
            output_root=str(tmp_path / "outputs"),
            enable_persistent_state=False,
        )
        fake_executor = FakeRemoteExecutor(
            stats=[_stat("12345", 100, 1780000000)],
            tails=[_result(stdout="bootstrap healthy line", command="tail")],
        )
        loop.session.remote_executor = fake_executor

        chunks = loop.watcher.poll()

        assert len(chunks) == 1
        assert loop.project_state.remote_log_watermarks[log_path]["offset"] == 100
        assert loop.project_state.remote_log_watermarks[log_path]["last_strategy"] == (
            "tail_bootstrap"
        )
        assert not loop.state_store.status_path.exists()


def test_monitor_loop_records_remote_log_metrics_in_runtime_health() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        log_path = "/tmp/service.log"
        loop = MonitorLoop(
            project=make_remote_project("remote_watermark_health", log_path),
            state_dir=str(tmp_path / "state"),
            output_root=str(tmp_path / "outputs"),
            enable_persistent_state=True,
        )
        fake_executor = FakeRemoteExecutor(
            stats=[_stat("12345", 100, 1780000000)],
            tails=[_result(stdout="bootstrap healthy line", command="tail")],
        )
        loop.session.remote_executor = fake_executor
        loop.detector.detect_all = lambda text, source: []

        events = loop.run_once()

        health = loop.state_store.load().runtime_health
        assert events == []
        assert health["remote_log_strategy_counts"] == {"tail_bootstrap": 1}
        assert health["remote_log_fallback_count"] == 0
        assert health["remote_log_bytes_read"] == len(
            "bootstrap healthy line".encode("utf-8")
        )
        assert health["remote_log_watermark_errors"] == []


def test_monitor_loop_rate_limits_remote_log_daemon_notices() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        log_path = "/tmp/service.log"
        daemon_log_path = tmp_path / "daemon.log"
        project = make_remote_project("remote_watermark_notice", log_path)
        project.monitor.remote_log_max_bytes_per_poll = 100

        loop = MonitorLoop(
            project=project,
            state_dir=str(tmp_path / "state"),
            output_root=str(tmp_path / "outputs"),
            daemon_log_path=str(daemon_log_path),
            enable_persistent_state=True,
        )
        fake_executor = FakeRemoteExecutor(
            stats=[
                _stat("12345", 1000, 1780000000),
                _stat("12345", 2000, 1780000001),
            ],
            tails=[
                _result(stdout="latest tail one", command="tail"),
                _result(stdout="latest tail two", command="tail"),
            ],
        )
        loop.session.remote_executor = fake_executor
        loop.detector.detect_all = lambda text, source: []

        loop.run_once()
        loop.run_once()

        daemon_log = daemon_log_path.read_text(encoding="utf-8")
        assert daemon_log.count("remote log watermark notice") == 1
        assert "delta_exceeds_max_bytes_per_poll" in daemon_log


def test_project_registry_parses_remote_watermark_monitor_options() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        config_path = Path(tmp) / "projects.yaml"
        config_path.write_text(
            """
projects:
  - project_id: remote_config
    name: Remote Config Test
    mode: remote
    ssh:
      user: lf
      host: localhost
      port: 22
    log_files:
      - /tmp/service.log
    monitor:
      interval_seconds: 5
      tail_lines: 123
      remote_watermark_enabled: false
      remote_watermark_shadow: true
      remote_log_max_bytes_per_poll: 4096
      auto_report: false
      max_events_per_run: 3
""",
            encoding="utf-8",
        )

        project = ProjectRegistry(str(config_path)).get("remote_config")

        assert project.monitor.tail_lines == 123
        assert project.monitor.remote_watermark_enabled is False
        assert project.monitor.remote_watermark_shadow is True
        assert project.monitor.remote_log_max_bytes_per_poll == 4096
