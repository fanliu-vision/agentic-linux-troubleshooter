from __future__ import annotations

from dataclasses import dataclass, field

from detectors import ErrorEvent
from monitors.project_registry import ProjectConfig
from policies import RemediationDecision, RemediationPolicy
from recovery.auto_recovery_runtime_gate import (
    RuntimeAutoRecoveryGateResult,
    evaluate_runtime_auto_recovery_gate,
)
from sessions import EvidenceItem, TroubleshootingSession


@dataclass
class AutoRecoveryResult:
    event_type: str
    issue_type: str
    decision: RemediationDecision
    apply_success: bool = False
    rerun_success: bool = False
    rollback_executed: bool = False
    report_paths: list[str] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)
    r15_gate: RuntimeAutoRecoveryGateResult | None = None

    @property
    def recovered(self) -> bool:
        return self.apply_success and self.rerun_success

    def to_markdown(self) -> str:
        if self.recovered:
            event_recovery_status = "recovered"
        elif self.rollback_executed:
            event_recovery_status = "rollback_done"
        elif self.decision.action == "manual_escalation":
            event_recovery_status = "manual_escalation"
        elif self.decision.action == "report_only":
            event_recovery_status = "report_only"
        else:
            event_recovery_status = "unresolved"

        # 兼容旧报告字段：deterministic_event_status 继续保留，
        # 但以后推荐 LLM 优先使用 event_recovery_status。
        deterministic_status = event_recovery_status

        # 这个字段只表达“当前事件自动恢复之后，是否还有自动恢复之外的风险需要人工看”。
        # AutoRecoveryResult 是单事件结果，无法可靠判断 disk/python_env 等全局残留风险，
        # 所以成功恢复时标记为 not_evaluated_by_auto_recovery，避免 LLM 把残留风险误写成 partially_recovered。
        residual_risk_status = (
            "not_evaluated_by_auto_recovery"
            if self.recovered
            else "requires_manual_review"
        )

        lines = [
            "## Stage 6C 自动恢复执行结果",
            "",
            f"- event_type: `{self.event_type}`",
            f"- issue_type: `{self.issue_type}`",
            f"- action: `{self.decision.action}`",
            f"- fix_id: `{self.decision.fix_id if self.decision.fix_id else '<none>'}`",
            f"- apply_success: `{self.apply_success}`",
            f"- rerun_success: `{self.rerun_success}`",
            f"- rollback_executed: `{self.rollback_executed}`",
            f"- recovered: `{self.recovered}`",
        ]

        if self.r15_gate is not None:
            lines.extend(
                [
                    f"- r15_strategy_layer: `{self.r15_gate.strategy_layer}`",
                    f"- r15_dry_run: `{self.r15_gate.dry_run}`",
                    f"- r15_would_execute: `{self.r15_gate.would_execute}`",
                    f"- r15_allowed_to_execute: `{self.r15_gate.allowed_to_execute}`",
                    f"- r15_downgrade_reason: `{self.r15_gate.downgrade_reason or '<none>'}`",
                ]
            )

        lines.extend(
            [
                "",
                "## 状态口径",
                f"- event_recovery_status: `{event_recovery_status}`",
                f"- residual_risk_status: `{residual_risk_status}`",
                f"- deterministic_event_status: `{deterministic_status}`",
                "- event_report_scope: `single_event`",
                "- status_rule: `event_recovery_status 只表示当前事件的自动恢复结果；disk/python_env 等次要风险应写入 residual_risk_status，不能把已恢复事件改写成 partially_recovered。`",
                "- consistency_rule: `如果 apply_success=True、rerun_success=True、rollback_executed=False、recovered=True，则当前事件必须写成 recovered。`",
                "",
                "### 策略原因",
                "",
                self.decision.reason,
                "",
            ]
        )

        if self.r15_gate is not None:
            lines.extend(
                [
                    "### R15 runtime gate audit",
                    "",
                    self.r15_gate.to_markdown(),
                    "",
                ]
            )

        lines.extend(["### 执行日志", ""])

        if self.messages:
            for item in self.messages:
                lines.append(item)
                lines.append("")
        else:
            lines.append("- 无执行日志。")

        if self.report_paths:
            lines.append("### 报告路径")
            lines.append("")
            for path in self.report_paths:
                lines.append(f"- `{path}`")

        return "\n".join(lines)


