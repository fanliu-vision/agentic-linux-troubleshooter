from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from detectors import ErrorEvent
from monitors.project_registry import ProjectConfig
from monitors.rate_limit_tracker import RateLimitTracker


@dataclass
class NotificationMessage:
    project_id: str
    project_name: str
    owner: str
    owner_contact: str

    event_type: str
    issue_type: str
    severity: str
    event_summary: str

    action: str
    fix_id: str = ""
    apply_success: bool = False
    rerun_success: bool = False
    rollback_executed: bool = False
    rollback_success: bool = False
    recovered: bool = False

    strategy_layer: str = ""
    auto_recover_allowed: bool = False
    dry_run: bool = True
    would_execute: bool = False
    allowed_to_execute: bool = False
    downgrade_reason: str = ""
    operator_required: bool = False
    audit_required: bool = True
    forbidden_action: bool = False
    precheck_result: dict[str, Any] = field(default_factory=dict)
    cooldown_result: dict[str, Any] = field(default_factory=dict)
    rate_limit_result: dict[str, Any] = field(default_factory=dict)
    rollback_available: bool = False
    execution_result: str = ""
    rollback_result: str = ""
    recovery_audit_summary: dict[str, Any] = field(default_factory=dict)
    recovery_audit_record: dict[str, Any] = field(default_factory=dict)

    report_paths: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    status: str = "unknown"
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "created_at": self.created_at,
            "project_id": self.project_id,
            "project_name": self.project_name,
            "owner": self.owner,
            "owner_contact": self.owner_contact,
            "event_type": self.event_type,
            "issue_type": self.issue_type,
            "severity": self.severity,
            "event_summary": self.event_summary,
            "action": self.action,
            "fix_id": self.fix_id,
            "apply_success": self.apply_success,
            "rerun_success": self.rerun_success,
            "rollback_executed": self.rollback_executed,
            "rollback_success": self.rollback_success,
            "recovered": self.recovered,
            "strategy_layer": self.strategy_layer,
            "auto_recover_allowed": self.auto_recover_allowed,
            "dry_run": self.dry_run,
            "would_execute": self.would_execute,
            "allowed_to_execute": self.allowed_to_execute,
            "downgrade_reason": self.downgrade_reason,
            "operator_required": self.operator_required,
            "audit_required": self.audit_required,
            "forbidden_action": self.forbidden_action,
            "precheck_result": self.precheck_result,
            "cooldown_result": self.cooldown_result,
            "rate_limit_result": self.rate_limit_result,
            "rollback_available": self.rollback_available,
            "execution_result": self.execution_result,
            "rollback_result": self.rollback_result,
            "recovery_audit_summary": self.recovery_audit_summary,
            "recovery_audit_record": self.recovery_audit_record,
            "status": self.status,
            "message": self.message,
            "report_paths": self.report_paths,
        }

    def to_markdown(self) -> str:
        report_text = "\n".join(f"- `{path}`" for path in self.report_paths) or "- <none>"

        return (
            "## Stage 6D 通知消息\n\n"
            f"- created_at: `{self.created_at}`\n"
            f"- project_id: `{self.project_id}`\n"
            f"- project_name: `{self.project_name}`\n"
            f"- owner: `{self.owner}`\n"
            f"- owner_contact: `{self.owner_contact}`\n"
            f"- event_type: `{self.event_type}`\n"
            f"- issue_type: `{self.issue_type}`\n"
            f"- severity: `{self.severity}`\n"
            f"- action: `{self.action}`\n"
            f"- fix_id: `{self.fix_id if self.fix_id else '<none>'}`\n"
            f"- apply_success: `{self.apply_success}`\n"
            f"- rerun_success: `{self.rerun_success}`\n"
            f"- rollback_executed: `{self.rollback_executed}`\n"
            f"- rollback_success: `{self.rollback_success}`\n"
            f"- recovered: `{self.recovered}`\n"
            f"- status: `{self.status}`\n"
            f"- strategy_layer: `{self.strategy_layer or '<unknown>'}`\n"
            f"- auto_recover_allowed: `{self.auto_recover_allowed}`\n"
            f"- dry_run: `{self.dry_run}`\n"
            f"- would_execute: `{self.would_execute}`\n"
            f"- allowed_to_execute: `{self.allowed_to_execute}`\n"
            f"- downgrade_reason: `{self.downgrade_reason or '<none>'}`\n"
            f"- operator_required: `{self.operator_required}`\n"
            f"- audit_required: `{self.audit_required}`\n"
            f"- forbidden_action: `{self.forbidden_action}`\n"
            f"- execution_result: `{self.execution_result or '<unknown>'}`\n"
            f"- rollback_result: `{self.rollback_result or '<unknown>'}`\n\n"
            f"### 摘要\n\n{self.message}\n\n"
            f"### 报告路径\n\n{report_text}\n"
        )


