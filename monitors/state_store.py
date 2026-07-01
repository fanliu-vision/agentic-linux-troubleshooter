from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class ProjectMonitorState:
    project_id: str
    started_at: str = field(default_factory=now_text)
    updated_at: str = field(default_factory=now_text)

    status: str = "initialized"
    mode: str = ""
    run_count: int = 0
    idle_cycles: int = 0

    last_heartbeat_at: str = ""
    last_health_check_at: str = ""
    last_health_status: str = "unknown"
    last_health_message: str = ""

    events_detected_total: int = 0
    reports_generated_total: int = 0
    notifications_sent_total: int = 0

    last_event_type: str = ""
    last_issue_type: str = ""
    last_event_fingerprint: str = ""
    last_report_path: str = ""

    seen_fingerprints: list[str] = field(default_factory=list)
    runtime_health: dict[str, Any] = field(default_factory=dict)
    remote_log_watermarks: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "status": self.status,
            "mode": self.mode,
            "run_count": self.run_count,
            "idle_cycles": self.idle_cycles,
            "last_heartbeat_at": self.last_heartbeat_at,
            "last_health_check_at": self.last_health_check_at,
            "last_health_status": self.last_health_status,
            "last_health_message": self.last_health_message,
            "events_detected_total": self.events_detected_total,
            "reports_generated_total": self.reports_generated_total,
            "notifications_sent_total": self.notifications_sent_total,
            "last_event_type": self.last_event_type,
            "last_issue_type": self.last_issue_type,
            "last_event_fingerprint": self.last_event_fingerprint,
            "last_report_path": self.last_report_path,
            "seen_fingerprints": self.seen_fingerprints,
            "runtime_health": self.runtime_health,
            "remote_log_watermarks": self.remote_log_watermarks,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProjectMonitorState":
        state = cls(project_id=str(data.get("project_id", "")))
        state.started_at = str(data.get("started_at", state.started_at))
        state.updated_at = str(data.get("updated_at", state.updated_at))
        state.status = str(data.get("status", "initialized"))
        state.mode = str(data.get("mode", ""))
        state.run_count = int(data.get("run_count", 0))
        state.idle_cycles = int(data.get("idle_cycles", 0))
        state.last_heartbeat_at = str(data.get("last_heartbeat_at", ""))
        state.last_health_check_at = str(data.get("last_health_check_at", ""))
        state.last_health_status = str(data.get("last_health_status", "unknown"))
        state.last_health_message = str(data.get("last_health_message", ""))
        state.events_detected_total = int(data.get("events_detected_total", 0))
        state.reports_generated_total = int(data.get("reports_generated_total", 0))
        state.notifications_sent_total = int(data.get("notifications_sent_total", 0))
        state.last_event_type = str(data.get("last_event_type", ""))
        state.last_issue_type = str(data.get("last_issue_type", ""))
        state.last_event_fingerprint = str(data.get("last_event_fingerprint", ""))
        state.last_report_path = str(data.get("last_report_path", ""))

        raw_seen = data.get("seen_fingerprints") or []
        state.seen_fingerprints = [str(item) for item in raw_seen]

        raw_runtime_health = data.get("runtime_health") or {}
        if isinstance(raw_runtime_health, dict):
            state.runtime_health = dict(raw_runtime_health)
        else:
            state.runtime_health = {}

        raw_remote_log_watermarks = data.get("remote_log_watermarks") or {}
        if isinstance(raw_remote_log_watermarks, dict):
            state.remote_log_watermarks = {
                str(path): dict(watermark)
                for path, watermark in raw_remote_log_watermarks.items()
                if isinstance(watermark, dict)
            }
        else:
            state.remote_log_watermarks = {}

        return state


class MonitorStateStore:
    """
    Stage 6E 长期状态存储。

    作用：
    1. 保存 project_status.json；
    2. 保存长期 seen_fingerprints；
    3. 记录 heartbeat、health check、事件统计；
    4. 让 daemon 重启后避免重复报警。
    """

    def __init__(
        self,
        project_id: str,
        state_dir: str = "state",
        max_seen_fingerprints: int = 5000,
    ) -> None:
        self.project_id = project_id
        self.state_dir = Path(state_dir)
        self.project_state_dir = self.state_dir / project_id
        self.status_path = self.project_state_dir / "project_status.json"
        self.events_path = self.project_state_dir / "events.jsonl"
        self.max_seen_fingerprints = max_seen_fingerprints

        self.project_state_dir.mkdir(parents=True, exist_ok=True)

    def load(self) -> ProjectMonitorState:
        if not self.status_path.exists():
            return ProjectMonitorState(project_id=self.project_id)

        try:
            data = json.loads(self.status_path.read_text(encoding="utf-8"))
            state = ProjectMonitorState.from_dict(data)
            if not state.project_id:
                state.project_id = self.project_id
            return state
        except Exception:
            # 状态文件损坏时不要让 daemon 无法启动。
            return ProjectMonitorState(project_id=self.project_id, status="state_load_failed")

    def save(self, state: ProjectMonitorState) -> None:
        state.updated_at = now_text()

        # 控制指纹集合大小，避免长期运行状态文件无限增长。
        if len(state.seen_fingerprints) > self.max_seen_fingerprints:
            state.seen_fingerprints = state.seen_fingerprints[-self.max_seen_fingerprints :]

        tmp_path = self.status_path.with_suffix(".json.tmp")
        tmp_path.write_text(
            json.dumps(state.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp_path.replace(self.status_path)

    def append_event(self, payload: dict[str, Any]) -> None:
        record = {
            "created_at": now_text(),
            **payload,
        }
        with self.events_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def seen_fingerprints(self) -> set[str]:
        return set(self.load().seen_fingerprints)

    def mark_seen(self, fingerprint: str) -> None:
        state = self.load()
        if fingerprint not in state.seen_fingerprints:
            state.seen_fingerprints.append(fingerprint)
        self.save(state)
