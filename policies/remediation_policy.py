from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from detectors import ErrorEvent
from monitors.project_registry import ProjectConfig


RecoveryAction = Literal[
    "auto_recover",
    "manual_escalation",
    "report_only",
]


@dataclass
class RemediationDecision:
    action: RecoveryAction
    fix_id: str = ""
    reason: str = ""
    severity: str = "medium"
    notify_owner: bool = True
    should_rerun: bool = True
    rollback_on_failure: bool = True

    @property
    def is_auto_recover(self) -> bool:
        return self.action == "auto_recover" and bool(self.fix_id)

    def to_markdown(self) -> str:
        return (
            "## Stage 6C 自动恢复策略决策\n\n"
            f"- action: `{self.action}`\n"
            f"- fix_id: `{self.fix_id if self.fix_id else '<none>'}`\n"
            f"- severity: `{self.severity}`\n"
            f"- notify_owner: `{self.notify_owner}`\n"
            f"- should_rerun: `{self.should_rerun}`\n"
            f"- rollback_on_failure: `{self.rollback_on_failure}`\n"
            f"- reason: {self.reason}\n"
        )


class RemediationPolicy:
    """
    Stage 6C 自动恢复策略。

    设计原则：
    1. 只有受控配置修改才允许自动 apply；
    2. 不执行 rm / kill / sudo / scancel / systemctl 等危险动作；
    3. 是否真正自动修，还必须受 projects.yaml 中 policy.auto_recover 和 allow_auto_apply 控制；
    4. 通用 Python 缺包仍默认升级；只有可选依赖降级开关可进入 safe_auto_recover。
    """

    DEFAULT_FIX_MAPPING = {
        "network_port": "fix-network-1",
        "gpu_oom": "fix-gpu-1",
        "cache_write_failed": "fix-cache-1",
        "optional_dependency_missing": "fix-optional-dep-1",
        "worker_overload": "fix-worker-1",
        "python_env": "fix-python-1",
        "model_path": "fix-model-path-1",
        "config_path": "fix-config-path-1",
    }

    ALWAYS_ESCALATE_EVENT_TYPES = {
        "disk_full",
        "slurm",
        "process_kill",
        "process_crash",
        "container_k8s",
        "host_resource",
        "network_connectivity",
        "dependency_service",
        "config_error",
        "auth_cert",
        "permission_denied",
        "sudo_required",
        "unknown",
    }

    ALWAYS_ESCALATE_ISSUE_TYPES = {
        "disk",
        "slurm",
        "system",
        "permission",
        "unknown",
    }

    def decide(self, event: ErrorEvent, project: ProjectConfig) -> RemediationDecision:
        event_type = getattr(event, "event_type", "unknown")
        issue_type = getattr(event, "issue_type", event_type)
        severity = getattr(event, "severity", "medium")
        should_rerun = bool(project.policy.auto_rerun_after_apply)
        rollback_on_failure = bool(project.policy.rollback_on_failure)

        if not project.policy.auto_recover:
            return RemediationDecision(
                action="manual_escalation",
                severity=severity,
                should_rerun=should_rerun,
                rollback_on_failure=rollback_on_failure,
                reason=(
                    "当前项目 policy.auto_recover=false。Agent 只生成报告和提醒，"
                    "不会自动执行 apply / remote-apply。"
                ),
            )

        if event_type in self.ALWAYS_ESCALATE_EVENT_TYPES:
            return RemediationDecision(
                action="manual_escalation",
                severity=severity,
                should_rerun=should_rerun,
                rollback_on_failure=rollback_on_failure,
                reason=f"`{event_type}` 属于高风险或不可控错误，不允许自动修复。",
            )

        if issue_type in self.ALWAYS_ESCALATE_ISSUE_TYPES:
            return RemediationDecision(
                action="manual_escalation",
                severity=severity,
                should_rerun=should_rerun,
                rollback_on_failure=rollback_on_failure,
                reason=f"`{issue_type}` 属于需要负责人或管理员处理的领域，不允许自动修复。",
            )

        if issue_type in project.policy.escalation_required:
            return RemediationDecision(
                action="manual_escalation",
                severity=severity,
                should_rerun=should_rerun,
                rollback_on_failure=rollback_on_failure,
                reason=(
                    f"`{issue_type}` 已在 projects.yaml 的 "
                    "`policy.escalation_required` 中声明，必须升级通知负责人。"
                ),
            )

        fix_id = self._select_fix_id(event_type=event_type, issue_type=issue_type)

        if not fix_id:
            return RemediationDecision(
                action="report_only",
                severity=severity,
                should_rerun=should_rerun,
                rollback_on_failure=rollback_on_failure,
                reason=(
                    f"当前事件 `{event_type}` / `{issue_type}` 没有映射到受控 fix_id，"
                    "只生成报告，不自动修复。"
                ),
            )

        if fix_id not in project.policy.allow_auto_apply:
            return RemediationDecision(
                action="manual_escalation",
                fix_id=fix_id,
                severity=severity,
                should_rerun=should_rerun,
                rollback_on_failure=rollback_on_failure,
                reason=(
                    f"候选 fix_id `{fix_id}` 未出现在 projects.yaml 的 "
                    "`policy.allow_auto_apply` 中，因此不允许自动 apply。"
                ),
            )

        if event_type == "python_env":
            return RemediationDecision(
                action="auto_recover",
                fix_id=fix_id,
                severity=severity,
                should_rerun=should_rerun,
                rollback_on_failure=rollback_on_failure,
                reason=(
                    "检测到 Python 环境问题，但项目已显式允许 `fix-python-1`。"
                    "这里仅允许受控修复，例如修改配置开关、切换 requirements 中明确声明的依赖；"
                    "不允许任意 pip install。"
                ),
            )

        if event_type in {
            "cache_write_failed",
            "optional_dependency_missing",
            "worker_overload",
        }:
            return RemediationDecision(
                action="auto_recover",
                fix_id=fix_id,
                severity=severity,
                should_rerun=should_rerun,
                rollback_on_failure=rollback_on_failure,
                reason=(
                    f"事件 `{event_type}` 已映射到低风险配置修复 `{fix_id}`，"
                    "仅允许修改项目内 JSON 配置字段，并且该 fix_id 已被项目策略显式允许。"
                ),
            )

        return RemediationDecision(
            action="auto_recover",
            fix_id=fix_id,
            severity=severity,
            should_rerun=should_rerun,
            rollback_on_failure=rollback_on_failure,
            reason=(
                f"事件 `{event_type}` 已映射到受控修复 `{fix_id}`，"
                "且该 fix_id 已被项目策略显式允许自动执行。"
            ),
        )

    def _select_fix_id(self, event_type: str, issue_type: str) -> str:
        if event_type in self.DEFAULT_FIX_MAPPING:
            return self.DEFAULT_FIX_MAPPING[event_type]

        if issue_type == "network_port":
            return "fix-network-1"

        if issue_type == "gpu":
            return "fix-gpu-1"

        if issue_type == "cache":
            return "fix-cache-1"

        if issue_type == "optional_dependency":
            return "fix-optional-dep-1"

        if issue_type == "worker_overload":
            return "fix-worker-1"

        if issue_type == "python_env":
            return "fix-python-1"

        return ""
