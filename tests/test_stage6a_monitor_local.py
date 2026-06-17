from __future__ import annotations

import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from monitors import MonitorLoop
from monitors.cycle_summary_reporter import CycleEventRecord
from monitors.project_registry import (
    MonitorConfig,
    NotificationConfig,
    PolicyConfig,
    ProjectConfig,
)


def reset_log(log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        "[service] starting enterprise order monitoring service\n"
        "[service] health=OK\n",
        encoding="utf-8",
    )


def append_error(log_path: Path) -> None:
    with log_path.open("a", encoding="utf-8") as f:
        f.write(
            "\nTraceback (most recent call last):\n"
            "  File \"/srv/order-service/run_service.py\", line 132, in start_metrics_exporter\n"
            "    server_socket.bind(('127.0.0.1', 9100))\n"
            "OSError: [Errno 98] Address already in use\n"
            "[summary]\n"
            "primary_failure=Address already in use\n"
        )


def make_project(project_dir: Path, alerts_dir: Path) -> ProjectConfig:
    return ProjectConfig(
        project_id="enterprise_demo_local",
        name="Stage 6A Isolated Monitor Test",
        mode="local",
        owner="tester",
        owner_contact="console",
        project_dir=str(project_dir),
        run_command="python app.py",
        log_files=["outputs/service.log"],
        monitor=MonitorConfig(
            interval_seconds=1,
            tail_lines=200,
            auto_report=False,
            max_events_per_run=5,
        ),
        policy=PolicyConfig(auto_recover=False),
        notification=NotificationConfig(
            enabled=False,
            channels=[],
            alerts_dir=str(alerts_dir),
        ),
    )


def make_record(event) -> CycleEventRecord:
    return CycleEventRecord(
        event_type=event.event_type,
        issue_type=event.issue_type,
        severity=event.severity,
        summary=event.summary,
        source=event.source,
        fingerprint=event.fingerprint,
        action="report_only",
        fix_id="",
        apply_success=False,
        rerun_success=False,
        rollback_executed=False,
        recovered=False,
        notification_status="skipped",
        notification_channels=[],
        notification_results=[],
        report_paths=[],
    )


def test_stage6a_monitor_local() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        project_dir = tmp_path / "runtime_project"
        log_path = project_dir / "outputs" / "service.log"
        state_dir = tmp_path / "state"
        output_root = tmp_path / "outputs" / "monitors"
        alerts_dir = tmp_path / "outputs" / "alerts"

        reset_log(log_path)

        loop = MonitorLoop(
            project=make_project(project_dir=project_dir, alerts_dir=alerts_dir),
            agent_depth="balanced",
            report_mode="rule",
            output_root=str(output_root),
            state_dir=str(state_dir),
            enable_persistent_state=True,
        )
        loop._handle_event = make_record

        events = loop.run_once()
        print("=" * 100)
        print("FIRST POLL")
        print("=" * 100)
        print(events)

        assert len(events) == 0
        assert str(loop.state_store.status_path).startswith(str(state_dir))

        append_error(log_path)

        events = loop.run_once()
        print("=" * 100)
        print("SECOND POLL")
        print("=" * 100)
        for event in events:
            print(event)

        assert len(events) == 1
        assert events[0].event_type == "network_port"
        assert events[0].fingerprint in loop.seen_fingerprints
        assert events[0].fingerprint in loop.state_store.seen_fingerprints()
        assert len(loop.reports_generated) == 1
        assert Path(loop.reports_generated[0]).exists()
        assert str(loop.reports_generated[0]).startswith(str(output_root))

        duplicate_events = loop.run_once()
        assert duplicate_events == []


def main() -> None:
    test_stage6a_monitor_local()
    print("=" * 100)
    print("STAGE 6A MONITOR LOCAL TEST PASSED")
    print("=" * 100)


if __name__ == "__main__":
    main()