class AutoRecoveryRunner:
    """
    Stage 6C 自动恢复执行器。

    流程：
    1. 根据 ErrorEvent 调用 RemediationPolicy；
    2. 如果允许自动恢复，执行 apply / remote-apply；
    3. apply 后执行 rerun / remote-rerun；
    4. rerun 成功则生成恢复报告；
    5. rerun 失败则 rollback，并生成升级报告。
    """

    def __init__(
        self,
        project: ProjectConfig,
        session: TroubleshootingSession,
        policy: RemediationPolicy | None = None,
    ) -> None:
        self.project = project
        self.session = session
        self.policy = policy or RemediationPolicy()

    def is_auto_recover_candidate(self, event: ErrorEvent) -> bool:
        decision = self.policy.decide(event=event, project=self.project)
        if not decision.is_auto_recover:
            return False

        gate = evaluate_runtime_auto_recovery_gate(
            event=event,
            project=self.project,
            remediation_decision=decision,
        )
        return gate.is_candidate

    def recover(self, event: ErrorEvent) -> AutoRecoveryResult:
        issue_type = getattr(event, "issue_type", getattr(event, "event_type", "unknown"))
        decision = self.policy.decide(event=event, project=self.project)
        report_scope_start = self._find_event_evidence_start(event)
        print("[Stage6C] AutoRecoveryRunner.recover() called")
        print(f"[Stage6C] event_type={event.event_type}, issue_type={event.issue_type}")
        print(f"[Stage6C] project_auto_recover={self.project.policy.auto_recover}")
        print(f"[Stage6C] allow_auto_apply={self.project.policy.allow_auto_apply}")

        result = AutoRecoveryResult(
            event_type=event.event_type,
            issue_type=issue_type,
            decision=decision,
        )

        self.session.add_evidence(
            content=decision.to_markdown(),
            source="recovery_policy",
            title=f"Recovery policy decision: {event.event_type}",
            issue_type=issue_type,
        )

        if not decision.is_auto_recover:
            result.messages.append(
                "[Escalation] 当前事件不满足自动修复条件，已进入负责人通知 / 报告流程。"
            )
            self._record_result(result)
            self._generate_report(
                result,
                evidence_items=self.session.evidence_items[report_scope_start:],
            )
            return result

        gate = evaluate_runtime_auto_recovery_gate(
            event=event,
            project=self.project,
            remediation_decision=decision,
        )
        result.r15_gate = gate

        self.session.add_evidence(
            content=gate.to_markdown(),
            source="r15_auto_recovery_gate",
            title=f"R15 auto recovery gate: {event.event_type}",
            issue_type=issue_type,
        )

        if not gate.allowed_to_execute:
            result.decision = self._downgrade_decision_for_r15_gate(
                decision=decision,
                gate=gate,
            )
            result.messages.append(
                "[R15Gate] 自动恢复未执行："
                f"strategy_layer={gate.strategy_layer}, "
                f"dry_run={gate.dry_run}, "
                f"would_execute={gate.would_execute}, "
                f"reason={gate.downgrade_reason or '<none>'}."
            )
            self._record_result(result)
            self._generate_report(
                result,
                evidence_items=self.session.evidence_items[report_scope_start:],
            )
            return result

        result.messages.append(
            f"[Policy] 自动恢复已允许，准备执行 fix_id={decision.fix_id}。"
        )

        try:
            self._generate_fix_plan_if_possible(result)

            if self.project.is_remote:
                self._run_remote_recovery(decision, result)
            else:
                self._run_local_recovery(decision, result)

            if result.apply_success and not result.rerun_success and decision.rollback_on_failure:
                self._rollback(result)

            self._record_result(result)
            self._generate_report(
                result,
                evidence_items=self.session.evidence_items[report_scope_start:],
            )
            return result

        except Exception as exc:
            result.messages.append(f"[RecoveryError] 自动恢复过程中出现异常：{type(exc).__name__}: {exc}")

            if decision.rollback_on_failure:
                self._rollback(result)

            self._record_result(result)
            self._generate_report(
                result,
                evidence_items=self.session.evidence_items[report_scope_start:],
            )
            return result

    def _downgrade_decision_for_r15_gate(
        self,
        *,
        decision: RemediationDecision,
        gate: RuntimeAutoRecoveryGateResult,
    ) -> RemediationDecision:
        action = "manual_escalation" if gate.operator_required else "report_only"
        if gate.dry_run and gate.auto_recover_allowed:
            action = "report_only"

        return RemediationDecision(
            action=action,  # type: ignore[arg-type]
            fix_id=gate.selected_fix_id or decision.fix_id,
            reason=(
                "R15 runtime gate blocked automatic execution. "
                f"strategy_layer={gate.strategy_layer}; "
                f"dry_run={gate.dry_run}; "
                f"would_execute={gate.would_execute}; "
                f"downgrade_reason={gate.downgrade_reason or '<none>'}."
            ),
            severity=decision.severity,
            notify_owner=decision.notify_owner,
            should_rerun=decision.should_rerun,
            rollback_on_failure=decision.rollback_on_failure,
        )

    def _find_event_evidence_start(self, event: ErrorEvent) -> int:
        fingerprint = getattr(event, "fingerprint", "")

        if not fingerprint:
            return len(self.session.evidence_items)

        for index in range(len(self.session.evidence_items) - 1, -1, -1):
            item = self.session.evidence_items[index]
            if fingerprint in item.content:
                return index

        return len(self.session.evidence_items)

    def _generate_fix_plan_if_possible(self, result: AutoRecoveryResult) -> None:
        try:
            text = self.session.generate_fix_plan()
            result.messages.append("[FixPlan] 已生成修复计划。")
            result.messages.append(text)
        except Exception as exc:
            result.messages.append(
                f"[FixPlan] 修复计划生成失败，但不会阻断受控 apply：{type(exc).__name__}: {exc}"
            )

    def _run_local_recovery(
        self,
        decision: RemediationDecision,
        result: AutoRecoveryResult,
    ) -> None:
        apply_text = self.session.apply_fix(decision.fix_id)
        result.messages.append("### Local apply result")
        result.messages.append(apply_text)

        result.apply_success = bool(self.session.latest_apply_success)

        if not result.apply_success:
            result.messages.append("[Apply] 本地 apply 失败，停止 rerun，转入报告 / 通知流程。")
            return

        if decision.should_rerun:
            rerun_text = self.session.rerun_project()
            result.messages.append("### Local rerun result")
            result.messages.append(rerun_text)
            result.rerun_success = bool(self.session.latest_rerun_success)

    def _run_remote_recovery(
        self,
        decision: RemediationDecision,
        result: AutoRecoveryResult,
    ) -> None:
        remote_project_dir = self.project.effective_project_dir

        apply_text = self.session.remote_apply_fix(
            fix_id=decision.fix_id,
            remote_project_dir=remote_project_dir,
        )
        result.messages.append("### Remote apply result")
        result.messages.append(apply_text)

        result.apply_success = bool(self.session.latest_remote_apply_success)

        if not result.apply_success:
            result.messages.append("[RemoteApply] 远程 apply 失败，停止 rerun，转入报告 / 通知流程。")
            return

        if decision.should_rerun:
            rerun_text = self.session.rerun_remote_project(remote_project_dir)
            result.messages.append("### Remote rerun result")
            result.messages.append(rerun_text)
            result.rerun_success = bool(self.session.latest_remote_rerun_success)

    def _rollback(self, result: AutoRecoveryResult) -> None:
        if not result.apply_success:
            result.messages.append("[Rollback] apply 未成功，无需 rollback。")
            return

        if result.rerun_success:
            result.messages.append("[Rollback] rerun 已成功，无需 rollback。")
            return

        if self.project.is_remote:
            rollback_text = self.session.remote_rollback_latest_apply()
            result.messages.append("### Remote rollback result")
            result.messages.append(rollback_text)
        else:
            rollback_text = self.session.rollback_latest_apply()
            result.messages.append("### Local rollback result")
            result.messages.append(rollback_text)

        result.rollback_executed = True

    def _generate_report(
        self,
        result: AutoRecoveryResult,
        evidence_items: list[EvidenceItem] | None = None,
    ) -> None:
        from datetime import datetime
        from pathlib import Path
        import shutil

        report, save_path, source = self.session.generate_report(
            evidence_items=evidence_items,
        )

        save_path = Path(save_path)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        event_report_name = (
            f"event_{timestamp}_"
            f"{result.event_type}_"
            f"{result.decision.action}_"
            f"{save_path.name}"
        )
        event_report_path = save_path.with_name(event_report_name)

        try:
            shutil.copyfile(save_path, event_report_path)
            result.report_paths.append(str(event_report_path))
            result.messages.append(
                f"[Report] event-specific report copied: {event_report_path}"
            )
        except Exception as exc:
            result.report_paths.append(str(save_path))
            result.messages.append(
                f"[Report][WARN] failed to copy event-specific report: "
                f"{type(exc).__name__}: {exc}"
            )

        result.messages.append(f"[Report] rolling final report generated by {source}: {save_path}")

    def _record_result(self, result: AutoRecoveryResult) -> None:
        result_text = result.to_markdown()

        if hasattr(self.session, "record_auto_recovery_result"):
            self.session.record_auto_recovery_result(
                result_text=result_text,
                action=result.decision.action,
                fix_id=result.decision.fix_id,
                apply_success=result.apply_success,
                rerun_success=result.rerun_success,
                rollback_executed=result.rollback_executed,
            )
            return

        self.session.add_evidence(
            content=result_text,
            source="auto_recovery",
            title=f"Auto recovery result: {result.event_type}",
            issue_type="auto_recovery",
        )