class NotificationManager:
    def __init__(
            self,
            project: ProjectConfig,
            rate_limit_tracker: RateLimitTracker | None = None,
    ) -> None:
        self.project = project
        self.rate_limit_tracker = rate_limit_tracker

    def should_notify(self, message: NotificationMessage) -> bool:
        config = self.project.notification

        if not config.enabled:
            return False

        if message.recovered:
            return config.notify_on_recovered

        if message.rollback_executed:
            return config.notify_on_rollback

        if message.action == "manual_escalation":
            return config.notify_on_escalation

        if message.action == "report_only":
            return config.notify_on_report_only

        return True

    def build_message_from_recovery(self, event: ErrorEvent, recovery_result) -> NotificationMessage:
        status = self._status_from_recovery(recovery_result)
        audit_record = self._recovery_audit_record(recovery_result)
        audit_summary = self._recovery_audit_summary(recovery_result, audit_record)

        message = NotificationMessage(
            project_id=self.project.project_id,
            project_name=self.project.name,
            owner=self.project.owner,
            owner_contact=self.project.owner_contact,
            event_type=event.event_type,
            issue_type=getattr(event, "issue_type", event.event_type),
            severity=event.severity,
            event_summary=event.summary,
            action=recovery_result.decision.action,
            fix_id=recovery_result.decision.fix_id,
            apply_success=recovery_result.apply_success,
            rerun_success=recovery_result.rerun_success,
            rollback_executed=recovery_result.rollback_executed,
            rollback_success=bool(getattr(recovery_result, "rollback_success", False)),
            recovered=recovery_result.recovered,
            strategy_layer=str(audit_record.get("strategy_layer", "")),
            auto_recover_allowed=bool(audit_record.get("auto_recover_allowed", False)),
            dry_run=bool(audit_record.get("dry_run", True)),
            would_execute=bool(audit_record.get("would_execute", False)),
            allowed_to_execute=bool(audit_record.get("allowed_to_execute", False)),
            downgrade_reason=str(audit_record.get("downgrade_reason", "")),
            operator_required=bool(audit_record.get("operator_required", False)),
            audit_required=bool(audit_record.get("audit_required", True)),
            forbidden_action=bool(audit_record.get("forbidden_action", False)),
            precheck_result=dict(audit_record.get("precheck_result") or {}),
            cooldown_result=dict(audit_record.get("cooldown_result") or {}),
            rate_limit_result=dict(audit_record.get("rate_limit_result") or {}),
            rollback_available=bool(audit_record.get("rollback_available", False)),
            execution_result=str(audit_record.get("execution_result", "")),
            rollback_result=str(audit_record.get("rollback_result", "")),
            recovery_audit_summary=audit_summary,
            recovery_audit_record=audit_record,
            report_paths=list(recovery_result.report_paths),
            status=status,
        )

        message.message = self._summary_text(message)
        return message

    def _recovery_audit_record(self, recovery_result) -> dict[str, Any]:
        getter = getattr(recovery_result, "recovery_audit_record", None)
        if callable(getter):
            return dict(getter())
        return dict(getattr(recovery_result, "recovery_audit_record", {}) or {})

    def _recovery_audit_summary(
        self,
        recovery_result,
        audit_record: dict[str, Any],
    ) -> dict[str, Any]:
        getter = getattr(recovery_result, "recovery_audit_summary", None)
        if callable(getter):
            return dict(getter())

        keys = [
            "strategy_layer",
            "action",
            "fix_id",
            "auto_recover_allowed",
            "dry_run",
            "would_execute",
            "allowed_to_execute",
            "downgrade_reason",
            "execution_result",
            "rollback_result",
            "operator_required",
            "forbidden_action",
            "recovered",
        ]
        return {key: audit_record.get(key) for key in keys}

    def notify_recovery(self, event: ErrorEvent, recovery_result) -> list[str]:
        message = self.build_message_from_recovery(event, recovery_result)

        if not self.should_notify(message):
            return ["[Notifier] notification skipped by project notification policy."]

        if self.rate_limit_tracker is not None:
            decision = self.rate_limit_tracker.reserve_alert_capacity(event)
            if not decision.allowed:
                return [f"[Notifier][RateLimit] {decision.reason}"]

        return self._dispatch(message)

    def _dispatch(self, message: NotificationMessage) -> list[str]:
        results: list[str] = []
        channels = list(dict.fromkeys(self.project.notification.channels))

        if not channels:
            return ["[Notifier] no notification channels enabled."]

        for channel in channels:
            if channel == "console":
                results.append(self._dispatch_console(message))
            elif channel == "file":
                results.append(self._dispatch_file(message))
            elif channel == "webhook":
                results.append(self._dispatch_webhook(message))
            else:
                results.append(
                    f"[Notifier][{channel}][WARN] unsupported notification channel."
                )

        return results

    def _dispatch_console(self, message: NotificationMessage) -> str:
        try:
            from notifiers.console_notifier import ConsoleNotifier

            return ConsoleNotifier().send(message)
        except Exception as exc:
            return f"[Notifier][Console][ERROR] {type(exc).__name__}: {exc}"

    def _dispatch_file(self, message: NotificationMessage) -> str:
        try:
            from notifiers.file_notifier import FileNotifier

            return FileNotifier(
                alerts_dir=self.project.notification.alerts_dir,
            ).send(message)
        except Exception as exc:
            return f"[Notifier][File][ERROR] {type(exc).__name__}: {exc}"

    def _dispatch_webhook(self, message: NotificationMessage) -> str:
        try:
            from notifiers.webhook_notifier import WebhookNotifier

            return WebhookNotifier(
                webhook_url=self.project.notification.webhook_url,
                webhook_url_env=self.project.notification.webhook_url_env,
            ).send(message)
        except Exception as exc:
            return f"[Notifier][Webhook][ERROR] {type(exc).__name__}: {exc}"

    def _status_from_recovery(self, recovery_result) -> str:
        if recovery_result.recovered:
            return "recovered"

        if recovery_result.rollback_executed:
            if bool(getattr(recovery_result, "rollback_success", False)):
                return "rollback_done"
            return "rollback_failed"

        if recovery_result.decision.action == "manual_escalation":
            return "manual_escalation"

        if recovery_result.decision.action == "report_only":
            return "report_only"

        return "unresolved"

    def _summary_text(self, message: NotificationMessage) -> str:
        if message.status == "recovered":
            return (
                f"[RECOVERED] 项目 `{message.project_id}` 检测到 `{message.event_type}`，"
                f"已自动执行 `{message.fix_id}`，apply 和 rerun 均成功。"
            )

        if message.status == "rollback_done":
            return (
                f"[ROLLBACK] 项目 `{message.project_id}` 检测到 `{message.event_type}`，"
                f"自动修复未通过验证，已执行 rollback，请负责人检查报告。"
            )

        if message.status == "rollback_failed":
            return (
                f"[ROLLBACK_FAILED] 项目 `{message.project_id}` 检测到 `{message.event_type}`，"
                "自动修复未通过验证，且 rollback 失败，请负责人立即检查报告。"
            )

        if message.status == "manual_escalation":
            return (
                f"[ESCALATION] 项目 `{message.project_id}` 检测到 `{message.event_type}`，"
                "该问题不在自动修复范围内，请负责人处理。"
            )

        if message.status == "report_only":
            if message.dry_run and message.auto_recover_allowed:
                return (
                    f"[DRY_RUN] 项目 `{message.project_id}` 检测到 `{message.event_type}`，"
                    f"R15 gate 已识别 `{message.fix_id}`，但 dry_run=true，未执行自动修复。"
                )
            return (
                f"[REPORT_ONLY] 项目 `{message.project_id}` 检测到 `{message.event_type}`，"
                "系统已生成报告，但未执行自动修复。"
            )

        return (
            f"[UNRESOLVED] 项目 `{message.project_id}` 检测到 `{message.event_type}`，"
            "自动恢复未完成，请负责人检查。"
        )
