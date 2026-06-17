from __future__ import annotations

import json
import os
import urllib.request
from urllib.error import URLError, HTTPError

from notifiers.notification_manager import NotificationMessage


class WebhookNotifier:
    def __init__(self, webhook_url: str = "", webhook_url_env: str = "") -> None:
        self.webhook_url = webhook_url
        self.webhook_url_env = webhook_url_env

    def send(self, message: NotificationMessage) -> str:
        url = self._resolve_url()

        if not url:
            return "[Notifier][Webhook] skipped: webhook url is not configured."

        payload = {
            "text": message.message,
            "status": message.status,
            "project_id": message.project_id,
            "event_type": message.event_type,
            "issue_type": message.issue_type,
            "severity": message.severity,
            "action": message.action,
            "fix_id": message.fix_id,
            "recovered": message.recovered,
            "report_paths": message.report_paths,
            "created_at": message.created_at,
        }

        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        req = urllib.request.Request(
            url=url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                return f"[Notifier][Webhook] sent, status={response.status}"
        except HTTPError as exc:
            return f"[Notifier][Webhook][ERROR] HTTP {exc.code}: {exc.reason}"
        except URLError as exc:
            return f"[Notifier][Webhook][ERROR] {exc.reason}"
        except Exception as exc:
            return f"[Notifier][Webhook][ERROR] {type(exc).__name__}: {exc}"

    def _resolve_url(self) -> str:
        if self.webhook_url:
            return self.webhook_url

        if self.webhook_url_env:
            return os.getenv(self.webhook_url_env, "")

        return ""