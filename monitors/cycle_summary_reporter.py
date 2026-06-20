from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from monitors.project_registry import ProjectConfig


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


@dataclass
class CycleEventRecord:
    event_type: str
    issue_type: str
    severity: str
    summary: str
    source: str
    fingerprint: str

    action: str
    fix_id: str = ""
    apply_success: bool = False
    rerun_success: bool = False
    rollback_executed: bool = False
    rollback_success: bool = False
    recovered: bool = False

    notification_status: str = ""
    notification_channels: list[str] = field(default_factory=list)
    notification_results: list[str] = field(default_factory=list)

    report_paths: list[str] = field(default_factory=list)
    recovery_audit_summary: dict[str, Any] = field(default_factory=dict)

    @property
    def status(self) -> str:
        if self.recovered:
            return "recovered"

        if self.rollback_executed and self.rollback_success:
            return "rollback_done"
        if self.rollback_executed:
            return "rollback_failed"

        if self.action == "manual_escalation":
            return "manual_escalation"

        if self.action == "report_only":
            return "report_only"

        return "unresolved"

    @property
    def event_recovery_status(self) -> str:
        # 与 status 含义一致，但字段名更明确，供 LLM 报告区分“事件恢复状态”和“残留风险状态”。
        return self.status

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "issue_type": self.issue_type,
            "severity": self.severity,
            "summary": self.summary,
            "source": self.source,
            "fingerprint": self.fingerprint,
            "action": self.action,
            "fix_id": self.fix_id,
            "apply_success": self.apply_success,
            "rerun_success": self.rerun_success,
            "rollback_executed": self.rollback_executed,
            "rollback_success": self.rollback_success,
            "recovered": self.recovered,
            "status": self.status,
            "event_recovery_status": self.event_recovery_status,
            "notification_status": self.notification_status,
            "notification_channels": self.notification_channels,
            "notification_results": self.notification_results,
            "report_paths": self.report_paths,
            "recovery_audit_summary": self.recovery_audit_summary,
        }


