from __future__ import annotations

import time
from dataclasses import dataclass, field

from detectors import ErrorEvent


@dataclass
class RateLimitDecision:
    allowed: bool
    reason: str = ""


@dataclass
class RateLimitConfig:
    max_events_per_cycle: int = 3
    max_reports_per_cycle: int = 5
    max_alerts_per_cycle: int = 3
    max_reports_per_event_type_per_cycle: int = 2
    max_alerts_per_event_type_per_cycle: int = 2
    report_fingerprint_cooldown_seconds: int = 300
    alert_fingerprint_cooldown_seconds: int = 300


@dataclass
class CycleRateLimitState:
    events_handled: int = 0
    reports_reserved: int = 0
    alerts_reserved: int = 0
    reports_by_event_type: dict[str, int] = field(default_factory=dict)
    alerts_by_event_type: dict[str, int] = field(default_factory=dict)
    suppressed_reports: list[str] = field(default_factory=list)
    suppressed_alerts: list[str] = field(default_factory=list)
    suppressed_events: list[str] = field(default_factory=list)


class RateLimitTracker:
    """
    In-memory runtime flood control for one monitor process.

    It only decides whether the current process should enter expensive report or
    alert paths. It does not change detector, policy, recovery, or report content.
    """

    def __init__(
            self,
            config: RateLimitConfig | None = None,
            clock=time.time,
    ) -> None:
        self.config = config or RateLimitConfig()
        self.clock = clock
        self.cycle = CycleRateLimitState()
        self._last_report_by_fingerprint: dict[str, float] = {}
        self._last_alert_by_fingerprint: dict[str, float] = {}
        self._alerted_fingerprints: set[str] = set()

    def begin_cycle(self) -> None:
        self.cycle = CycleRateLimitState()

    def allow_event(self, event: ErrorEvent) -> RateLimitDecision:
        if self.cycle.events_handled >= self.config.max_events_per_cycle:
            reason = (
                "event suppressed by per-cycle event limit: "
                f"event_type={event.event_type}, fingerprint={event.fingerprint}"
            )
            self.cycle.suppressed_events.append(reason)
            return RateLimitDecision(False, reason)

        return RateLimitDecision(True)

    def record_event_handled(self) -> None:
        self.cycle.events_handled += 1

    def reserve_report_capacity(
            self,
            event: ErrorEvent,
            expected_reports: int = 1,
    ) -> RateLimitDecision:
        expected_reports = max(1, int(expected_reports))

        if self.cycle.reports_reserved + expected_reports > self.config.max_reports_per_cycle:
            reason = (
                "report suppressed by per-cycle report limit: "
                f"event_type={event.event_type}, fingerprint={event.fingerprint}, "
                f"expected_reports={expected_reports}"
            )
            self.cycle.suppressed_reports.append(reason)
            return RateLimitDecision(False, reason)

        current_for_type = self.cycle.reports_by_event_type.get(event.event_type, 0)
        if current_for_type >= self.config.max_reports_per_event_type_per_cycle:
            reason = (
                "report suppressed by per-event-type report limit: "
                f"event_type={event.event_type}, fingerprint={event.fingerprint}, "
                f"expected_reports={expected_reports}"
            )
            self.cycle.suppressed_reports.append(reason)
            return RateLimitDecision(False, reason)

        cooldown = self._cooldown_decision(
            registry=self._last_report_by_fingerprint,
            fingerprint=event.fingerprint,
            cooldown_seconds=self.config.report_fingerprint_cooldown_seconds,
            label="report",
            event=event,
        )
        if not cooldown.allowed:
            self.cycle.suppressed_reports.append(cooldown.reason)
            return cooldown

        self.cycle.reports_reserved += expected_reports
        self.cycle.reports_by_event_type[event.event_type] = current_for_type + 1
        self._last_report_by_fingerprint[event.fingerprint] = self.clock()
        return RateLimitDecision(True)

    def reserve_cycle_report(self, report_name: str = "cycle_summary") -> RateLimitDecision:
        if self.cycle.reports_reserved + 1 > self.config.max_reports_per_cycle:
            reason = f"{report_name} suppressed by per-cycle report limit"
            self.cycle.suppressed_reports.append(reason)
            return RateLimitDecision(False, reason)

        self.cycle.reports_reserved += 1
        return RateLimitDecision(True)

    def reserve_alert_capacity(self, event: ErrorEvent) -> RateLimitDecision:
        if event.fingerprint in self._alerted_fingerprints:
            reason = (
                "alert suppressed because fingerprint was already alerted: "
                f"event_type={event.event_type}, fingerprint={event.fingerprint}"
            )
            self.cycle.suppressed_alerts.append(reason)
            return RateLimitDecision(False, reason)

        if self.cycle.alerts_reserved >= self.config.max_alerts_per_cycle:
            reason = (
                "alert suppressed by per-cycle alert limit: "
                f"event_type={event.event_type}, fingerprint={event.fingerprint}"
            )
            self.cycle.suppressed_alerts.append(reason)
            return RateLimitDecision(False, reason)

        current_for_type = self.cycle.alerts_by_event_type.get(event.event_type, 0)
        if current_for_type >= self.config.max_alerts_per_event_type_per_cycle:
            reason = (
                "alert suppressed by per-event-type alert limit: "
                f"event_type={event.event_type}, fingerprint={event.fingerprint}"
            )
            self.cycle.suppressed_alerts.append(reason)
            return RateLimitDecision(False, reason)

        cooldown = self._cooldown_decision(
            registry=self._last_alert_by_fingerprint,
            fingerprint=event.fingerprint,
            cooldown_seconds=self.config.alert_fingerprint_cooldown_seconds,
            label="alert",
            event=event,
        )
        if not cooldown.allowed:
            self.cycle.suppressed_alerts.append(cooldown.reason)
            return cooldown

        self.cycle.alerts_reserved += 1
        self.cycle.alerts_by_event_type[event.event_type] = current_for_type + 1
        self._last_alert_by_fingerprint[event.fingerprint] = self.clock()
        self._alerted_fingerprints.add(event.fingerprint)
        return RateLimitDecision(True)

    def _cooldown_decision(
            self,
            registry: dict[str, float],
            fingerprint: str,
            cooldown_seconds: int,
            label: str,
            event: ErrorEvent,
    ) -> RateLimitDecision:
        if cooldown_seconds <= 0:
            return RateLimitDecision(True)

        last_seen = registry.get(fingerprint)
        if last_seen is None:
            return RateLimitDecision(True)

        elapsed = self.clock() - last_seen
        if elapsed >= cooldown_seconds:
            return RateLimitDecision(True)

        reason = (
            f"{label} suppressed by fingerprint cooldown: "
            f"event_type={event.event_type}, fingerprint={fingerprint}, "
            f"cooldown_seconds={cooldown_seconds}, elapsed_seconds={elapsed:.3f}"
        )
        return RateLimitDecision(False, reason)
