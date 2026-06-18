from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from detectors import ErrorEvent
from monitors import MonitorLoop
from monitors.cycle_summary_reporter import CycleEventRecord
from monitors.project_registry import (
    MonitorConfig,
    NotificationConfig,
    PolicyConfig,
    ProjectConfig,
)


def make_project(project_id: str) -> ProjectConfig:
    return ProjectConfig(
        project_id=project_id,
        name="MonitorLoop Seen Test",
        mode="local",
        owner="tester",
        owner_contact="console",
        project_dir=".",
        run_command="python app.py",
        log_files=[],
        monitor=MonitorConfig(auto_report=False, max_events_per_run=5),
        policy=PolicyConfig(auto_recover=False),
        notification=NotificationConfig(enabled=False, channels=[]),
    )


def make_event(signature: str = "signature-1") -> ErrorEvent:
    return ErrorEvent(
        event_type="network_port",
        issue_type="network_port",
        severity="medium",
        summary="port conflict",
        source="test",
        raw_excerpt="OSError: [Errno 98] Address already in use",
        signature=signature,
    )


def make_record(event: ErrorEvent) -> CycleEventRecord:
    return CycleEventRecord(
        event_type=event.event_type,
        issue_type=event.issue_type,
        severity=event.severity,
        summary=event.summary,
        source=event.source,
        fingerprint=event.fingerprint,
        action="auto_recover",
        fix_id="fix-network-1",
        apply_success=True,
        rerun_success=True,
        rollback_executed=False,
        recovered=True,
        notification_status="recovered",
        notification_channels=[],
        notification_results=[],
        report_paths=[],
    )


def make_loop(project_id: str, state_dir: str, output_root: str, event: ErrorEvent) -> MonitorLoop:
    loop = MonitorLoop(
        project=make_project(project_id),
        state_dir=state_dir,
        output_root=output_root,
        enable_persistent_state=True,
    )
    loop.watcher = SimpleNamespace(
        poll=lambda: [
            SimpleNamespace(content="detected error", source="test", path="service.log")
        ]
    )
    loop.detector = SimpleNamespace(
        detect=lambda text, source: [event]
    )
    return loop


def assert_runtime_health_fields(health: dict) -> None:
    expected_fields = {
        "last_cycle_started_at",
        "last_cycle_finished_at",
        "last_cycle_duration_seconds",
        "last_events_detected",
        "last_reports_generated",
        "last_alerts_generated",
        "last_error",
        "last_exception_type",
        "daemon_pid",
        "daemon_uptime_seconds",
        "llm_fallback_used",
        "health_status",
        "health_message",
    }

    assert expected_fields <= set(health)


def test_handle_event_failure_does_not_mark_seen() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        event = make_event("failure")
        loop = make_loop(
            project_id="seen_failure",
            state_dir=str(Path(tmp) / "state"),
            output_root=str(Path(tmp) / "outputs"),
            event=event,
        )
        loop._handle_event = lambda event: (_ for _ in ()).throw(RuntimeError("boom"))

        events = loop.run_once()

        assert events == []
        assert event.fingerprint not in loop.seen_fingerprints
        assert event.fingerprint not in loop.state_store.seen_fingerprints()
        assert loop.project_state.status == "event_handling_failed"
        assert loop.project_state.idle_cycles == 0

        health = loop.state_store.load().runtime_health
        assert_runtime_health_fields(health)
        assert health["health_status"] == "degraded"
        assert health["health_message"] == "event handling failed; daemon continued"
        assert health["last_exception_type"] == "RuntimeError"
        assert "boom" in health["last_error"]
        assert health["last_events_detected"] == 0
        assert health["last_reports_generated"] == 0


def test_handle_event_success_marks_seen() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        event = make_event("success")
        loop = make_loop(
            project_id="seen_success",
            state_dir=str(Path(tmp) / "state"),
            output_root=str(Path(tmp) / "outputs"),
            event=event,
        )
        loop._handle_event = lambda event: make_record(event)
        loop._write_cycle_summary_report = lambda records: str(Path(tmp) / "cycle.md")

        events = loop.run_once()

        assert events == [event]
        assert event.fingerprint in loop.seen_fingerprints
        assert event.fingerprint in loop.state_store.seen_fingerprints()

        health = loop.state_store.load().runtime_health
        assert_runtime_health_fields(health)
        assert health["health_status"] == "ok"
        assert health["health_message"] == "monitor cycle completed"
        assert health["last_events_detected"] == 1
        assert health["last_reports_generated"] == 1
        assert health["last_alerts_generated"] == 0
        assert health["last_error"] == ""
        assert health["last_exception_type"] == ""
        assert health["daemon_pid"] > 0
        assert health["daemon_uptime_seconds"] >= 0
        assert health["llm_fallback_used"] is False


def test_successful_event_generates_cycle_summary() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        event = make_event("summary")
        loop = make_loop(
            project_id="seen_summary",
            state_dir=str(Path(tmp) / "state"),
            output_root=str(Path(tmp) / "outputs"),
            event=event,
        )
        summary_path = str(Path(tmp) / "cycle_summary_report.md")
        calls: list[list[CycleEventRecord]] = []

        loop._handle_event = lambda event: make_record(event)

        def write_summary(records: list[CycleEventRecord]) -> str:
            calls.append(records)
            return summary_path

        loop._write_cycle_summary_report = write_summary

        loop.run_once()

        assert len(calls) == 1
        assert calls[0][0].fingerprint == event.fingerprint
        assert loop.reports_generated[0] == summary_path
        assert loop.project_state.last_report_path == summary_path


def test_cycle_summary_failure_does_not_crash_run_once() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        event = make_event("summary-failure")
        loop = make_loop(
            project_id="seen_summary_failure",
            state_dir=str(Path(tmp) / "state"),
            output_root=str(Path(tmp) / "outputs"),
            event=event,
        )
        loop._handle_event = lambda event: make_record(event)
        loop._write_cycle_summary_report = lambda records: (_ for _ in ()).throw(
            RuntimeError("summary failed")
        )

        events = loop.run_once()

        assert events == [event]
        assert event.fingerprint in loop.seen_fingerprints
        assert loop.reports_generated == []

        health = loop.state_store.load().runtime_health
        assert_runtime_health_fields(health)
        assert health["health_status"] == "degraded"
        assert health["health_message"] == "cycle summary report generation failed; daemon continued"
        assert health["last_exception_type"] == "RuntimeError"
        assert "summary failed" in health["last_error"]
        assert health["last_events_detected"] == 1
        assert health["last_reports_generated"] == 0


def main() -> None:
    test_handle_event_failure_does_not_mark_seen()
    test_handle_event_success_marks_seen()
    test_successful_event_generates_cycle_summary()
    test_cycle_summary_failure_does_not_crash_run_once()
    print("=" * 100)
    print("STAGE 6E MONITOR LOOP SEEN TEST PASSED")
    print("=" * 100)


if __name__ == "__main__":
    main()