class CycleSummaryReporter:
    """
    Stage 6 report consistency layer.

    作用：
    1. 用确定性代码计算本轮总体状态；
    2. 避免 LLM 把部分恢复写成全部恢复；
    3. 为多事件 monitor cycle 生成 cycle summary report。
    """

    def __init__(self, project: ProjectConfig) -> None:
        self.project = project

    def compute_overall_status(self, records: list[CycleEventRecord]) -> str:
        if not records:
            return "idle"

        if all(record.recovered for record in records):
            return "recovered"

        if any(record.rollback_executed and not record.rollback_success for record in records):
            return "rollback_failed"

        if any(record.recovered for record in records):
            return "partially_recovered"

        if any(record.rollback_executed for record in records):
            return "rollback_done"

        if any(record.action == "manual_escalation" for record in records):
            return "manual_escalation"

        if any(record.action == "report_only" for record in records):
            return "report_only"

        return "unresolved"

    def write_report(
        self,
        records: list[CycleEventRecord],
        output_dir: str | Path,
    ) -> str:
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        report_path = output_path / f"cycle_{_timestamp()}_summary_report.md"
        report_path.write_text(
            self.to_markdown(records),
            encoding="utf-8",
        )
        return str(report_path)

    def to_markdown(self, records: list[CycleEventRecord]) -> str:
        overall_status = self.compute_overall_status(records)

        # cycle summary 只统计本轮已经进入 AutoRecoveryRunner 的事件恢复结果。
        # disk/python_env 等没有被自动修复的风险不在这里改写 overall_status；
        # 它们应该作为残留风险写入 LLM 报告的“次要问题与后续风险”。
        residual_risk_status = "not_evaluated_by_cycle_summary"

        recovered_count = sum(1 for record in records if record.recovered)
        rollback_count = sum(1 for record in records if record.rollback_executed)
        rollback_failed_count = sum(
            1 for record in records
            if record.rollback_executed and not record.rollback_success
        )
        escalation_count = sum(
            1 for record in records
            if record.action == "manual_escalation"
        )
        unresolved_count = sum(
            1 for record in records
            if record.status == "unresolved"
        )

        lines: list[str] = []

        lines.append("# Stage 6 多事件监控与自动恢复汇总报告")
        lines.append("")
        lines.append("> 本报告由代码确定性生成，是本轮监控周期的主报告。")
        lines.append("> 如果事件级 LLM 报告与本报告不一致，以本报告中的 overall_status 和事件状态表为准。")
        lines.append("> overall_status 只统计本轮已进入 AutoRecoveryRunner 的事件恢复结果。")
        lines.append("> disk/python_env 等未自动处理的问题属于 residual_risk，不应把已恢复事件改写为 partially_recovered。")
        lines.append("")
        lines.append("## 1. 确定性总体结论")
        lines.append("")
        lines.append(f"- generated_at: `{_now_text()}`")
        lines.append(f"- project_id: `{self.project.project_id}`")
        lines.append(f"- project_name: `{self.project.name}`")
        lines.append(f"- mode: `{self.project.mode}`")
        lines.append(f"- owner: `{self.project.owner}`")
        lines.append(f"- overall_status: `{overall_status}`")
        lines.append(f"- event_recovery_status: `{overall_status}`")
        lines.append(f"- residual_risk_status: `{residual_risk_status}`")
        lines.append(f"- events_total: `{len(records)}`")
        lines.append(f"- recovered_count: `{recovered_count}`")
        lines.append(f"- rollback_count: `{rollback_count}`")
        lines.append(f"- rollback_failed_count: `{rollback_failed_count}`")
        lines.append(f"- manual_escalation_count: `{escalation_count}`")
        lines.append(f"- unresolved_count: `{unresolved_count}`")
        lines.append("")

        if overall_status == "recovered":
            lines.append("本轮所有已处理事件均已自动恢复，并通过 rerun 验证。若仍存在 disk/python_env 等未自动处理问题，应作为残留风险单独说明。")
        elif overall_status == "partially_recovered":
            lines.append(
                "本轮已处理事件为部分恢复：至少一个已处理事件已恢复，但仍存在未恢复、已回滚或需要人工处理的已处理事件。"
            )
        elif overall_status == "rollback_done":
            lines.append(
                "本轮自动恢复未完成，至少一个事件已执行 rollback，需要负责人继续处理。"
            )
        elif overall_status == "rollback_failed":
            lines.append(
                "本轮自动恢复未完成，且至少一个事件 rollback 失败，需要负责人立即处理。"
            )
        elif overall_status == "manual_escalation":
            lines.append(
                "本轮事件未自动恢复，已进入人工升级处理流程。"
            )
        elif overall_status == "report_only":
            lines.append(
                "本轮仅生成报告，未执行自动修复。"
            )
        else:
            lines.append(
                "本轮事件尚未恢复，需负责人检查事件报告和通知审计。"
            )

        lines.append("")
        lines.append("## 2. 事件级处理结果")
        lines.append("")
        lines.append(
            "| # | event_type | issue_type | severity | action | fix_id | apply_success | rerun_success | rollback_executed | rollback_success | recovered | status | event_recovery_status |"
        )
        lines.append(
            "|---|------------|------------|----------|--------|--------|---------------|---------------|-------------------|------------------|-----------|--------|-----------------------|"
        )

        for index, record in enumerate(records, start=1):
            lines.append(
                "| "
                f"{index} | "
                f"`{record.event_type}` | "
                f"`{record.issue_type}` | "
                f"`{record.severity}` | "
                f"`{record.action}` | "
                f"`{record.fix_id or '<none>'}` | "
                f"`{record.apply_success}` | "
                f"`{record.rerun_success}` | "
                f"`{record.rollback_executed}` | "
                f"`{record.rollback_success}` | "
                f"`{record.recovered}` | "
                f"`{record.status}` | "
                f"`{record.event_recovery_status}` |"
            )

        lines.append("")
        lines.append("## 3. 恢复状态解释")
        lines.append("")

        for index, record in enumerate(records, start=1):
            lines.append(f"### 事件 {index}: `{record.event_type}`")
            lines.append("")
            lines.append(f"- summary: {record.summary}")
            lines.append(f"- source: `{record.source}`")
            lines.append(f"- fingerprint: `{record.fingerprint}`")
            lines.append(f"- deterministic_status: `{record.status}`")
            lines.append(f"- event_recovery_status: `{record.event_recovery_status}`")
            lines.append("- residual_risk_status: `not_evaluated_by_cycle_summary`")
            lines.append("")

            if record.status == "recovered":
                lines.append(
                    "结论：该事件已完成自动恢复，apply 和 rerun 均成功。"
                )
            elif record.status == "rollback_done":
                lines.append(
                    "结论：该事件自动修复 apply 成功，但 rerun 验证失败，系统已执行 rollback，最终未恢复。"
                )
            elif record.status == "rollback_failed":
                lines.append(
                    "结论：该事件自动修复 apply 成功，但 rerun 验证失败，且 rollback 未成功，需要负责人立即处理。"
                )
            elif record.status == "manual_escalation":
                lines.append(
                    "结论：该事件不在自动修复范围内，已升级通知负责人。"
                )
            elif record.status == "report_only":
                lines.append(
                    "结论：该事件只生成报告，未执行自动修复。"
                )
            else:
                lines.append(
                    "结论：该事件尚未恢复，需要负责人继续检查。"
                )

            lines.append("")

        lines.append("## 4. 通知与审计")
        lines.append("")

        for index, record in enumerate(records, start=1):
            channels = ", ".join(record.notification_channels) or "<none>"
            lines.append(f"### 事件 {index}: `{record.event_type}`")
            lines.append(f"- notification_status: `{record.notification_status or '<unknown>'}`")
            lines.append(f"- notification_channels: `{channels}`")
            if record.recovery_audit_summary:
                lines.append("- recovery_audit_summary:")
                for key, value in record.recovery_audit_summary.items():
                    lines.append(f"  - {key}: `{value}`")
            else:
                lines.append("- recovery_audit_summary: <empty>")

            if record.notification_results:
                lines.append("- notification_results:")
                for item in record.notification_results:
                    lines.append(f"  - {item}")
            else:
                lines.append("- notification_results: <empty>")

            lines.append("")

        lines.append("## 5. 事件报告路径")
        lines.append("")

        for index, record in enumerate(records, start=1):
            lines.append(f"### 事件 {index}: `{record.event_type}`")

            if record.report_paths:
                for path in record.report_paths:
                    lines.append(f"- `{path}`")
            else:
                lines.append("- <empty>")

            lines.append("")

        lines.append("## 6. 一致性规则")
        lines.append("")
        lines.append("- 如果任一事件 `recovered=False`，本轮总体状态不得写成 `recovered`。")
        lines.append("- 如果任一事件 `rollback_executed=True`，必须明确说明该事件未恢复并已回滚。")
        lines.append("- 如果任一事件 `rollback_executed=True` 且 `rollback_success=False`，总体状态必须是 `rollback_failed`。")
        lines.append("- 如果同时存在 `recovered=True` 和 `recovered=False` 的已处理事件，总体状态必须是 `partially_recovered`。")
        lines.append("- LLM 事件报告只能作为解释性报告，本汇总报告中的 `overall_status` 是确定性结果。")
        lines.append("- `overall_status` 表示已处理事件的自动恢复状态，不等同于系统中不存在任何残留风险。")
        lines.append("- 如果所有已处理事件 `recovered=True`，但仍存在 disk/python_env 风险，应写为 `overall_status=recovered` 且 `residual_risk_status=has_manual_risks` 或在风险章节单独说明。")

        return "\n".join(lines)
