from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from detectors import ErrorEvent
from monitors.rate_limit_tracker import RateLimitConfig, RateLimitTracker


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_event(event_type: str, signature: str) -> ErrorEvent:
    return ErrorEvent(
        event_type=event_type,
        issue_type=event_type,
        severity="high",
        summary=f"{event_type} summary",
        source="test",
        raw_excerpt=f"{event_type} evidence",
        signature=signature,
    )


def test_report_event_type_limit_suppresses_third_same_type() -> None:
    tracker = RateLimitTracker(
        config=RateLimitConfig(max_reports_per_event_type_per_cycle=2)
    )
    tracker.begin_cycle()

    first = make_event("process_crash", "one")
    second = make_event("process_crash", "two")
    third = make_event("process_crash", "three")

    assert tracker.reserve_report_capacity(first).allowed
    assert tracker.reserve_report_capacity(second).allowed

    decision = tracker.reserve_report_capacity(third)

    assert not decision.allowed
    assert "per-event-type report limit" in decision.reason
    assert len(tracker.cycle.suppressed_reports) == 1


def test_report_fingerprint_cooldown_allows_after_window() -> None:
    clock = FakeClock()
    tracker = RateLimitTracker(
        config=RateLimitConfig(report_fingerprint_cooldown_seconds=60),
        clock=clock,
    )
    event = make_event("network_port", "same")

    tracker.begin_cycle()
    assert tracker.reserve_report_capacity(event).allowed

    tracker.begin_cycle()
    blocked = tracker.reserve_report_capacity(event)
    assert not blocked.allowed
    assert "fingerprint cooldown" in blocked.reason

    clock.advance(61)
    tracker.begin_cycle()
    assert tracker.reserve_report_capacity(event).allowed


def test_alert_duplicate_fingerprint_is_suppressed() -> None:
    tracker = RateLimitTracker()
    event = make_event("container_k8s", "same-alert")

    tracker.begin_cycle()
    assert tracker.reserve_alert_capacity(event).allowed

    tracker.begin_cycle()
    blocked = tracker.reserve_alert_capacity(event)

    assert not blocked.allowed
    assert "already alerted" in blocked.reason
