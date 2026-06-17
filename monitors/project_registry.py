from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


def _as_bool(value: Any, default: bool = False) -> bool:
    """
    Safely parse YAML boolean-like values.

    Why not use bool(value)?
    - bool("false") is True in Python, which is dangerous for config flags.
    """
    if value is None:
        return default

    if isinstance(value, bool):
        return value

    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "yes", "y", "1", "on"}:
            return True
        if text in {"false", "no", "n", "0", "off"}:
            return False

    return bool(value)

@dataclass
class SSHConfig:
    user: str = ""
    host: str = ""
    port: int = 22


@dataclass
class MonitorConfig:
    interval_seconds: int = 5
    tail_lines: int = 200
    auto_report: bool = True
    max_events_per_run: int = 5


@dataclass
class PolicyConfig:
    auto_recover: bool = False
    allow_auto_apply: list[str] = field(default_factory=list)
    escalation_required: list[str] = field(default_factory=list)
    rollback_on_failure: bool = True
    auto_rerun_after_apply: bool = True


@dataclass
class NotificationConfig:
    enabled: bool = True
    channels: list[str] = field(default_factory=lambda: ["console", "file"])
    alerts_dir: str = "outputs/alerts"
    webhook_url: str = ""
    webhook_url_env: str = ""
    notify_on_recovered: bool = True
    notify_on_escalation: bool = True
    notify_on_rollback: bool = True
    notify_on_report_only: bool = True


@dataclass
class ProjectConfig:
    project_id: str
    name: str
    mode: str
    owner: str = ""
    owner_contact: str = "console"

    project_dir: str = ""
    remote_project_dir: str = ""
    run_command: str = ""
    log_files: list[str] = field(default_factory=list)

    ssh: SSHConfig = field(default_factory=SSHConfig)
    monitor: MonitorConfig = field(default_factory=MonitorConfig)
    policy: PolicyConfig = field(default_factory=PolicyConfig)
    notification: NotificationConfig = field(default_factory=NotificationConfig)

    @property
    def is_remote(self) -> bool:
        return self.mode == "remote"

    @property
    def effective_project_dir(self) -> str:
        return self.remote_project_dir if self.is_remote else self.project_dir


class ProjectRegistry:
    def __init__(self, config_path: str = "configs/projects.yaml") -> None:
        self.config_path = Path(config_path)

    def load_all(self) -> list[ProjectConfig]:
        if not self.config_path.exists():
            raise FileNotFoundError(f"项目注册文件不存在：{self.config_path}")

        data = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}
        projects = data.get("projects", [])

        return [self._parse_project(item) for item in projects]

    def get(self, project_id: str) -> ProjectConfig:
        for project in self.load_all():
            if project.project_id == project_id:
                return project

        raise KeyError(f"未找到 project_id={project_id} 的项目配置。")

    @staticmethod
    def _parse_project(item: dict[str, Any]) -> ProjectConfig:
        ssh_data = item.get("ssh") or {}
        monitor_data = item.get("monitor") or {}
        policy_data = item.get("policy") or {}
        notification_data = item.get("notification") or {}

        return ProjectConfig(
            project_id=str(item.get("project_id", "")),
            name=str(item.get("name", "")),
            mode=str(item.get("mode", "local")),
            owner=str(item.get("owner", "")),
            owner_contact=str(item.get("owner_contact", "console")),
            project_dir=str(item.get("project_dir", "")),
            remote_project_dir=str(item.get("remote_project_dir", "")),
            run_command=str(item.get("run_command", "")),
            log_files=list(item.get("log_files") or []),
            ssh=SSHConfig(
                user=str(ssh_data.get("user", "")),
                host=str(ssh_data.get("host", "")),
                port=int(ssh_data.get("port", 22)),
            ),
            monitor=MonitorConfig(
                interval_seconds=int(monitor_data.get("interval_seconds", 5)),
                tail_lines=int(monitor_data.get("tail_lines", 200)),
                auto_report=_as_bool(monitor_data.get("auto_report"), True),
                max_events_per_run=int(monitor_data.get("max_events_per_run", 5)),
            ),
            policy=PolicyConfig(
                auto_recover=_as_bool(policy_data.get("auto_recover"), False),
                allow_auto_apply=list(policy_data.get("allow_auto_apply") or []),
                escalation_required=list(policy_data.get("escalation_required") or []),
                rollback_on_failure=_as_bool(policy_data.get("rollback_on_failure"), True),
                auto_rerun_after_apply=_as_bool(policy_data.get("auto_rerun_after_apply"), True),
            ),
            notification=NotificationConfig(
                enabled=_as_bool(notification_data.get("enabled"), True),
                channels=list(notification_data.get("channels") or ["console", "file"]),
                alerts_dir=str(notification_data.get("alerts_dir", "outputs/alerts")),
                webhook_url=str(notification_data.get("webhook_url", "")),
                webhook_url_env=str(notification_data.get("webhook_url_env", "")),
                notify_on_recovered=_as_bool(notification_data.get("notify_on_recovered"), True),
                notify_on_escalation=_as_bool(notification_data.get("notify_on_escalation"), True),
                notify_on_rollback=_as_bool(notification_data.get("notify_on_rollback"), True),
                notify_on_report_only=_as_bool(notification_data.get("notify_on_report_only"), True),
            ),
        )