from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from detectors import ErrorEvent
from monitors.project_registry import ProjectConfig
from policies import CompatibilityRemediationPolicy, RemediationDecision
from recovery.auto_recovery_runtime_gate import (
    RuntimeAutoRecoveryGateResult,
    evaluate_runtime_auto_recovery_gate,
    refresh_runtime_auto_recovery_audit,
)
from recovery.auto_recovery_runtime_controls import RuntimeAutoRecoveryCooldownTracker
from sessions import EvidenceItem, TroubleshootingSession
from monitors.trace_store import APPROVAL_STATUS_APPROVED


@dataclass
class AutoRecoveryResult:
    event_type: str
    issue_type: str
    decision: RemediationDecision
    apply_success: bool = False
    rerun_success: bool = False
    rollback_executed: bool = False
    rollback_success: bool = False
    report_paths: list[str] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)
    r15_gate: RuntimeAutoRecoveryGateResult | None = None
    legacy_decision: RemediationDecision | None = None
    decision_shadow: dict[str, Any] = field(default_factory=dict)
    apply_edit_summary: list[dict[str, Any]] = field(default_factory=list)
    rollback_edit_summary: list[dict[str, Any]] = field(default_factory=list)
    approval_request_record: dict[str, Any] = field(default_factory=dict)
    approval_decision_record: dict[str, Any] = field(default_factory=dict)

    @property
    def recovered(self) -> bool:
        return self.apply_success and self.rerun_success

    @property
    def event_recovery_status(self) -> str:
        if self.recovered:
            return "recovered"
        if self.rollback_executed and self.rollback_success:
            return "rollback_done"
        if self.rollback_executed:
            return "rollback_failed"
        if self.decision.action == "manual_escalation":
            return "manual_escalation"
        if self.decision.action == "report_only":
            return "report_only"
        return "unresolved"

    @property
    def residual_risk_status(self) -> str:
        return (
            "not_evaluated_by_auto_recovery"
            if self.recovered
            else "requires_manual_review"
        )

    def recovery_audit_record(self) -> dict[str, Any]:
        gate_record = (
            deepcopy(self.r15_gate.audit_record)
            if self.r15_gate is not None
            else {}
        )
        gate_record.update(
            {
                "event_type": self.event_type,
                "fingerprint": gate_record.get("fingerprint", ""),
                "strategy_layer": gate_record.get(
                    "strategy_layer",
                    self.decision.action,
                ),
                "selected_policy": gate_record.get(
                    "selected_policy",
                    "r15.runtime.not_evaluated",
                ),
                "action": self.decision.action,
                "legacy_policy_action": (
                    self.legacy_decision.action
                    if self.legacy_decision is not None
                    else ""
                ),
                "legacy_policy_fix_id": (
                    self.legacy_decision.fix_id
                    if self.legacy_decision is not None
                    else ""
                ),
                "decision_shadow": dict(self.decision_shadow),
                "candidate_fix_id": gate_record.get(
                    "candidate_fix_id",
                    self.decision.fix_id or "",
                ),
                "selected_fix_id": gate_record.get(
                    "selected_fix_id",
                    self.decision.fix_id or "",
                ),
                "fix_id": self.decision.fix_id or "",
                "auto_recover_allowed": bool(
                    gate_record.get("auto_recover_allowed", False)
                ),
                "dry_run": bool(gate_record.get("dry_run", True)),
                "would_execute": bool(gate_record.get("would_execute", False)),
                "allowed_to_execute": bool(
                    gate_record.get("allowed_to_execute", False)
                ),
                "precheck_result": gate_record.get(
                    "precheck_result",
                    {"passed": False, "reason": "r15_gate_not_evaluated"},
                ),
                "cooldown_result": gate_record.get(
                    "cooldown_result",
                    {"allowed": False, "reason": "r15_gate_not_evaluated"},
                ),
                "rate_limit_result": gate_record.get(
                    "rate_limit_result",
                    {
                        "checked_before_runner": False,
                        "source": "not_evaluated",
                    },
                ),
                "rollback_available": bool(
                    gate_record.get("rollback_available", False)
                ),
                "rollback_plan": gate_record.get("precheck_result", {}).get(
                    "rollback_plan",
                    {},
                ),
                "operator_required": bool(
                    gate_record.get("operator_required", False)
                ),
                "downgrade_reason": str(
                    gate_record.get("downgrade_reason", "")
                ),
                "forbidden_action": bool(
                    gate_record.get("forbidden_action", False)
                ),
                "execution_result": self._execution_result(),
                "rollback_result": self._rollback_result(),
                "apply_success": self.apply_success,
                "rerun_success": self.rerun_success,
                "rollback_executed": self.rollback_executed,
                "rollback_success": self.rollback_success,
                "apply_edit_summary": list(self.apply_edit_summary),
                "rollback_edit_summary": list(self.rollback_edit_summary),
                "approval_required": bool(self.approval_request_record),
                "approval_request": dict(self.approval_request_record),
                "approval_decision": dict(self.approval_decision_record),
                "approval_request_id": str(
                    self.approval_request_record.get("request_id", "")
                ),
                "approval_status": str(
                    (
                        self.approval_decision_record
                        or self.approval_request_record
                    ).get("status", "")
                ),
                "recovered": self.recovered,
                "event_recovery_status": self.event_recovery_status,
                "residual_risk_status": self.residual_risk_status,
                "audit_required": bool(gate_record.get("audit_required", True)),
                "created_at": gate_record.get(
                    "created_at",
                    datetime.now(timezone.utc).isoformat(),
                ),
            }
        )
        return gate_record

    def recovery_audit_summary(self) -> dict[str, Any]:
        audit = self.recovery_audit_record()
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
            "rollback_success",
            "operator_required",
            "forbidden_action",
            "recovered",
        ]
        return {key: audit.get(key) for key in keys}

    def _execution_result(self) -> str:
        if self.r15_gate is not None and not self.r15_gate.allowed_to_execute:
            if self.r15_gate.downgrade_reason == "human_approval_required":
                return "not_run_human_approval_required"
            if self.r15_gate.downgrade_reason.startswith("approval_"):
                return "not_run_approval_invalid"
            if (
                self.r15_gate.dry_run
                and self.r15_gate.auto_recover_allowed
                and not self._gate_blocked_before_dry_run(self.r15_gate)
            ):
                return "not_run_r15_dry_run"
            return "not_run_r15_gate_blocked"

        if self.r15_gate is None:
            return "not_run_r15_gate_missing"

        if self.recovered:
            return "executed_recovered"

        if not self.apply_success:
            return "executed_apply_failed"

        if self.apply_success and not self.rerun_success:
            return "executed_rerun_failed"

        return "executed_unresolved"

    @staticmethod
    def _gate_blocked_before_dry_run(gate: RuntimeAutoRecoveryGateResult) -> bool:
        if gate.operator_required:
            return True

        if gate.precheck_result.get("passed") is not True:
            return True

        if gate.downgrade_reason and gate.downgrade_reason not in {
            "r15_dry_run",
            "no_op_already_safe",
        }:
            return True

        return False

    def _rollback_result(self) -> str:
        if self.rollback_executed:
            return "rollback_succeeded" if self.rollback_success else "rollback_failed"
        if self.recovered:
            return "not_needed_recovered"
        if self.r15_gate is not None and not self.r15_gate.allowed_to_execute:
            return "not_run_before_execution"
        if self.apply_success and not self.rerun_success:
            return "rollback_not_executed"
        return "not_run_before_execution"

    def to_markdown(self) -> str:
        event_recovery_status = self.event_recovery_status

        # 兼容旧报告字段：deterministic_event_status 继续保留，
        # 但以后推荐 LLM 优先使用 event_recovery_status。
        deterministic_status = event_recovery_status

        # 这个字段只表达“当前事件自动恢复之后，是否还有自动恢复之外的风险需要人工看”。
        # AutoRecoveryResult 是单事件结果，无法可靠判断 disk/python_env 等全局残留风险，
        # 所以成功恢复时标记为 not_evaluated_by_auto_recovery，避免 LLM 把残留风险误写成 partially_recovered。
        residual_risk_status = self.residual_risk_status
        audit = self.recovery_audit_record()

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
            f"- rollback_success: `{self.rollback_success}`",
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
                f"- r15_execution_result: `{audit.get('execution_result')}`",
            f"- r15_rollback_result: `{audit.get('rollback_result')}`",
            f"- r15_rollback_success: `{audit.get('rollback_success')}`",
            f"- r15_operator_required: `{audit.get('operator_required')}`",
                f"- r15_forbidden_action: `{audit.get('forbidden_action')}`",
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
                    "### Runtime gate audit",
                    "",
                    self.r15_gate.to_markdown(),
                    "",
                ]
            )

        lines.extend(
            [
                "### Runtime gate audit fields",
                "",
                "```json",
                f"{self._audit_json(audit)}",
                "```",
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

    @staticmethod
    def _audit_json(audit: dict[str, Any]) -> str:
        import json

        return json.dumps(audit, ensure_ascii=False, indent=2)


class AutoRecoveryRunner:
    """
    Stage 6C 自动恢复执行器。

    流程：
    1. CompatibilityRemediationPolicy 生成兼容输出；
    2. runtime gate 根据 registry domain policy 和 project policy overlay 作最终裁决；
    3. gate 允许时执行 apply / remote-apply；
    4. apply 后执行 rerun / remote-rerun；
    5. rerun 失败则 rollback，并生成报告。
    """

    def __init__(
        self,
        project: ProjectConfig,
        session: TroubleshootingSession,
        policy: CompatibilityRemediationPolicy | None = None,
        trace_store: Any | None = None,
        approval_store: Any | None = None,
    ) -> None:
        self.project = project
        self.session = session
        self.policy = policy or CompatibilityRemediationPolicy()
        self.cooldown_tracker = RuntimeAutoRecoveryCooldownTracker.from_project(project)
        self.trace_store = trace_store
        self.approval_store = approval_store

    def is_auto_recover_candidate(self, event: ErrorEvent) -> bool:
        decision = self.policy.decide(event=event, project=self.project)
        gate = evaluate_runtime_auto_recovery_gate(
            event=event,
            project=self.project,
            remediation_decision=decision,
            cooldown_result=self.cooldown_tracker.check(
                event_type=event.event_type,
                fingerprint=event.fingerprint,
                project_id=self.project.project_id,
            ),
        )
        if "ambiguous_event_evidence" in (gate.precheck_result.get("reasons") or []):
            return False

        return gate.is_candidate and gate.precheck_result.get("passed") is True

    def recover(self, event: ErrorEvent) -> AutoRecoveryResult:
        return self._recover(event=event, approval_request_id="")

    def recover_after_approval(
        self,
        event: ErrorEvent,
        approval_request_id: str,
    ) -> AutoRecoveryResult:
        return self._recover(
            event=event,
            approval_request_id=approval_request_id,
        )

    def _recover(
        self,
        *,
        event: ErrorEvent,
        approval_request_id: str = "",
    ) -> AutoRecoveryResult:
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
            legacy_decision=decision,
        )

        self.session.add_evidence(
            content=decision.to_markdown(),
            source="recovery_policy",
            title=f"Recovery policy decision: {event.event_type}",
            issue_type=issue_type,
        )

        gate = evaluate_runtime_auto_recovery_gate(
            event=event,
            project=self.project,
            remediation_decision=decision,
            cooldown_result=self.cooldown_tracker.check(
                event_type=event.event_type,
                fingerprint=event.fingerprint,
                project_id=self.project.project_id,
            ),
        )
        result.r15_gate = gate

        if gate.allowed_to_execute:
            if approval_request_id:
                approval_allowed, approval_reason = self._validate_approval_for_gate(
                    event=event,
                    gate=gate,
                    approval_request_id=approval_request_id,
                    result=result,
                )
                if not approval_allowed:
                    self._block_gate(
                        gate,
                        reason=approval_reason,
                        operator_required=True,
                    )
            elif self._requires_human_approval_for_live_apply():
                self._block_gate(
                    gate,
                    reason="human_approval_required",
                    operator_required=True,
                )

            if gate.allowed_to_execute:
                reservation = self.cooldown_tracker.reserve(
                    event_type=event.event_type,
                    fingerprint=event.fingerprint,
                    project_id=self.project.project_id,
                )
                gate.cooldown_result = reservation
                if reservation.get("allowed") is not True:
                    gate.allowed_to_execute = False
                    gate.would_execute = False
                    gate.downgrade_reason = str(
                        reservation.get("reason") or "cooldown_not_satisfied"
                    )
                refresh_runtime_auto_recovery_audit(gate)

        result.decision = self._decision_from_r15_gate(
            legacy_decision=decision,
            gate=gate,
        )
        result.decision_shadow = self._decision_shadow(
            legacy_decision=decision,
            final_decision=result.decision,
            gate=gate,
        )
        self._trace(
            "policy_decided",
            event=event,
            gate=gate,
            payload={
                "legacy_decision": self._decision_payload(decision),
                "final_decision": self._decision_payload(result.decision),
                "decision_shadow": dict(result.decision_shadow),
                "runtime_policy_decision": dict(
                    gate.audit_record.get("policy_decision") or {}
                ),
            },
        )
        self._trace(
            "precheck_completed",
            event=event,
            gate=gate,
            payload={
                "precheck_result": gate.precheck_result,
                "rollback_available": gate.rollback_available,
                "cooldown_result": gate.cooldown_result,
            },
        )

        self.session.add_evidence(
            content=gate.to_markdown(),
            source="r15_auto_recovery_gate",
            title=f"Runtime auto recovery gate: {event.event_type}",
            issue_type=issue_type,
        )
        if not approval_request_id:
            result.approval_request_record = self._create_approval_request_if_required(
                event=event,
                gate=gate,
            )

        if not gate.allowed_to_execute:
            result.messages.append(
                "[RuntimeGate] 自动恢复未执行："
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
            f"[Policy] 自动恢复已允许，准备执行 fix_id={result.decision.fix_id}。"
        )
        self._trace(
            "execution_started",
            event=event,
            result=result,
            gate=gate,
            payload={
                "fix_id": result.decision.fix_id,
                "mode": "remote" if self.project.is_remote else "local",
            },
        )

        try:
            self._generate_fix_plan_if_possible(result)

            if self.project.is_remote:
                self._run_remote_recovery(result.decision, result)
            else:
                self._run_local_recovery(result.decision, result)
            self._trace(
                "execution_finished",
                event=event,
                result=result,
                gate=gate,
                payload={
                    "status": "completed",
                    "apply_success": result.apply_success,
                    "rerun_success": result.rerun_success,
                    "recovered": result.recovered,
                    "apply_edit_summary": list(result.apply_edit_summary),
                },
            )

            if (
                result.apply_success
                and not result.rerun_success
                and result.decision.rollback_on_failure
            ):
                self._rollback(result)

            self._record_result(result)
            self._generate_report(
                result,
                evidence_items=self.session.evidence_items[report_scope_start:],
            )
            return result

        except Exception as exc:
            result.messages.append(f"[RecoveryError] 自动恢复过程中出现异常：{type(exc).__name__}: {exc}")
            self._trace(
                "execution_finished",
                event=event,
                result=result,
                gate=gate,
                payload={
                    "status": "exception",
                    "exception_type": type(exc).__name__,
                    "error": str(exc),
                    "apply_success": result.apply_success,
                    "rerun_success": result.rerun_success,
                },
            )

            if result.decision.rollback_on_failure:
                self._rollback(result)

            self._record_result(result)
            self._generate_report(
                result,
                evidence_items=self.session.evidence_items[report_scope_start:],
            )
            return result

    def _decision_from_r15_gate(
        self,
        *,
        legacy_decision: RemediationDecision,
        gate: RuntimeAutoRecoveryGateResult,
    ) -> RemediationDecision:
        if gate.allowed_to_execute:
            return RemediationDecision(
                action="auto_recover",
                fix_id=gate.selected_fix_id,
                reason=(
                    "Runtime gate authorized automatic recovery. "
                    f"strategy_layer={gate.strategy_layer}; "
                    f"selected_fix_id={gate.selected_fix_id}; "
                    f"would_execute={gate.would_execute}."
                ),
                severity=legacy_decision.severity,
                notify_owner=legacy_decision.notify_owner,
                should_rerun=legacy_decision.should_rerun,
                rollback_on_failure=legacy_decision.rollback_on_failure,
            )

        return self._downgrade_decision_for_r15_gate(
            decision=legacy_decision,
            gate=gate,
        )

    def _downgrade_decision_for_r15_gate(
        self,
        *,
        decision: RemediationDecision,
        gate: RuntimeAutoRecoveryGateResult,
    ) -> RemediationDecision:
        action = "manual_escalation" if gate.operator_required else "report_only"
        if gate.dry_run and gate.auto_recover_allowed and not gate.operator_required:
            action = "report_only"

        return RemediationDecision(
            action=action,  # type: ignore[arg-type]
            fix_id=gate.selected_fix_id or decision.fix_id,
            reason=(
                "Runtime gate blocked automatic execution. "
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

    @staticmethod
    def _decision_shadow(
        *,
        legacy_decision: RemediationDecision,
        final_decision: RemediationDecision,
        gate: RuntimeAutoRecoveryGateResult,
    ) -> dict[str, Any]:
        return {
            "mode": "r15_runtime_gate_final_decision",
            "legacy_action": legacy_decision.action,
            "legacy_fix_id": legacy_decision.fix_id,
            "legacy_is_auto_recover": legacy_decision.is_auto_recover,
            "gate_strategy_layer": gate.strategy_layer,
            "gate_candidate_fix_id": gate.candidate_fix_id,
            "gate_selected_fix_id": gate.selected_fix_id,
            "gate_allowed_to_execute": gate.allowed_to_execute,
            "gate_downgrade_reason": gate.downgrade_reason,
            "final_action": final_decision.action,
            "final_fix_id": final_decision.fix_id,
            "changed": (
                legacy_decision.action != final_decision.action
                or legacy_decision.fix_id != final_decision.fix_id
            ),
        }

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
        self._assert_r15_execution_authorized(decision=decision, result=result)

        apply_text = self.session.apply_fix(decision.fix_id)
        result.messages.append("### Local apply result")
        result.messages.append(apply_text)

        result.apply_success = bool(self.session.latest_apply_success)
        result.apply_edit_summary = self._latest_session_apply_edits(remote=False)

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
        self._assert_r15_execution_authorized(decision=decision, result=result)

        remote_project_dir = self.project.effective_project_dir

        apply_text = self.session.remote_apply_fix(
            fix_id=decision.fix_id,
            remote_project_dir=remote_project_dir,
        )
        result.messages.append("### Remote apply result")
        result.messages.append(apply_text)

        result.apply_success = bool(self.session.latest_remote_apply_success)
        result.apply_edit_summary = self._latest_session_apply_edits(remote=True)

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

        self._trace(
            "rollback_started",
            result=result,
            payload={
                "rollback_reason": "rerun_failed_after_apply",
                "apply_success": result.apply_success,
                "rerun_success": result.rerun_success,
                "apply_edit_summary": list(result.apply_edit_summary),
            },
        )

        if self.project.is_remote:
            rollback_text = self.session.remote_rollback_latest_apply()
            result.messages.append("### Remote rollback result")
            result.messages.append(rollback_text)
            result.rollback_edit_summary = self._latest_session_rollback_edits(remote=True)
            result.rollback_success = bool(
                getattr(self.session, "latest_remote_rollback_success", False)
            )
        else:
            rollback_text = self.session.rollback_latest_apply()
            result.messages.append("### Local rollback result")
            result.messages.append(rollback_text)
            result.rollback_edit_summary = self._latest_session_rollback_edits(remote=False)
            result.rollback_success = bool(
                getattr(self.session, "latest_rollback_success", False)
            )

        result.rollback_executed = True
        self._trace(
            "rollback_finished",
            result=result,
            payload={
                "rollback_executed": result.rollback_executed,
                "rollback_success": result.rollback_success,
                "rollback_edit_summary": list(result.rollback_edit_summary),
            },
        )

    def _latest_session_apply_edits(self, *, remote: bool) -> list[dict[str, Any]]:
        attr = "latest_remote_apply_edit_records" if remote else "latest_apply_edit_records"
        return list(getattr(self.session, attr, []) or [])

    def _latest_session_rollback_edits(self, *, remote: bool) -> list[dict[str, Any]]:
        attr = (
            "latest_remote_rollback_edit_records"
            if remote
            else "latest_rollback_edit_records"
        )
        return list(getattr(self.session, attr, []) or [])

    def _assert_r15_execution_authorized(
        self,
        *,
        decision: RemediationDecision,
        result: AutoRecoveryResult,
    ) -> None:
        gate = result.r15_gate
        if gate is None:
            raise RuntimeError("r15_runtime_gate_required_before_auto_recovery")

        if not gate.audit_required or not gate.audit_record:
            raise RuntimeError("r15_runtime_gate_audit_required_before_auto_recovery")

        if gate.dry_run:
            raise RuntimeError("r15_runtime_gate_dry_run_blocks_auto_recovery")

        if not gate.allowed_to_execute or not gate.would_execute:
            raise RuntimeError("r15_runtime_gate_did_not_authorize_execution")

        if gate.selected_fix_id != decision.fix_id:
            raise RuntimeError(
                "r15_runtime_gate_selected_fix_mismatch:"
                f"gate={gate.selected_fix_id};decision={decision.fix_id}"
            )

        if gate.strategy_layer != "safe_auto_recover":
            raise RuntimeError(
                "r15_runtime_gate_requires_safe_auto_recover:"
                f"{gate.strategy_layer}"
            )

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
                rollback_success=result.rollback_success,
                recovery_audit_record=result.recovery_audit_record(),
                recovery_audit_summary=result.recovery_audit_summary(),
            )
            return

        self.session.add_evidence(
            content=result_text,
            source="auto_recovery",
            title=f"Auto recovery result: {result.event_type}",
            issue_type="auto_recovery",
        )

    def _create_approval_request_if_required(
        self,
        *,
        event: ErrorEvent,
        gate: RuntimeAutoRecoveryGateResult,
    ) -> dict[str, Any]:
        if self.approval_store is None:
            return {}

        if not gate.operator_required:
            return {}

        try:
            return dict(self.approval_store.create_request_from_gate(
                event=event,
                gate=gate,
                audit_record=gate.audit_record,
                reason=gate.downgrade_reason,
            ))
        except Exception:
            return {}

    def _requires_human_approval_for_live_apply(self) -> bool:
        return bool(
            getattr(
                self.project.policy,
                "require_human_approval_for_live_apply",
                False,
            )
        )

    def _validate_approval_for_gate(
        self,
        *,
        event: ErrorEvent,
        gate: RuntimeAutoRecoveryGateResult,
        approval_request_id: str,
        result: AutoRecoveryResult,
    ) -> tuple[bool, str]:
        if self.approval_store is None:
            return False, "approval_store_unavailable"

        try:
            request = dict(self.approval_store.get_request(approval_request_id))
            latest = dict(self.approval_store.latest_record(approval_request_id))
        except KeyError:
            return False, "approval_request_not_found"

        result.approval_request_record = request
        result.approval_decision_record = latest

        status = str(latest.get("status", ""))
        if status != APPROVAL_STATUS_APPROVED:
            return False, f"approval_status_not_approved:{status or '<missing>'}"

        if str(request.get("fingerprint", "")) != event.fingerprint:
            return False, "approval_fingerprint_mismatch"

        if str(request.get("event_type", "")) != event.event_type:
            return False, "approval_event_type_mismatch"

        if str(request.get("selected_fix_id", "")) != gate.selected_fix_id:
            return False, "approval_fix_id_mismatch"

        if request.get("approvable") is not True:
            return False, "approval_request_not_approvable"

        return True, "approval_valid_after_fresh_gate"

    @staticmethod
    def _block_gate(
        gate: RuntimeAutoRecoveryGateResult,
        *,
        reason: str,
        operator_required: bool,
    ) -> None:
        gate.allowed_to_execute = False
        gate.would_execute = False
        gate.operator_required = operator_required
        gate.downgrade_reason = reason
        refresh_runtime_auto_recovery_audit(gate)

    def _trace(
        self,
        stage: str,
        *,
        event: ErrorEvent | None = None,
        result: AutoRecoveryResult | None = None,
        gate: RuntimeAutoRecoveryGateResult | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        if self.trace_store is None:
            return

        record_payload = dict(payload or {})

        if gate is not None:
            record_payload["gate"] = self._gate_payload(gate)

        event_type = getattr(event, "event_type", "")
        fingerprint = getattr(event, "fingerprint", "")

        if result is not None:
            audit = result.recovery_audit_record()
            event_type = event_type or result.event_type
            fingerprint = fingerprint or str(audit.get("fingerprint", ""))
            record_payload["recovery_audit_summary"] = result.recovery_audit_summary()

        try:
            self.trace_store.append(
                stage,
                event=event,
                event_type=event_type,
                fingerprint=fingerprint,
                payload=record_payload,
            )
        except Exception:
            return

    @staticmethod
    def _decision_payload(decision: RemediationDecision) -> dict[str, Any]:
        return {
            "action": decision.action,
            "fix_id": decision.fix_id,
            "severity": decision.severity,
            "notify_owner": decision.notify_owner,
            "should_rerun": decision.should_rerun,
            "rollback_on_failure": decision.rollback_on_failure,
            "reason": decision.reason,
        }

    @staticmethod
    def _gate_payload(gate: RuntimeAutoRecoveryGateResult) -> dict[str, Any]:
        return {
            "strategy_layer": gate.strategy_layer,
            "candidate_fix_id": gate.candidate_fix_id,
            "selected_fix_id": gate.selected_fix_id,
            "auto_recover_allowed": gate.auto_recover_allowed,
            "dry_run": gate.dry_run,
            "would_execute": gate.would_execute,
            "allowed_to_execute": gate.allowed_to_execute,
            "operator_required": gate.operator_required,
            "rollback_available": gate.rollback_available,
            "downgrade_reason": gate.downgrade_reason,
        }
