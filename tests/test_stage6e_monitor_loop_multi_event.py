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


def make_project(
    project_id: str,
    auto_recover: bool = False,
    max_events_per_run: int = 5,
) -> ProjectConfig:
    return ProjectConfig(
        project_id=project_id,
        name="MonitorLoop Multi Event Test",
        mode="local",
        owner="tester",
        owner_contact="console",
        project_dir=".",
        run_command="python app.py",
        log_files=[],
        monitor=MonitorConfig(auto_report=False, max_events_per_run=max_events_per_run),
        policy=PolicyConfig(
            auto_recover=auto_recover,
            allow_auto_apply=[
                "fix-network-1",
                "fix-gpu-1",
                "fix-python-1",
            ],
        ),
        notification=NotificationConfig(enabled=False, channels=[]),
    )


def make_event(
    event_type: str,
    issue_type: str,
    signature: str,
    severity: str = "high",
) -> ErrorEvent:
    return ErrorEvent(
        event_type=event_type,
        issue_type=issue_type,
        severity=severity,
        summary=f"{event_type} summary",
        source="test",
        raw_excerpt=f"{event_type} evidence",
        signature=signature,
    )


def make_record(
    event: ErrorEvent,
    action: str = "manual_escalation",
    report_paths: list[str] | None = None,
) -> CycleEventRecord:
    return CycleEventRecord(
        event_type=event.event_type,
        issue_type=event.issue_type,
        severity=event.severity,
        summary=event.summary,
        source=event.source,
        fingerprint=event.fingerprint,
        action=action,
        fix_id="fix-network-1" if action == "auto_recover" else "",
        apply_success=action == "auto_recover",
        rerun_success=action == "auto_recover",
        rollback_executed=False,
        recovered=action == "auto_recover",
        notification_status=action,
        notification_channels=[],
        notification_results=[],
        report_paths=list(report_paths or []),
    )


class DetectAllStub:
    def __init__(self, events: list[ErrorEvent]) -> None:
        self.events = events
        self.detect_all_calls = 0
        self.detect_calls = 0

    def detect_all(self, text: str, source: str) -> list[ErrorEvent]:
        self.detect_all_calls += 1
        return list(self.events)

    def detect(self, text: str, source: str) -> list[ErrorEvent]:
        self.detect_calls += 1
        return []


def make_loop(
    tmp: str,
    events: list[ErrorEvent],
    project_id: str = "multi_event",
    auto_recover: bool = False,
    max_events_per_run: int = 5,
) -> tuple[MonitorLoop, DetectAllStub]:
    detector = DetectAllStub(events)
    loop = MonitorLoop(
        project=make_project(
            project_id=project_id,
            auto_recover=auto_recover,
            max_events_per_run=max_events_per_run,
        ),
        state_dir=str(Path(tmp) / "state"),
        output_root=str(Path(tmp) / "outputs" / "monitors"),
        enable_persistent_state=True,
    )
    loop.watcher = SimpleNamespace(
        poll=lambda: [
            SimpleNamespace(content="detected errors", source="test", path="service.log")
        ]
    )
    loop.detector = detector
    return loop, detector


def test_detect_all_two_manual_events_are_both_processed() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        event1 = make_event("process_crash", "process", "manual-1")
        event2 = make_event("container_k8s", "container_k8s", "manual-2")
        loop, detector = make_loop(tmp, [event1, event2])
        handled: list[ErrorEvent] = []
        summary_calls: list[list[CycleEventRecord]] = []

        def handle(event: ErrorEvent) -> CycleEventRecord:
            handled.append(event)
            return make_record(event)

        def write_summary(records: list[CycleEventRecord]) -> str:
            summary_calls.append(records)
            return str(Path(tmp) / "cycle_summary.md")

        loop._handle_event = handle
        loop._write_cycle_summary_report = write_summary

        events = loop.run_once()

        assert detector.detect_all_calls == 1
        assert detector.detect_calls == 0
        assert events == [event1, event2]
        assert handled == [event1, event2]
        assert event1.fingerprint in loop.seen_fingerprints
        assert event2.fingerprint in loop.seen_fingerprints
        assert len(summary_calls) == 1
        assert [record.fingerprint for record in summary_calls[0]] == [
            event1.fingerprint,
            event2.fingerprint,
        ]


def test_one_success_one_failure_marks_only_success_and_keeps_running() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        event1 = make_event("process_crash", "process", "success")
        event2 = make_event("container_k8s", "container_k8s", "failure")
        loop, _ = make_loop(tmp, [event1, event2])

        def handle(event: ErrorEvent) -> CycleEventRecord:
            if event is event2:
                raise RuntimeError("boom")
            return make_record(event)

        loop._handle_event = handle
        loop._write_cycle_summary_report = lambda records: str(Path(tmp) / "cycle.md")

        events = loop.run_once()

        assert events == [event1]
        assert event1.fingerprint in loop.seen_fingerprints
        assert event2.fingerprint not in loop.seen_fingerprints


