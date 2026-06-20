from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


class FileNotifier:
    """
    Stage 6D file notifier.

    输出三类文件：
    1. <project_id>_alerts.jsonl：历史通知记录，追加写入
    2. <project_id>_latest_alert.md：最新通知快照，覆盖写入
    3. <project_id>_alerts/<timestamp>_<event>_<status>_<uid>.md/json：每次通知独立归档
    """

    def __init__(self, alerts_dir: str = "outputs/alerts") -> None:
        self.alerts_dir = Path(alerts_dir)
        self.alerts_dir.mkdir(parents=True, exist_ok=True)

    def send(self, message) -> str:
        return self.notify(message)

    def notify(self, message) -> str:
        project_id = getattr(message, "project_id", "unknown_project")
        event_type = getattr(message, "event_type", "unknown_event")
        status = getattr(message, "status", "unknown")
        safe_project_id = self._safe_name(project_id)

        payload = self._message_to_dict(message)

        history_jsonl_path = self.alerts_dir / f"{safe_project_id}_alerts.jsonl"
        latest_md_path = self.alerts_dir / f"{safe_project_id}_latest_alert.md"

        archive_dir = self.alerts_dir / f"{safe_project_id}_alerts"
        archive_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        uid = uuid.uuid4().hex[:8]

        safe_event_type = self._safe_name(event_type)
        safe_status = self._safe_name(status)

        archive_base = f"{timestamp}_{safe_event_type}_{safe_status}_{uid}"
        archive_md_path = archive_dir / f"{archive_base}.md"
        archive_json_path = archive_dir / f"{archive_base}.json"

        markdown = self._message_to_markdown(message, payload)

        with history_jsonl_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")

        latest_md_path.write_text(markdown, encoding="utf-8")
        archive_md_path.write_text(markdown, encoding="utf-8")
        archive_json_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        return (
            "[Notifier][File] wrote "
            f"{history_jsonl_path} and {latest_md_path}; "
            f"archived {archive_md_path}"
        )

    def _message_to_dict(self, message) -> dict[str, Any]:
        if hasattr(message, "to_dict"):
            data = message.to_dict()
        elif is_dataclass(message):
            data = asdict(message)
        else:
            data = dict(getattr(message, "__dict__", {}))

        data.setdefault("archived_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        return data

    def _message_to_markdown(self, message, payload: dict[str, Any]) -> str:
        lines: list[str] = []

        lines.append("# Stage 6D 通知消息")
        lines.append("")
        lines.append("## 1. 基本信息")
        lines.append("")
        lines.append(f"- created_at: `{payload.get('created_at', '')}`")
        lines.append(f"- project_id: `{payload.get('project_id', '')}`")
        lines.append(f"- project_name: `{payload.get('project_name', '')}`")
        lines.append(f"- owner: `{payload.get('owner', '')}`")
        lines.append(f"- owner_contact: `{payload.get('owner_contact', '')}`")
        lines.append("")
        lines.append("## 2. 事件信息")
        lines.append("")
        lines.append(f"- event_type: `{payload.get('event_type', '')}`")
        lines.append(f"- issue_type: `{payload.get('issue_type', '')}`")
        lines.append(f"- severity: `{payload.get('severity', '')}`")
        lines.append(f"- status: `{payload.get('status', '')}`")
        lines.append(f"- action: `{payload.get('action', '')}`")
        lines.append(f"- fix_id: `{payload.get('fix_id', '')}`")
        lines.append("")
        lines.append("## 3. 自动恢复结果")
        lines.append("")
        lines.append(f"- apply_success: `{payload.get('apply_success', False)}`")
        lines.append(f"- rerun_success: `{payload.get('rerun_success', False)}`")
        lines.append(f"- rollback_executed: `{payload.get('rollback_executed', False)}`")
        lines.append(f"- rollback_success: `{payload.get('rollback_success', False)}`")
        lines.append(f"- recovered: `{payload.get('recovered', False)}`")
        lines.append("")
        lines.append("## 4. R15 恢复审计")
        lines.append("")
        lines.append(f"- strategy_layer: `{payload.get('strategy_layer', '')}`")
        lines.append(f"- auto_recover_allowed: `{payload.get('auto_recover_allowed', False)}`")
        lines.append(f"- dry_run: `{payload.get('dry_run', True)}`")
        lines.append(f"- would_execute: `{payload.get('would_execute', False)}`")
        lines.append(f"- allowed_to_execute: `{payload.get('allowed_to_execute', False)}`")
        lines.append(f"- downgrade_reason: `{payload.get('downgrade_reason', '') or '<none>'}`")
        lines.append(f"- operator_required: `{payload.get('operator_required', False)}`")
        lines.append(f"- audit_required: `{payload.get('audit_required', True)}`")
        lines.append(f"- forbidden_action: `{payload.get('forbidden_action', False)}`")
        lines.append(f"- rollback_available: `{payload.get('rollback_available', False)}`")
        lines.append(f"- execution_result: `{payload.get('execution_result', '')}`")
        lines.append(f"- rollback_result: `{payload.get('rollback_result', '')}`")
        lines.append("")
        lines.append("### recovery_audit_summary")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(payload.get("recovery_audit_summary", {}) or {}, ensure_ascii=False, indent=2))
        lines.append("```")
        lines.append("")
        lines.append("## 5. 通知消息")
        lines.append("")
        lines.append(str(payload.get("message", "")))
        lines.append("")
        lines.append("## 6. 报告路径")
        lines.append("")

        report_paths = payload.get("report_paths", []) or []
        if report_paths:
            for path in report_paths:
                lines.append(f"- `{path}`")
        else:
            lines.append("- <empty>")

        lines.append("")
        lines.append("## 7. 原始字段")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(payload, ensure_ascii=False, indent=2))
        lines.append("```")

        return "\n".join(lines)

    def _safe_name(self, text: str) -> str:
        text = str(text).strip().lower()
        text = re.sub(r"[^a-zA-Z0-9_-]+", "_", text)
        return text.strip("_") or "unknown"
