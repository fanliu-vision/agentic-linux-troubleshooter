from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from detectors import ErrorEvent
from monitors.project_registry import ProjectConfig
from safe_recovery.registry import (
    STRATEGY_MANUAL_ESCALATION,
    STRATEGY_SAFE_AUTO_RECOVER,
    fix_id_for_event_type,
    fix_id_for_issue_type,
    fix_mapping_by_event_type,
    get_recovery_domain_spec_for_event_type,
    manual_event_types_without_fix,
    manual_issue_types_without_fix,
    strategy_for_issue_type,
)


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
            "## 自动恢复兼容策略适配器输出\n\n"
            f"- action: `{self.action}`\n"
            f"- fix_id: `{self.fix_id if self.fix_id else '<none>'}`\n"
            f"- severity: `{self.severity}`\n"
            f"- notify_owner: `{self.notify_owner}`\n"
            f"- should_rerun: `{self.should_rerun}`\n"
            f"- rollback_on_failure: `{self.rollback_on_failure}`\n"
            f"- reason: {self.reason}\n"
        )


class CompatibilityRemediationPolicy:
    """
    Compatibility adapter for the legacy remediation decision shape.

    This adapter keeps the old RemediationDecision evidence/report contract alive.
    It does not decide whether execution is allowed; registry domain policy,
    project policy overlay, and the runtime gate are the authoritative layers.
    """

    # Compatibility exports retained for one release cycle.
    DEFAULT_FIX_MAPPING = fix_mapping_by_event_type()
    ALWAYS_ESCALATE_EVENT_TYPES = manual_event_types_without_fix()
    ALWAYS_ESCALATE_ISSUE_TYPES = manual_issue_types_without_fix()

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

        domain_spec = get_recovery_domain_spec_for_event_type(event_type)
        if self._requires_manual_escalation_by_registry(
            event_type=event_type,
            issue_type=issue_type,
        ):
            reason = (
                domain_spec.reason
                if domain_spec is not None and domain_spec.reason
                else f"`{event_type}` 属于高风险或不可控错误，不允许自动修复。"
            )
            return RemediationDecision(
                action="manual_escalation",
                severity=severity,
                should_rerun=should_rerun,
                rollback_on_failure=rollback_on_failure,
                reason=(
                    f"`{event_type}` / `{issue_type}` 当前 registry 策略为 "
                    f"`{STRATEGY_MANUAL_ESCALATION}`：{reason}"
                ),
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

        return RemediationDecision(
            action="auto_recover",
            fix_id=fix_id,
            severity=severity,
            should_rerun=should_rerun,
            rollback_on_failure=rollback_on_failure,
            reason=self._auto_recover_reason(
                event_type=event_type,
                issue_type=issue_type,
                fix_id=fix_id,
            ),
        )

    def _select_fix_id(self, event_type: str, issue_type: str) -> str:
        fix_id = fix_id_for_event_type(event_type)
        if fix_id:
            return fix_id

        return fix_id_for_issue_type(issue_type)

    @staticmethod
    def _requires_manual_escalation_by_registry(
        *,
        event_type: str,
        issue_type: str,
    ) -> bool:
        domain_spec = get_recovery_domain_spec_for_event_type(event_type)
        if domain_spec is not None:
            return (
                domain_spec.strategy_layer == STRATEGY_MANUAL_ESCALATION
                and not domain_spec.fix_id
            )

        return strategy_for_issue_type(issue_type) == STRATEGY_MANUAL_ESCALATION

    @staticmethod
    def _auto_recover_reason(
        *,
        event_type: str,
        issue_type: str,
        fix_id: str,
    ) -> str:
        domain_spec = get_recovery_domain_spec_for_event_type(event_type)
        if domain_spec is None:
            return (
                f"事件 `{event_type}` / `{issue_type}` 已映射到受控修复 `{fix_id}`，"
                "且该 fix_id 已被项目策略显式允许自动执行。"
            )

        if domain_spec.strategy_layer == STRATEGY_SAFE_AUTO_RECOVER:
            return (
                f"事件 `{event_type}` 已映射到低风险配置修复 `{fix_id}`："
                f"{domain_spec.reason}。该 fix_id 已被项目策略显式允许。"
            )

        return (
            f"事件 `{event_type}` 当前 registry 策略为 "
            f"`{domain_spec.strategy_layer}`，但保留 legacy fix_id `{fix_id}` "
            "兼容路径；该 fix_id 已被项目策略显式允许。"
        )


# Compatibility alias retained for one release cycle. New code should refer to
# CompatibilityRemediationPolicy when it explicitly needs the legacy evidence
# shape; execution authority remains with the runtime gate.
RemediationPolicy = CompatibilityRemediationPolicy
