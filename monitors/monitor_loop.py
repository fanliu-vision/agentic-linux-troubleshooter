from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from detectors import ErrorEventDetector, ErrorEvent
from monitors.log_watcher import (
    LocalLogWatcher,
    RemoteLogWatcher,
    RemoteLogWatermarkStore,
)
from monitors.project_registry import ProjectConfig
from sessions import TroubleshootingSession
from recovery import AutoRecoveryRunner
from notifiers import NotificationManager
from monitors.daemon_logger import DaemonLogger
from monitors.health_checker import ProjectHealthChecker
from monitors.rate_limit_tracker import RateLimitTracker
from monitors.state_store import MonitorStateStore, ProjectMonitorState
from monitors.cycle_summary_reporter import CycleEventRecord, CycleSummaryReporter


@dataclass
class MonitorRunResult:
    project_id: str
    events_detected: int
    reports_generated: list[str]


class MonitorLoop:
    """
    Stage 6 monitor and auto-recovery loop.

    It polls local or remote logs, detects error events, runs diagnosis/reporting,
    and delegates recovery decisions to AutoRecoveryRunner. Only safe_auto_recover
    candidates may execute recovery after project policy, runtime gate, precheck,
    cooldown, rollback, and audit checks; high-risk events remain manual/report-only.
    """

    MAX_EVENTS_PER_CYCLE = 3
    MAX_AUTO_RECOVER_PER_CYCLE = 1

    def __init__(
            self,
            project: ProjectConfig,
            agent_depth: str = "balanced",
            report_mode: str = "rule",
            output_root: str = "outputs/monitors",
            state_dir: str = "state",
            daemon_log_path: str | None = None,
            heartbeat_interval: int = 60,
            health_check_interval: int = 300,
            enable_persistent_state: bool = True,
    ) -> None:
        self.project = project
        self.agent_depth = agent_depth
        self.report_mode = report_mode
        self.output_root = Path(output_root)
        self.output_root.mkdir(parents=True, exist_ok=True)

        self.detector = ErrorEventDetector()
        self.seen_fingerprints: set[str] = set()
        self.reports_generated: list[str] = []

        self.state_dir = state_dir
        self.heartbeat_interval = heartbeat_interval
        self.health_check_interval = health_check_interval
        self.enable_persistent_state = enable_persistent_state

        self.state_store = MonitorStateStore(
            project_id=self.project.project_id,
            state_dir=self.state_dir,
        )

        if self.enable_persistent_state:
            self.project_state: ProjectMonitorState = self.state_store.load()
            self.seen_fingerprints.update(self.project_state.seen_fingerprints)
        else:
            # Stage 6E: --no-persistent-state 时不加载历史状态，
            # 避免 run_count / idle_cycles / seen_fingerprints 受旧状态影响。
            self.project_state = ProjectMonitorState(project_id=self.project.project_id)

        self.project_state.mode = self.project.mode

        self.session = self._build_session()
        self.watcher = self._build_watcher()
        self.rate_limit_tracker = RateLimitTracker()
        self.recovery_runner = AutoRecoveryRunner(
            project=self.project,
            session=self.session,
        )
        self.notification_manager = NotificationManager(
            project=self.project,
            rate_limit_tracker=self.rate_limit_tracker,
        )

        if daemon_log_path is None:
            daemon_log_path = str(
                Path(self.state_dir) / self.project.project_id / "daemon.log"
            )

        self.daemon_logger = DaemonLogger(daemon_log_path)
        self.health_checker = ProjectHealthChecker(project=self.project)
        self.cycle_summary_reporter = CycleSummaryReporter(project=self.project)

        self._last_heartbeat_ts = 0.0
        self._last_health_check_ts = 0.0
        self._stop_requested = False
        self._daemon_started_ts = self._now_ts()
        self._active_cycle_started_ts: float | None = None
        self._active_cycle_started_at = ""
        self._current_cycle_health_status = "ok"
        self._current_cycle_health_message = "monitor cycle completed"
        self._current_cycle_last_error = ""
        self._current_cycle_last_exception_type = ""
        self._current_cycle_llm_fallback_used = False
        self._current_remote_log_metrics = self._empty_remote_log_metrics()
        self._remote_log_notice_last_ts: dict[str, float] = {}
        self._remote_log_notice_interval_seconds = 300

        self.daemon_logger.info(
            f"MonitorLoop initialized: project_id={self.project.project_id}, "
            f"mode={self.project.mode}, persistent_seen={len(self.seen_fingerprints)}"
        )

    def run_forever(self, max_idle_cycles: int | None = None) -> None:
        print(self._banner())
        self.run_daemon(
            max_cycles=None,
            max_idle_cycles=max_idle_cycles,
        )

    def run_for_cycles(self, cycles: int) -> MonitorRunResult:
        print(self._banner())

        events_before = self.project_state.events_detected_total
        reports_before = len(self.reports_generated)

        self.run_daemon(
            max_cycles=cycles,
            max_idle_cycles=None,
        )

        events_after = self.project_state.events_detected_total

        return MonitorRunResult(
            project_id=self.project.project_id,
            events_detected=events_after - events_before,
            reports_generated=self.reports_generated[reports_before:],
        )

    def _append_report_paths(self, paths: list[str]) -> None:
        for path in paths:
            if path and path not in self.reports_generated:
                self.reports_generated.append(path)

    def _expected_reports_for_event(self) -> int:
        return 2 if self.project.monitor.auto_report else 1

    def _count_alert_results(self, notify_results: list[str]) -> int:
        count = 0

        for item in notify_results:
            if item.startswith("[Notifier][RateLimit]"):
                continue
            if item == "[Notifier] notification skipped by project notification policy.":
                continue
            count += 1

        return count


    def _select_events_for_cycle(self, events: list[ErrorEvent]) -> list[ErrorEvent]:
        """
        Stage 6E: 一个日志轮询周期里可能出现多个错误。
        这里按 event_type + fingerprint 聚合，保留同一 event_type 的不同实例，
        避免旧 fingerprint 抢占同窗口中新出现的同类故障。
        """
        if not events:
            return []

        severity_rank = {
            "critical": 5,
            "high": 4,
            "medium": 3,
            "low": 2,
            "info": 1,
        }

        priority = {
            "disk_full": 10,
            "gpu_oom": 20,
            "slurm": 30,
            "python_env": 40,
            "network_port": 50,
        }

        selected_by_identity: dict[tuple[str, str], ErrorEvent] = {}

        for event in events:
            key = (event.event_type, event.fingerprint)

            if key not in selected_by_identity:
                selected_by_identity[key] = event
                continue

            old = selected_by_identity[key]
            old_score = severity_rank.get(old.severity, 0)
            new_score = severity_rank.get(event.severity, 0)

            if new_score > old_score:
                selected_by_identity[key] = event

        selected = list(selected_by_identity.values())

        selected.sort(
            key=lambda event: (
                event.fingerprint in self.seen_fingerprints,
                priority.get(event.event_type, 999),
                -severity_rank.get(event.severity, 0),
                event.line_number,
            )
        )

        max_events = min(
            self.project.monitor.max_events_per_run,
            self.MAX_EVENTS_PER_CYCLE,
        )

        return selected[:max_events]

    def _detect_events_for_chunk(self, text: str, source: str) -> list[ErrorEvent]:
        detect_all = getattr(self.detector, "detect_all", None)

        if callable(detect_all):
            return list(detect_all(text=text, source=source) or [])

        return list(self.detector.detect(text=text, source=source) or [])

    def _is_auto_recover_candidate(self, event: ErrorEvent) -> bool:
        runner_candidate = getattr(self.recovery_runner, "is_auto_recover_candidate", None)
        if callable(runner_candidate):
            try:
                return bool(runner_candidate(event))
            except Exception as exc:
                self.daemon_logger.warning(
                    f"failed to pre-check R15 auto recovery candidate: "
                    f"fingerprint={event.fingerprint}, event_type={event.event_type}, "
                    f"error={type(exc).__name__}: {exc}"
                )
                return False

        policy = getattr(self.recovery_runner, "policy", None)
        if policy is None or not hasattr(policy, "decide"):
            return False

        try:
            decision = policy.decide(event=event, project=self.project)
        except Exception as exc:
            self.daemon_logger.warning(
                f"failed to pre-check auto recovery candidate: "
                f"fingerprint={event.fingerprint}, event_type={event.event_type}, "
                f"error={type(exc).__name__}: {exc}"
            )
            return False

        return bool(decision.is_auto_recover)

    def _write_cycle_summary_report(
            self,
            records: list[CycleEventRecord],
    ) -> str:
        output_dir = self._resolve_cycle_report_dir(records)

        summary_path = self.cycle_summary_reporter.write_report(
            records=records,
            output_dir=output_dir,
        )

        overall_status = self.cycle_summary_reporter.compute_overall_status(records)

        self.daemon_logger.info(
            f"cycle summary report generated: overall_status={overall_status}, path={summary_path}"
        )

        print("")
        print("=" * 100)
        print("[Report] Stage 6 multi-event cycle summary")
        print("=" * 100)
        print(f"overall_status: {overall_status}")
        print(f"events_total: {len(records)}")
        print(f"summary_report: {summary_path}")
        print("=" * 100)

        return summary_path

    def _resolve_cycle_report_dir(
            self,
            records: list[CycleEventRecord],
    ) -> Path:
        for record in records:
            for path in record.report_paths:
                if path:
                    return Path(path).parent

        return self.output_root / self.project.project_id

    def _mark_event_seen(self, event: ErrorEvent) -> None:
        self.seen_fingerprints.add(event.fingerprint)

        if not self.enable_persistent_state:
            return

        self.project_state.seen_fingerprints = sorted(self.seen_fingerprints)
        self.project_state.events_detected_total += 1
        self.project_state.last_event_type = event.event_type
        self.project_state.last_issue_type = event.issue_type
        self.project_state.last_event_fingerprint = event.fingerprint
        self.state_store.save(self.project_state)

        self.state_store.append_event(
            {
                "event_type": event.event_type,
                "issue_type": event.issue_type,
                "severity": event.severity,
                "summary": event.summary,
                "source": event.source,
                "fingerprint": event.fingerprint,
            }
        )

    def request_stop(self, reason: str = "stop_requested") -> None:
        self._stop_requested = True
        self.project_state.status = reason

        if self.enable_persistent_state:
            self.state_store.save(self.project_state)

        self.daemon_logger.warning(f"stop requested: reason={reason}")

    def _sleep_with_stop(self, seconds: int) -> None:
        end_time = time.time() + seconds

        while time.time() < end_time:
            if self._stop_requested:
                return

            time.sleep(min(1, max(0, end_time - time.time())))

    def _begin_cycle_health(self) -> None:
        self.rate_limit_tracker.begin_cycle()

        started_ts = self._now_ts()
        started_at = self._now_text()

        self._active_cycle_started_ts = started_ts
        self._active_cycle_started_at = started_at
        self._current_cycle_health_status = "ok"
        self._current_cycle_health_message = "monitor cycle completed"
        self._current_cycle_last_error = ""
        self._current_cycle_last_exception_type = ""
        self._current_cycle_llm_fallback_used = False
        self._current_remote_log_metrics = self._empty_remote_log_metrics()

        runtime_health = dict(self.project_state.runtime_health or {})
        runtime_health.update(
            {
                "last_cycle_started_at": started_at,
                "daemon_pid": os.getpid(),
                "daemon_uptime_seconds": round(
                    max(0.0, started_ts - self._daemon_started_ts),
                    3,
                ),
                "health_status": "running",
                "health_message": "monitor cycle in progress",
            }
        )
        self.project_state.runtime_health = runtime_health

        if self.enable_persistent_state:
            self.state_store.save(self.project_state)

    def _record_cycle_degraded(self, exc: Exception, message: str) -> None:
        self._current_cycle_health_status = "degraded"
        self._current_cycle_health_message = message
        self._current_cycle_last_error = str(exc)
        self._current_cycle_last_exception_type = type(exc).__name__

    def _record_cycle_degraded_text(
            self,
            error: str,
            exception_type: str,
            message: str,
    ) -> None:
        self._current_cycle_health_status = "degraded"
        self._current_cycle_health_message = message
        self._current_cycle_last_error = error
        self._current_cycle_last_exception_type = exception_type

    def _record_llm_fallback_from_source(self, source: str) -> None:
        if "fallback" in source.lower():
            self._current_cycle_llm_fallback_used = True

    def _record_llm_fallback_from_messages(self, messages: list[str]) -> None:
        for message in messages:
            if "fallback" in message.lower():
                self._current_cycle_llm_fallback_used = True
                return

    def _finish_cycle_health(
            self,
            events_detected: int,
            reports_generated: int,
            alerts_generated: int,
    ) -> None:
        finished_ts = self._now_ts()
        finished_at = self._now_text()
        started_ts = self._active_cycle_started_ts or finished_ts
        started_at = self._active_cycle_started_at or finished_at

        runtime_health = dict(self.project_state.runtime_health or {})
        runtime_health.update(
            {
                "last_cycle_started_at": started_at,
                "last_cycle_finished_at": finished_at,
                "last_cycle_duration_seconds": round(
                    max(0.0, finished_ts - started_ts),
                    3,
                ),
                "last_events_detected": int(events_detected),
                "last_reports_generated": int(reports_generated),
                "last_alerts_generated": int(alerts_generated),
                "last_error": self._current_cycle_last_error,
                "last_exception_type": self._current_cycle_last_exception_type,
                "daemon_pid": os.getpid(),
                "daemon_uptime_seconds": round(
                    max(0.0, finished_ts - self._daemon_started_ts),
                    3,
                ),
                "llm_fallback_used": bool(self._current_cycle_llm_fallback_used),
                "health_status": self._current_cycle_health_status,
                "health_message": self._current_cycle_health_message,
                "rate_limited_events": len(
                    self.rate_limit_tracker.cycle.suppressed_events
                ),
                "rate_limited_reports": len(
                    self.rate_limit_tracker.cycle.suppressed_reports
                ),
                "rate_limited_alerts": len(
                    self.rate_limit_tracker.cycle.suppressed_alerts
                ),
                **self._current_remote_log_metrics,
            }
        )
        self.project_state.runtime_health = runtime_health

        if self.enable_persistent_state:
            self.state_store.save(self.project_state)

    @staticmethod
    def _empty_remote_log_metrics() -> dict[str, Any]:
        return {
            "remote_log_strategy_counts": {},
            "remote_log_fallback_count": 0,
            "remote_log_bytes_read": 0,
            "remote_log_watermark_errors": [],
        }

    def _capture_remote_log_observability(self) -> None:
        metrics = self._empty_remote_log_metrics()
        watcher_metrics = getattr(self.watcher, "last_poll_metrics", None)

        if isinstance(watcher_metrics, dict):
            strategy_counts = watcher_metrics.get("remote_log_strategy_counts") or {}
            if isinstance(strategy_counts, dict):
                metrics["remote_log_strategy_counts"] = dict(strategy_counts)

            metrics["remote_log_fallback_count"] = int(
                watcher_metrics.get("remote_log_fallback_count") or 0
            )
            metrics["remote_log_bytes_read"] = int(
                watcher_metrics.get("remote_log_bytes_read") or 0
            )

            errors = watcher_metrics.get("remote_log_watermark_errors") or []
            if isinstance(errors, list):
                metrics["remote_log_watermark_errors"] = list(errors)

        self._current_remote_log_metrics = metrics

    def _log_remote_log_notices(self) -> None:
        notices = getattr(self.watcher, "last_poll_notices", None)
        if not isinstance(notices, list):
            return

        now = self._now_ts()
        for notice in notices:
            if not isinstance(notice, dict):
                continue

            kind = str(notice.get("kind", "remote_log_notice"))
            path = str(notice.get("path", ""))
            reason = str(notice.get("reason", ""))
            strategy = str(notice.get("strategy", ""))
            key = f"{kind}:{path}:{reason}:{strategy}"
            last_ts = self._remote_log_notice_last_ts.get(key, 0.0)
            if now - last_ts < self._remote_log_notice_interval_seconds:
                continue

            self._remote_log_notice_last_ts[key] = now
            message = (
                "remote log watermark notice: "
                f"kind={kind}, path={path}, reason={reason}, strategy={strategy}"
            )
            self.daemon_logger.warning(message)


    def _handle_event(self, event: ErrorEvent) -> CycleEventRecord | None:
        print("")
        print("=" * 100)
        print("[ALERT] Error event detected")
        print("=" * 100)
        print(f"project_id: {self.project.project_id}")
        print(f"event_type: {event.event_type}")
        print(f"issue_type: {event.issue_type}")
        print(f"severity: {event.severity}")
        print(f"summary: {event.summary}")
        print(f"source: {event.source}")
        print(f"fingerprint: {event.fingerprint}")
        print("=" * 100)

        event_evidence_start = len(self.session.evidence_items)

        self.session.add_evidence(
            content=event.to_evidence_text(),
            source="monitor_error_event",
            title=f"Monitor detected event: {event.event_type}",
            issue_type=event.issue_type,
        )

        print("[Diagnosis] route updated:")
        print(self.session.initial_diagnosis_summary())

        # 只要进入监控事件，就统一进入 AutoRecoveryRunner。
        # 最终是否执行由 registry domain policy、project policy overlay
        # 和 runtime gate 共同裁决。
        recovery_result = self.recovery_runner.recover(event)
        self._record_llm_fallback_from_messages(recovery_result.messages)
        self._append_report_paths(recovery_result.report_paths)
        if self.enable_persistent_state:
            self.project_state.reports_generated_total += len(recovery_result.report_paths)
            if recovery_result.report_paths:
                self.project_state.last_report_path = recovery_result.report_paths[-1]
            self.project_state.status = (
                "recovered" if recovery_result.recovered else recovery_result.decision.action
            )
            self.state_store.save(self.project_state)

        print("")
        print("=" * 100)
        print("[Recovery] Stage 6C recovery result")
        print("=" * 100)
        print(f"event_type: {recovery_result.event_type}")
        print(f"issue_type: {recovery_result.issue_type}")
        print(f"action: {recovery_result.decision.action}")
        print(f"fix_id: {recovery_result.decision.fix_id or '<none>'}")
        print(f"apply_success: {recovery_result.apply_success}")
        print(f"rerun_success: {recovery_result.rerun_success}")
        print(f"rollback_executed: {recovery_result.rollback_executed}")
        print(f"recovered: {recovery_result.recovered}")
        print("reports:")
        for path in recovery_result.report_paths:
            print(f"- {path}")
        print("=" * 100)

        notify_results = self.notification_manager.notify_recovery(
            event=event,
            recovery_result=recovery_result,
        )
        for notify_result in notify_results:
            if "[ERROR]" in notify_result:
                self._record_cycle_degraded_text(
                    error=notify_result,
                    exception_type="NotificationError",
                    message="notification dispatch reported an error; daemon continued",
                )
                break

        notification_status = (
            "recovered"
            if recovery_result.recovered
            else (
                "rollback_done"
                if recovery_result.rollback_executed
                else recovery_result.decision.action
            )
        )

        notification_text = "\n".join(notify_results)

        if hasattr(self.session, "record_notification_result"):
            self.session.record_notification_result(
                result_text=notification_text,
                status=notification_status,
                channels=self.project.notification.channels,
            )

        if self.enable_persistent_state:
            # 这里只统计实际 dispatch 返回的结果条数，限流和策略 skip 不计入 alert 产出。
            self.project_state.notifications_sent_total += self._count_alert_results(
                notify_results
            )
            self.state_store.save(self.project_state)

        print("")
        print("=" * 100)
        print("[Notifier] Stage 6D notification result")
        print("=" * 100)
        for item in notify_results:
            print(item)
        print("=" * 100)

        post_notification_report_paths: list[str] = []

        if self.project.monitor.auto_report:
            try:
                from datetime import datetime
                from pathlib import Path
                import shutil

                report, save_path, source = self.session.generate_report(
                    report_intent="post_notification",
                    evidence_items=self.session.evidence_items[event_evidence_start:],
                )
                self._record_llm_fallback_from_source(source)
                save_path = Path(save_path)

                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                post_report_path = save_path.with_name(
                    f"event_{timestamp}_{event.event_type}_post_notification_{save_path.name}"
                )

                shutil.copyfile(save_path, post_report_path)

                post_notification_report_paths.append(str(post_report_path))
                self._append_report_paths([str(post_report_path)])

                if str(post_report_path) not in recovery_result.report_paths:
                    recovery_result.report_paths.append(str(post_report_path))

                if self.enable_persistent_state:
                    self.project_state.reports_generated_total += 1
                    self.project_state.last_report_path = str(post_report_path)
                    self.state_store.save(self.project_state)

                self.daemon_logger.info(
                    f"post-notification report generated by {source}: {post_report_path}"
                )

            except Exception as exc:
                self._record_cycle_degraded(
                    exc,
                    "post-notification report generation failed; daemon continued",
                )
                self.daemon_logger.warning(
                    f"failed to generate post-notification report: "
                    f"{type(exc).__name__}: {exc}"
                )

        record = CycleEventRecord(
            event_type=event.event_type,
            issue_type=event.issue_type,
            severity=event.severity,
            summary=event.summary,
            source=event.source,
            fingerprint=event.fingerprint,
            action=recovery_result.decision.action,
            fix_id=recovery_result.decision.fix_id or "",
            apply_success=recovery_result.apply_success,
            rerun_success=recovery_result.rerun_success,
            rollback_executed=recovery_result.rollback_executed,
            rollback_success=recovery_result.rollback_success,
            recovered=recovery_result.recovered,
            notification_status=notification_status,
            notification_channels=list(self.project.notification.channels),
            notification_results=list(notify_results),
            report_paths=list(recovery_result.report_paths),
            recovery_audit_summary=recovery_result.recovery_audit_summary(),
        )

        return record

    def run_once(self) -> list[ErrorEvent]:
        self._begin_cycle_health()
        reports_before = len(self.reports_generated)
        alerts_before = self.project_state.notifications_sent_total

        detected_events: list[ErrorEvent] = []
        cycle_records: list[CycleEventRecord] = []
        candidate_events: list[ErrorEvent] = []
        failed_events = 0
        auto_recover_candidates_handled = 0

        try:
            chunks = self.watcher.poll()
            self._capture_remote_log_observability()
            self._log_remote_log_notices()

            if not chunks:
                print("[Monitor][WARN] no log chunks read.")
                print(
                    "[Monitor][WARN] This may mean log_files do not exist, "
                    "are empty, or watcher starts from EOF."
                )
                self.daemon_logger.warning(
                    "no log chunks read; log_files may not exist, be empty, or watcher starts from EOF."
                )

            for chunk in chunks:
                events = self._detect_events_for_chunk(
                    text=chunk.content,
                    source=f"{chunk.source}:{chunk.path}",
                )
                candidate_events.extend(events)

            events_to_handle = self._select_events_for_cycle(candidate_events)

            for event in events_to_handle:
                if event.fingerprint in self.seen_fingerprints:
                    self.daemon_logger.info(
                        f"event skipped because fingerprint already seen: "
                        f"event_type={event.event_type}, fingerprint={event.fingerprint}"
                    )
                    continue

                event_limit = self.rate_limit_tracker.allow_event(event)
                if not event_limit.allowed:
                    self.daemon_logger.warning(event_limit.reason)
                    continue

                is_auto_recover_candidate = self._is_auto_recover_candidate(event)
                if (
                    is_auto_recover_candidate
                    and auto_recover_candidates_handled >= self.MAX_AUTO_RECOVER_PER_CYCLE
                ):
                    # R10-3b safety guard: delay extra auto-recover candidates rather
                    # than allowing one monitor cycle to execute multiple fixes.
                    self.daemon_logger.warning(
                        f"auto recovery candidate skipped by per-cycle limit: "
                        f"fingerprint={event.fingerprint}, event_type={event.event_type}"
                    )
                    continue

                if is_auto_recover_candidate:
                    auto_recover_candidates_handled += 1

                report_limit = self.rate_limit_tracker.reserve_report_capacity(
                    event,
                    expected_reports=self._expected_reports_for_event(),
                )
                if not report_limit.allowed:
                    self.daemon_logger.warning(report_limit.reason)
                    continue

                self.rate_limit_tracker.record_event_handled()

                try:
                    record = self._handle_event(event)
                except Exception as exc:
                    failed_events += 1
                    self._record_cycle_degraded(
                        exc,
                        "event handling failed; daemon continued",
                    )
                    self.daemon_logger.warning(
                        f"event handling failed; fingerprint will not be marked seen: "
                        f"fingerprint={event.fingerprint}, event_type={event.event_type}, "
                        f"error={type(exc).__name__}: {exc}"
                    )
                    continue

                if record is not None:
                    self._mark_event_seen(event)
                    detected_events.append(event)
                    cycle_records.append(record)

            if failed_events and not detected_events:
                print("[Monitor][WARN] error events detected but handling failed.")
                self.project_state.status = "event_handling_failed"

                if self.enable_persistent_state:
                    self.state_store.save(self.project_state)
            elif not detected_events:
                print("[Monitor] no new error events.")
                self.project_state.idle_cycles += 1

                if self.project_state.last_health_status == "warning":
                    self.project_state.status = "health_warning"
                else:
                    self.project_state.status = "idle"

                if self.enable_persistent_state:
                    self.state_store.save(self.project_state)
            else:
                self.project_state.idle_cycles = 0
                if self.enable_persistent_state:
                    self.state_store.save(self.project_state)

            if cycle_records:
                try:
                    summary_limit = self.rate_limit_tracker.reserve_cycle_report()
                    if not summary_limit.allowed:
                        self.daemon_logger.warning(summary_limit.reason)
                        summary_path = ""
                    else:
                        summary_path = self._write_cycle_summary_report(cycle_records)

                    if summary_path:
                        # cycle summary 是确定性主报告，放到 reports_generated 最前面
                        if summary_path in self.reports_generated:
                            self.reports_generated.remove(summary_path)

                        self.reports_generated.insert(0, summary_path)

                        if self.enable_persistent_state:
                            self.project_state.reports_generated_total += 1
                            self.project_state.last_report_path = summary_path
                            self.state_store.save(self.project_state)
                except Exception as exc:
                    self._record_cycle_degraded(
                        exc,
                        "cycle summary report generation failed; daemon continued",
                    )
                    self.daemon_logger.warning(
                        f"failed to generate cycle summary report: "
                        f"{type(exc).__name__}: {exc}"
                    )

            self._finish_cycle_health(
                events_detected=len(detected_events),
                reports_generated=max(0, len(self.reports_generated) - reports_before),
                alerts_generated=max(
                    0,
                    self.project_state.notifications_sent_total - alerts_before,
                ),
            )

            return detected_events

        except Exception as exc:
            self._record_cycle_degraded(
                exc,
                "monitor cycle raised an exception",
            )
            self._finish_cycle_health(
                events_detected=len(detected_events),
                reports_generated=max(0, len(self.reports_generated) - reports_before),
                alerts_generated=max(
                    0,
                    self.project_state.notifications_sent_total - alerts_before,
                )
            )
            raise


    def run_daemon(
            self,
            max_cycles: int | None = None,
            max_idle_cycles: int | None = None,
    ) -> None:
        """
        Stage 6E Python 级守护模式。

        max_cycles=None 表示无限运行。
        max_idle_cycles=None 表示不因空闲退出。

        注意：
        - run_count 是历史累计轮数；
        - local_cycles 是本进程轮数；
        - idle_cycles 是历史累计空闲轮数；
        - local_idle_cycles 是本进程连续空闲轮数，用于 max_idle_cycles 判断。
        """

        local_cycles = 0
        local_idle_cycles = 0

        self.project_state.status = "running"
        self.project_state.mode = self.project.mode

        if self.enable_persistent_state:
            self.state_store.save(self.project_state)

        self.daemon_logger.info(
            f"Stage 6E daemon started: project_id={self.project.project_id}, "
            f"max_cycles={max_cycles}, max_idle_cycles={max_idle_cycles}"
        )


        try:
            while True:
                if self._stop_requested:
                    self.daemon_logger.info("stop requested, exiting daemon loop.")
                    break

                if max_cycles is not None and local_cycles >= max_cycles:
                    self.daemon_logger.info(
                        f"max_cycles reached in current process: {local_cycles}"
                    )
                    break

                if max_idle_cycles is not None and local_idle_cycles >= max_idle_cycles:
                    self.daemon_logger.info(
                        f"max_idle_cycles reached in current process: {local_idle_cycles}"
                    )
                    break

                self._maybe_heartbeat()
                self._maybe_health_check()

                local_cycles += 1
                self.project_state.run_count += 1

                if self.enable_persistent_state:
                    self.state_store.save(self.project_state)

                self.daemon_logger.info(
                    f"monitor cycle started: "
                    f"local_cycles={local_cycles}, "
                    f"run_count_total={self.project_state.run_count}"
                )

                detected_events = self.run_once()

                if detected_events:
                    local_idle_cycles = 0
                else:
                    local_idle_cycles += 1

                self.daemon_logger.info(
                    f"monitor cycle finished: "
                    f"events_detected={len(detected_events)}, "
                    f"local_idle_cycles={local_idle_cycles}"
                )

                self._sleep_with_stop(self.project.monitor.interval_seconds)

        except KeyboardInterrupt:
            self._stop_requested = True
            self.project_state.status = "stopped_by_keyboard_interrupt"

            if self.enable_persistent_state:
                self.state_store.save(self.project_state)

            self.daemon_logger.warning("daemon stopped by Ctrl+C.")

        except Exception as exc:
            self.project_state.status = "error"

            if self.enable_persistent_state:
                self.state_store.save(self.project_state)

            self.daemon_logger.error(
                f"daemon crashed: {type(exc).__name__}: {exc}"
            )
            raise

        finally:
            if self.project_state.status not in {
                "stopped_by_keyboard_interrupt",
                "stopped_by_signal",
                "stop_requested",
                "error",
            }:
                self.project_state.status = "stopped"

                if self.enable_persistent_state:
                    self.state_store.save(self.project_state)

            self.daemon_logger.info(
                f"Stage 6E daemon finished: status={self.project_state.status}, "
                f"local_cycles={local_cycles}, "
                f"run_count_total={self.project_state.run_count}, "
                f"events_total={self.project_state.events_detected_total}"
            )


    def _now_ts(self) -> float:
        return time.time()

    def _now_text(self) -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _maybe_heartbeat(self) -> None:
        now = self._now_ts()

        if now - self._last_heartbeat_ts < self.heartbeat_interval:
            return

        self._last_heartbeat_ts = now

        self.project_state.last_heartbeat_at = self._now_text()
        self.project_state.status = (
            self.project_state.status if self.project_state.status else "running"
        )

        if self.enable_persistent_state:
            self.state_store.save(self.project_state)

        self.daemon_logger.heartbeat(
            f"project_id={self.project.project_id}, "
            f"status={self.project_state.status}, "
            f"run_count={self.project_state.run_count}, "
            f"seen_fingerprints={len(self.seen_fingerprints)}, "
            f"events_total={self.project_state.events_detected_total}"
        )

    def _maybe_health_check(self) -> None:
        now = self._now_ts()

        if now - self._last_health_check_ts < self.health_check_interval:
            return

        self._last_health_check_ts = now

        result = self.health_checker.check()

        self.project_state.last_health_check_at = self._now_text()
        self.project_state.last_health_status = result.status
        self.project_state.last_health_message = result.message

        if self.enable_persistent_state:
            self.state_store.save(self.project_state)

        if result.ok:
            self.daemon_logger.info(f"health_check ok: {result.message}")
        else:
            self.project_state.status = "health_warning"
            if self.enable_persistent_state:
                self.state_store.save(self.project_state)

            self.daemon_logger.warning(f"health_check {result.status}: {result.message}")

    def _build_session(self) -> TroubleshootingSession:
        session = TroubleshootingSession(
            output_root=str(self.output_root / self.project.project_id),
            agent_depth=self.agent_depth,
            report_mode=self.report_mode,
            project_dir=self.project.project_dir,
            run_command=self.project.run_command,
        )

        if self.project.is_remote:
            session.set_remote_profile(
                user=self.project.ssh.user,
                host=self.project.ssh.host,
                port=self.project.ssh.port,
            )

        return session

    def _build_remote_log_watermark_store(self) -> RemoteLogWatermarkStore:
        watermarks = self.project_state.remote_log_watermarks

        def on_update(path: str, watermark: dict[str, Any]) -> None:
            self.project_state.remote_log_watermarks[path] = dict(watermark)
            if self.enable_persistent_state:
                self.state_store.save(self.project_state)

        return RemoteLogWatermarkStore(
            watermarks=watermarks,
            on_update=on_update,
        )

    def _build_watcher(self):
        if self.project.is_remote:
            return RemoteLogWatcher(
                log_files=self.project.log_files,
                session=self.session,
                tail_lines=self.project.monitor.tail_lines,
                max_bytes_per_poll=self.project.monitor.remote_log_max_bytes_per_poll,
                watermark_store=self._build_remote_log_watermark_store(),
                watermark_enabled=self.project.monitor.remote_watermark_enabled,
                shadow_mode=self.project.monitor.remote_watermark_shadow,
            )

        return LocalLogWatcher(
            log_files=self.project.log_files,
            project_dir=self.project.project_dir,
        )

    def _banner(self) -> str:
        lines = [
            "=" * 100,
            "Stage 6 Monitor Loop Started",
            "=" * 100,
            f"project_id: {self.project.project_id}",
            f"name: {self.project.name}",
            f"mode: {self.project.mode}",
            f"owner: {self.project.owner}",
            f"run_command: {self.project.run_command}",
            f"log_files: {self.project.log_files}",
            f"interval_seconds: {self.project.monitor.interval_seconds}",
            f"auto_report: {self.project.monitor.auto_report}",
            "-" * 100,
            "Stage 6E daemon/state settings",
            f"state_dir: {self.state_dir}",
            f"persistent_state: {self.enable_persistent_state}",
            f"heartbeat_interval: {self.heartbeat_interval}",
            f"health_check_interval: {self.health_check_interval}",
            f"seen_fingerprints_loaded: {len(self.seen_fingerprints)}",
            "=" * 100,
        ]

        return "\n".join(lines)
