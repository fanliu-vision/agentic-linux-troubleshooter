from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from detectors import ErrorEvent
from monitors.project_registry import ProjectConfig


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
    recovered: bool = False

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
            "recovered": self.recovered,
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
            f"- recovered: `{self.recovered}`\n"
            f"- status: `{self.status}`\n\n"
            f"### 摘要\n\n{self.message}\n\n"
            f"### 报告路径\n\n{report_text}\n"
        )


class NotificationManager:
    def __init__(self, project: ProjectConfig) -> None:
        self.project = project

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
            recovered=recovery_result.recovered,
            report_paths=list(recovery_result.report_paths),
            status=status,
        )

        message.message = self._summary_text(message)
        return message

    def notify_recovery(self, event: ErrorEvent, recovery_result) -> list[str]:
        message = self.build_message_from_recovery(event, recovery_result)

        if not self.should_notify(message):
            return ["[Notifier] notification skipped by project notification policy."]

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
            return "rollback_done"

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

        if message.status == "manual_escalation":
            return (
                f"[ESCALATION] 项目 `{message.project_id}` 检测到 `{message.event_type}`，"
                "该问题不在自动修复范围内，请负责人处理。"
            )

        if message.status == "report_only":
            return (
                f"[REPORT_ONLY] 项目 `{message.project_id}` 检测到 `{message.event_type}`，"
                "系统已生成报告，但未执行自动修复。"
            )

        return (
            f"[UNRESOLVED] 项目 `{message.project_id}` 检测到 `{message.event_type}`，"
            "自动恢复未完成，请负责人检查。"
        )