def test_seen_event_is_skipped_without_blocking_unseen_event() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        seen_event = make_event("process_crash", "process", "seen")
        new_event = make_event("container_k8s", "container_k8s", "new")
        loop, _ = make_loop(tmp, [seen_event, new_event])
        handled: list[ErrorEvent] = []

        loop.seen_fingerprints.add(seen_event.fingerprint)
        loop._handle_event = lambda event: handled.append(event) or make_record(event)
        loop._write_cycle_summary_report = lambda records: str(Path(tmp) / "cycle.md")

        events = loop.run_once()

        assert events == [new_event]
        assert handled == [new_event]
        assert new_event.fingerprint in loop.seen_fingerprints


def test_more_than_three_events_only_processes_first_three() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        events = [
            make_event("process_crash", "process", "event-1"),
            make_event("container_k8s", "container_k8s", "event-2"),
            make_event("host_resource", "host_resource", "event-3"),
            make_event("dependency_service", "dependency_service", "event-4"),
        ]
        loop, _ = make_loop(tmp, events)
        handled: list[ErrorEvent] = []

        loop._handle_event = lambda event: handled.append(event) or make_record(event)
        loop._write_cycle_summary_report = lambda records: str(Path(tmp) / "cycle.md")

        detected = loop.run_once()

        assert detected == events[: MonitorLoop.MAX_EVENTS_PER_CYCLE]
        assert handled == events[: MonitorLoop.MAX_EVENTS_PER_CYCLE]
        assert events[3].fingerprint not in loop.seen_fingerprints


def test_same_event_type_report_rate_limit_suppresses_third_instance() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        events = [
            make_event("process_crash", "process", "same-type-1"),
            make_event("process_crash", "process", "same-type-2"),
            make_event("process_crash", "process", "same-type-3"),
        ]
        loop, _ = make_loop(tmp, events)
        handled: list[ErrorEvent] = []
        summary_calls: list[list[CycleEventRecord]] = []

        loop._handle_event = lambda event: handled.append(event) or make_record(event)

        def write_summary(records: list[CycleEventRecord]) -> str:
            summary_calls.append(records)
            return str(Path(tmp) / "cycle.md")

        loop._write_cycle_summary_report = write_summary

        detected = loop.run_once()

        assert detected == events[:2]
        assert handled == events[:2]
        assert events[0].fingerprint in loop.seen_fingerprints
        assert events[1].fingerprint in loop.seen_fingerprints
        assert events[2].fingerprint not in loop.seen_fingerprints
        assert len(summary_calls) == 1
        assert [record.fingerprint for record in summary_calls[0]] == [
            events[0].fingerprint,
            events[1].fingerprint,
        ]

        health = loop.state_store.load().runtime_health
        assert health["health_status"] == "ok"
        assert health["last_events_detected"] == 2
        assert health["rate_limited_reports"] == 1


def test_legacy_detect_fallback_still_handles_single_event() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        event = make_event("process_crash", "process", "legacy")
        loop = MonitorLoop(
            project=make_project("legacy_detect"),
            state_dir=str(Path(tmp) / "state"),
            output_root=str(Path(tmp) / "outputs" / "monitors"),
            enable_persistent_state=True,
        )
        loop.watcher = SimpleNamespace(
            poll=lambda: [
                SimpleNamespace(content="detected error", source="test", path="service.log")
            ]
        )
        loop.detector = SimpleNamespace(detect=lambda text, source: [event])
        loop._handle_event = lambda event: make_record(event)
        loop._write_cycle_summary_report = lambda records: str(Path(tmp) / "cycle.md")

        events = loop.run_once()

        assert events == [event]
        assert event.fingerprint in loop.seen_fingerprints


def test_cycle_summary_failure_does_not_affect_successful_events() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        event1 = make_event("process_crash", "process", "summary-1")
        event2 = make_event("container_k8s", "container_k8s", "summary-2")
        loop, _ = make_loop(tmp, [event1, event2])

        loop._handle_event = lambda event: make_record(event)
        loop._write_cycle_summary_report = lambda records: (_ for _ in ()).throw(
            RuntimeError("summary failed")
        )

        events = loop.run_once()

        assert events == [event1, event2]
        assert event1.fingerprint in loop.seen_fingerprints
        assert event2.fingerprint in loop.seen_fingerprints
        assert loop.reports_generated == []

        health = loop.state_store.load().runtime_health
        assert health["health_status"] == "degraded"
        assert health["last_exception_type"] == "RuntimeError"
        assert "summary failed" in health["last_error"]
        assert health["last_events_detected"] == 2
        assert health["last_reports_generated"] == 0


def test_auto_recovery_candidates_are_limited_to_one_per_cycle() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        event1 = make_event("gpu_oom", "gpu", "auto-1")
        event2 = make_event("network_port", "network_port", "auto-2", severity="medium")
        loop, _ = make_loop(
            tmp,
            [event1, event2],
            project_id="auto_limit",
            auto_recover=True,
        )
        handled: list[ErrorEvent] = []

        loop._handle_event = lambda event: handled.append(event) or make_record(
            event,
            action="auto_recover",
        )
        loop._write_cycle_summary_report = lambda records: str(Path(tmp) / "cycle.md")

        events = loop.run_once()

        assert events == [event1]
        assert handled == [event1]
        assert event1.fingerprint in loop.seen_fingerprints
        assert event2.fingerprint not in loop.seen_fingerprints
