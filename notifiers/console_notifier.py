from __future__ import annotations

from notifiers.notification_manager import NotificationMessage


class ConsoleNotifier:
    def send(self, message: NotificationMessage) -> str:
        print("")
        print("=" * 100)
        print("[Notifier][Console] Stage 6D notification")
        print("=" * 100)
        print(f"status: {message.status}")
        print(f"project_id: {message.project_id}")
        print(f"owner: {message.owner}")
        print(f"owner_contact: {message.owner_contact}")
        print(f"event_type: {message.event_type}")
        print(f"issue_type: {message.issue_type}")
        print(f"severity: {message.severity}")
        print(f"action: {message.action}")
        print(f"fix_id: {message.fix_id if message.fix_id else '<none>'}")
        print(f"apply_success: {message.apply_success}")
        print(f"rerun_success: {message.rerun_success}")
        print(f"rollback_executed: {message.rollback_executed}")
        print(f"rollback_success: {message.rollback_success}")
        print(f"recovered: {message.recovered}")
        print(f"message: {message.message}")
        print("reports:")
        for path in message.report_paths:
            print(f"- {path}")
        print("=" * 100)

        return "[Notifier][Console] sent"
