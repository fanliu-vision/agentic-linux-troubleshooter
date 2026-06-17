from __future__ import annotations

import re
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from dotenv import load_dotenv
from openai import OpenAI

from agents.domain_agents import AgentResult


class ReportAgent:
    """
    ReportAgent merges ManagerAgent and domain-agent outputs into one Markdown report.

    Stage-3 V1 uses a deterministic report generator.
    Later, this class can be upgraded to an LLM-based ReportAgent.
    """

    def __init__(self, output_dir: str = "outputs/reports") -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def build_report(self, route: Dict[str, Any], results: List[AgentResult]) -> str:
        primary_issue = route.get("primary_issue_type") or route.get("issue_type") or "unknown"
        secondary_issues = route.get("secondary_issue_types") or []
        all_issues = route.get("all_detected_issue_types") or []
        log_path = route.get("log_path") or "<none>"

        manager_result = (
                self._find_result(results, "ManagerAgent")
                or self._find_result(results, "DynamicManagerAgent")
        )
        log_result = self._find_result(results, "LogDiagnosisAgent")
        primary_result = self._find_primary_result(results)

        domain_results = [
            r for r in results
            if r.agent_name not in {"ManagerAgent", "DynamicManagerAgent", "LogDiagnosisAgent"}
        ]

        lines = []

        lines.append("# 多 Agent Linux 排障报告")
        lines.append("")
        lines.append("## 1. 总体结论")
        lines.append("")
        lines.append(f"- **主故障类型**：`{primary_issue}`")
        lines.append(f"- **次要问题类型**：`{secondary_issues}`")
        lines.append(f"- **全部识别类型**：`{all_issues}`")
        lines.append(f"- **日志路径**：`{log_path}`")
        lines.append("")
        if primary_result:
            lines.append(f"**主故障摘要**：{primary_result.summary}")
        elif log_result:
            lines.append(f"**主故障摘要**：{log_result.summary}")
        else:
            lines.append("**主故障摘要**：未能明确判断，需要补充日志或系统命令输出。")

        lines.append("")
        lines.append("## 2. ManagerAgent 路由结果")
        lines.append("")
        if manager_result:
            lines.extend(self._result_basic_lines(manager_result))
        else:
            lines.append("- <no manager result>")

        lines.append("")
        lines.append("## 3. LogDiagnosisAgent 日志诊断")
        lines.append("")
        if log_result:
            lines.extend(self._result_basic_lines(log_result))
        else:
            lines.append("- 未运行日志诊断。")

        lines.append("")
        lines.append("## 4. 领域 Agent 分析结果")
        lines.append("")
        if domain_results:
            for result in domain_results:
                lines.append(f"### 4.{domain_results.index(result) + 1} {result.agent_name}")
                lines.append("")
                lines.extend(self._result_detail_lines(result))
                lines.append("")
        else:
            lines.append("- 未运行领域 Agent。")

        lines.append("")
        lines.append("## 5. 主故障与次要问题排序")
        lines.append("")
        if primary_result:
            lines.append(f"- **主故障**：`{primary_result.issue_type}`，原因：{primary_result.summary}")
        else:
            lines.append(f"- **主故障**：`{primary_issue}`")

        non_issue_types = {
            "manager",
            "interactive_evidence",
            "resolution",
            "auto_recovery",
            "recovery_policy",
        }

        secondary_domain_results = [
            result for result in domain_results
            if (
                    not result.is_primary
                    and result.status == "ok"
                    and result.issue_type not in non_issue_types
            )
        ]

        if secondary_domain_results:
            lines.append("- **次要问题**：")
            for result in secondary_domain_results:
                lines.append(f"  - `{result.issue_type}`：{result.summary}")
        else:
            lines.append("- **次要问题**：<empty>")

        lines.append("")
        lines.append("## 6. 建议继续执行的只读检查命令")
        lines.append("")
        checks = self._collect_unique_items(
            result.recommended_checks
            for result in results
            if result.status == "ok"
        )

        safe_commands = []
        text_checks = []

        for check in checks:
            if self._is_safe_command_like(check):
                safe_commands.append(check)
            else:
                text_checks.append(check)

        if safe_commands:
            lines.append("```bash")
            for command in safe_commands:
                lines.append(command)
            lines.append("```")
        else:
            lines.append("- 暂无可直接执行的只读命令。")

        if text_checks:
            lines.append("")
            lines.append("补充检查说明：")
            for item in text_checks:
                lines.append(f"- {item}")

        lines.append("")
        lines.append("## 7. 低风险修复建议")
        lines.append("")
        low_risk_actions = self._collect_unique_items(
            result.low_risk_actions
            for result in results
            if result.status == "ok"
        )
        if low_risk_actions:
            for action in low_risk_actions:
                lines.append(f"- {action}")
        else:
            lines.append("- 暂无。")

        lines.append("")
        lines.append("## 8. 需要人工确认的操作")
        lines.append("")
        manual_actions = self._collect_unique_items(
            result.manual_confirm_actions
            for result in results
            if result.status == "ok"
        )
        if manual_actions:
            for action in manual_actions:
                lines.append(f"- {action}")
        else:
            lines.append("- 暂无。")

        lines.append("")
        lines.append("## 9. 风险提醒")
        lines.append("")
        risk_notes = self._collect_unique_items(
            result.risk_notes
            for result in results
            if result.status == "ok"
        )
        if risk_notes:
            for note in risk_notes:
                lines.append(f"- {note}")
        else:
            lines.append("- 暂无。")

        lines.append("")
        lines.append("## 10. Stage 6C 自动恢复结果")
        lines.append("")

        auto_recovery_result = self._find_result(results, "AutoRecoveryAgent")

        if auto_recovery_result:
            lines.extend(self._result_detail_lines(auto_recovery_result))
        else:
            lines.append("- 本次报告未检测到自动恢复执行结果。")
            lines.append("- 如果期望 Stage 6C 自动修复，请检查：")
            lines.append("  - projects.yaml 中 policy.auto_recover 是否为 true")
            lines.append("  - policy.allow_auto_apply 是否包含对应 fix_id")
            lines.append("  - MonitorLoop.run_once() 是否调用 _handle_event(event)")
            lines.append("  - AutoRecoveryRunner 是否在 generate_report() 前写入 recovery evidence")

        lines.append("")
        lines.append("## 11. 证据来源")
        lines.append("")
        lines.append("- 来自内容感知路由：`classify_issue_dict`")
        if log_result:
            lines.append("- 来自日志诊断：`diagnose_mixed_log_file`")
        for result in domain_results:
            if result.status == "ok":
                lines.append(f"- 来自领域 Agent：`{result.agent_name}`")
        lines.append("")
        lines.append(f"报告生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        return "\n".join(lines)

    def save_report(self, report: str, filename: str = "last_multi_agent_report.md") -> Path:
        path = self.output_dir / filename
        path.write_text(report, encoding="utf-8")
        return path

    @staticmethod
    def _find_result(results: List[AgentResult], agent_name: str) -> AgentResult | None:
        for result in results:
            if result.agent_name == agent_name:
                return result
        return None

    @staticmethod
    def _find_primary_result(results: List[AgentResult]) -> AgentResult | None:
        for result in results:
            if result.is_primary and result.status == "ok":
                return result
        return None

    @staticmethod
    def _result_basic_lines(result: AgentResult) -> List[str]:
        return [
            f"- agent_name: `{result.agent_name}`",
            f"- issue_type: `{result.issue_type}`",
            f"- status: `{result.status}`",
            f"- is_primary: `{result.is_primary}`",
            f"- summary: {result.summary}",
        ]

    @staticmethod
    def _result_detail_lines(result: AgentResult) -> List[str]:
        lines = [
            f"- issue_type: `{result.issue_type}`",
            f"- status: `{result.status}`",
            f"- is_primary: `{result.is_primary}`",
            f"- summary: {result.summary}",
        ]

        def add_list(title: str, items: List[str]) -> None:
            lines.append(f"- {title}:")
            if items:
                for item in items:
                    clean_item = str(item).strip()
                    clean_item = clean_item.lstrip("-").strip()
                    lines.append(f"  - {clean_item}")
            else:
                lines.append("  - <empty>")

        add_list("evidence", result.evidence)
        add_list("analysis", result.analysis)
        add_list("recommended_checks", result.recommended_checks)
        add_list("low_risk_actions", result.low_risk_actions)
        add_list("manual_confirm_actions", result.manual_confirm_actions)
        add_list("risk_notes", result.risk_notes)

        return lines

    @staticmethod
    def _collect_unique_items(groups) -> List[str]:
        items = []
        for group in groups:
            for item in group:
                if item and item not in items:
                    items.append(item)
        return items

    @staticmethod
    def _is_safe_command_like(text: str) -> bool:
        """
        Only allow real read-only shell commands in bash code blocks.
        Chinese descriptions or dangerous operations should not enter code blocks.
        """
        text = text.strip()

        if not text:
            return False

        # 中文说明不进入 bash 代码块
        if re.search(r"[\u4e00-\u9fff]", text):
            return False

        dangerous = [
            "rm -rf",
            "kill -9",
            "scancel",
            "sudo rm",
            "chmod -R",
            "chown -R",
            "mkfs",
            "dd ",
            "systemctl restart",
            "reboot",
            "shutdown",
        ]

        if any(x in text for x in dangerous):
            return False

        safe_patterns = [
            r"^df\s+",
            r"^du\s+",
            r"^ss\s+",
            r"^lsof\s+",
            r"^squeue(\s|$)",
            r"^scontrol\s+show\s+",
            r"^sinfo(\s|$)",
            r"^which\s+",
            r"^python\s+-c\s+",
            r"^python\s+-m\s+pip\s+",
            r"^echo\s+",
            r"^hy-smi(\s|$)",
            r"^nvidia-smi(\s|$)",
            r"^grep\s+",
        ]

        return any(re.search(pattern, text) for pattern in safe_patterns)

class LLMReportAgent:
    """
    LLMReportAgent generates a natural-language troubleshooting report from
    structured multi-agent results.

    Important:
    - It does not call tools.
    - It does not re-diagnose from raw logs.
    - It only summarizes and organizes evidence already produced by ManagerAgent
      and domain agents.
    """

    def __init__(
            self,
            model_id: str = "deepseek-chat",
            api_base: str = "https://api.deepseek.com",
            api_key_env: str = "DEEPSEEK_API_KEY",
            temperature: float = 0.15,
            max_context_chars: int = 48000,
            raw_excerpt_chars: int = 3600,
            output_dir: str = "outputs/reports",
    ) -> None:
        load_dotenv()

        self.model_id = model_id
        self.api_base = api_base
        self.api_key_env = api_key_env
        self.temperature = temperature
        self.max_context_chars = max_context_chars
        self.raw_excerpt_chars = raw_excerpt_chars

        api_key = os.getenv(api_key_env)
        if not api_key:
            raise RuntimeError(
                f"未找到环境变量 {api_key_env}。如果要使用 LLMReportAgent，"
                f"请先在 WSL 环境或 .env 中配置 {api_key_env}。"
            )

        self.client = OpenAI(
            api_key=api_key,
            base_url=api_base,
        )

        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def build_report(self, route: Dict[str, Any], results: List[AgentResult]) -> str:
        context = self._build_llm_context(route, results)

        system_prompt = self._build_system_prompt()
        user_prompt = self._build_user_prompt(context)

        response = self.client.chat.completions.create(
            model=self.model_id,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=self.temperature,
        )

        report = response.choices[0].message.content or ""
        report = self._postprocess_report(report)

        if not report:
            raise RuntimeError("LLMReportAgent 返回了空报告。")

        return report

    def save_report(self, report: str, filename: str = "last_multi_agent_llm_report.md") -> Path:
        path = self.output_dir / filename
        path.write_text(report, encoding="utf-8")
        return path

    @staticmethod
    def _is_stage6_result(result: AgentResult) -> bool:
        text = "\n".join(
            [
                result.agent_name or "",
                result.issue_type or "",
                result.status or "",
                result.summary or "",
                "\n".join(str(item) for item in result.evidence),
                "\n".join(str(item) for item in result.analysis),
                result.raw_output or "",
            ]
        ).lower()

        markers = [
            "autorecoveryagent",
            "auto_recovery",
            "stage 6c",
            "stage 6d",
            "stage 6e",
            "自动恢复",
            "通知",
            "notifier",
            "notification",
            "apply_success",
            "rerun_success",
            "rollback_executed",
            "recovered",
            "manual_escalation",
            "report_only",
            "fix_id",
            "daemon",
            "heartbeat",
            "project_status.json",
            "alerts",
        ]

        return any(marker.lower() in text for marker in markers)

    @classmethod
    def _has_stage6_context(cls, results: List[AgentResult]) -> bool:
        return any(cls._is_stage6_result(result) for result in results)

    def _format_stage6_executive_context(self, results: List[AgentResult]) -> str:
        stage6_results = [
            result for result in results
            if self._is_stage6_result(result)
        ]

        if not stage6_results:
            return "未检测到 Stage 6 自动恢复、通知或 daemon 上下文。"

        lines: list[str] = []
        lines.append("以下是最高优先级上下文。")
        lines.append("如果这里存在 AutoRecoveryAgent / NotificationAgent，报告必须优先写监控与自动恢复链路。")
        lines.append("")

        for index, result in enumerate(stage6_results, start=1):
            lines.append(f"[STAGE6_RESULT_{index}: {result.agent_name}]")
            lines.append(f"issue_type: {result.issue_type}")
            lines.append(f"status: {result.status}")
            lines.append(f"is_primary: {result.is_primary}")
            lines.append(f"summary: {result.summary}")

            if result.evidence:
                lines.append("evidence:")
                for item in result.evidence[:20]:
                    lines.append(f"- {item}")

            if result.analysis:
                lines.append("analysis:")
                for item in result.analysis[:20]:
                    lines.append(f"- {item}")

            if result.raw_output:
                lines.append("raw_output_excerpt:")
                lines.append(result.raw_output[:3600])

            lines.append("")

        return "\n".join(lines)

    def _build_llm_context(self, route: Dict[str, Any], results: List[AgentResult]) -> str:
        """
        Convert route and AgentResult objects into compact text for LLM.

        Notes:
        1. skipped AgentResult should only explain why the agent was skipped.
           It must not be treated as completed diagnosis evidence.
        2. DynamicManagerAgent's raw_output is the V3 ExecutionPlan.
           It should be provided as execution plan context, not as fault evidence.
        3. LogDiagnosisAgent's raw_output is high-value diagnostic evidence,
           so it should provide more details through raw_output_excerpt.
        """
        lines = []

        has_stage6 = self._has_stage6_context(results)
        report_intent = route.get("report_intent") or "event_troubleshooting"
        report_context_type = (
            "monitoring_auto_recovery"
            if has_stage6
            else "traditional_troubleshooting"
        )
        if has_stage6 and report_intent == "post_notification":
            title_hint = "通知后状态报告"
        elif has_stage6:
            title_hint = "事件排障报告"
        else:
            title_hint = "多 Agent Linux 排障报告"

        lines.append("[REPORT_CONTEXT]")
        lines.append(f"report_context_type: {report_context_type}")
        lines.append(f"report_intent: {report_intent}")
        lines.append(f"report_title_hint: {title_hint}")
        lines.append("")

        if has_stage6:
            lines.append("[STAGE6_EXECUTIVE_CONTEXT]")
            lines.append(self._format_stage6_executive_context(results))
            lines.append("")

        lines.append("[ROUTE]")
        lines.append(f"issue_type: {route.get('issue_type')}")
        lines.append(f"primary_issue_type: {route.get('primary_issue_type')}")
        lines.append(f"secondary_issue_types: {route.get('secondary_issue_types')}")
        lines.append(f"all_detected_issue_types: {route.get('all_detected_issue_types')}")
        lines.append(f"confidence: {route.get('confidence')}")
        lines.append(f"log_path: {route.get('log_path')}")
        lines.append(f"routing_reason: {route.get('routing_reason')}")
        lines.append("")

        for result in results:
            lines.append(f"[AGENT_RESULT: {result.agent_name}]")
            lines.append(f"issue_type: {result.issue_type}")
            lines.append(f"status: {result.status}")
            lines.append(f"is_primary: {result.is_primary}")
            lines.append(f"summary: {result.summary}")

            # =========================
            # if 1：跳过的 Agent 只说明跳过原因
            # =========================
            if result.status == "skipped":
                lines.append("skip_note:")
                lines.append(
                    "- This agent was skipped by the V3 ExecutionPlan. "
                    "Do not treat it as a completed diagnosis."
                )

                if result.analysis:
                    lines.append("skip_reason:")
                    for item in result.analysis[:5]:
                        lines.append(f"- {item}")

                lines.append("")
                continue

            # =========================
            # if 2：DynamicManagerAgent 单独提供执行计划
            # =========================
            if result.agent_name == "DynamicManagerAgent":
                lines.append("manager_analysis:")
                for item in result.analysis[:8]:
                    lines.append(f"- {item}")

                if result.raw_output:
                    lines.append("execution_plan_excerpt:")
                    lines.append(result.raw_output[:1800])

                lines.append("")
                continue

            lines.append("evidence:")
            for item in result.evidence[:16]:
                lines.append(f"- {item}")

            lines.append("analysis:")
            for item in result.analysis[:12]:
                lines.append(f"- {item}")

            lines.append("recommended_checks:")
            for item in result.recommended_checks[:16]:
                lines.append(f"- {item}")

            lines.append("low_risk_actions:")
            for item in result.low_risk_actions[:12]:
                lines.append(f"- {item}")

            lines.append("manual_confirm_actions:")
            for item in result.manual_confirm_actions[:10]:
                lines.append(f"- {item}")

            lines.append("risk_notes:")
            for item in result.risk_notes[:10]:
                lines.append(f"- {item}")

            raw_excerpt = self._select_raw_output_excerpt(result)
            if raw_excerpt:
                lines.append("raw_output_excerpt:")
                lines.append(raw_excerpt)

            lines.append("")

        context = "\n".join(lines)

        if len(context) > self.max_context_chars:
            context = context[: self.max_context_chars] + "\n[CONTEXT_TRUNCATED]"

        return context

    def _select_raw_output_excerpt(self, result: AgentResult) -> str:
        """
        Select useful raw_output excerpts for LLMReportAgent.

        The goal is to give the LLM more details like timeline, matched patterns,
        primary failure and recommended checks, without flooding it with full logs.
        """
        raw = (result.raw_output or "").strip()
        if not raw:
            return ""

        if result.agent_name == "LogDiagnosisAgent":
            return self._extract_log_diagnosis_excerpt(raw, max_chars=5200)

        if result.agent_name == "DynamicManagerAgent":
            return raw[:1800]

        return raw[:2200]

    @staticmethod
    def _extract_log_diagnosis_excerpt(raw: str, max_chars: int = 2600) -> str:
        """
        Keep high-value sections from diagnose_mixed_log_file output.
        """
        sections = []

        important_headers = [
            "MIXED_LOG_DIAGNOSIS",
            "MATCHED_ERROR_PATTERNS",
            "RULE_BASED_DIAGNOSIS",
            "TIMELINE",
            "RECOMMENDED_NEXT_CHECKS",
        ]

        for header in important_headers:
            pattern = rf"\[{header}\]\n(.*?)(?=\n\[[A-Z0-9_]+\]|\Z)"
            match = re.search(pattern, raw, flags=re.DOTALL)
            if match:
                content = match.group(1).strip()
                if content:
                    sections.append(f"[{header}]\n{content}")

        excerpt = "\n\n".join(sections).strip()

        if not excerpt:
            excerpt = raw

        if len(excerpt) > max_chars:
            excerpt = excerpt[:max_chars] + "\n[RAW_OUTPUT_EXCERPT_TRUNCATED]"

        return excerpt

    @staticmethod
    def _postprocess_report(report: str) -> str:
        """
        Clean LLM report output:
        1. Remove conversational preface before the markdown title.
        2. Normalize overly absolute wording.
        3. Ensure report starts directly with the expected title when possible.
        """
        report = (report or "").strip()

        expected_titles = [
            "# 事件排障报告",
            "# 通知后状态报告",
            "# Agentic Linux 监控与自动恢复报告",
            "# 多 Agent Linux 排障报告",
        ]

        starts = [
            report.find(title)
            for title in expected_titles
            if report.find(title) != -1
        ]

        if starts:
            report = report[min(starts):].strip()

        replacements = {
            "唯一的决定性因素": "最直接的终止原因",
            "唯一决定性因素": "最直接的终止原因",
            "唯一原因": "直接原因",
            "直接且决定性因素": "最直接的终止原因",
            "根本原因": "主要原因",
            "必然导致": "可能导致",
        }

        for old, new in replacements.items():
            report = report.replace(old, new)

        return report

    @staticmethod
    def _build_system_prompt() -> str:
        return """
    你是 Agentic Linux Troubleshooting & Auto-Recovery 系统的报告生成 Agent。

    你只负责根据上游多 Agent 的结构化结果生成最终 Markdown 报告。
    你不能调用工具，不能重新诊断原始日志，不能编造新的命令输出，不能声称已经执行了没有执行的命令。
    
    你需要同时兼容两种报告模式：
    
    模式 A：传统多 Agent Linux 排障报告
    适用于没有 AutoRecoveryAgent / NotificationAgent / Stage 6 自动恢复上下文的情况。
    这时报告应保持原有高质量排障报告风格：
    - 主故障
    - 次要问题
    - 连锁影响
    - 时间线
    - 关键证据
    - 建议继续执行的只读检查
    - 低风险修复建议
    - 需要人工确认的操作
    - 风险提醒
    
    模式 B：Agentic Linux 监控与自动恢复报告
    适用于存在 AutoRecoveryAgent、auto_recovery、recovery_policy、NotificationAgent、notification、Notifier、apply_success、rerun_success、rollback_executed、recovered、manual_escalation、fix_id、daemon、heartbeat、project_status.json、alerts 等上下文的情况。
    这时报告必须优先围绕：
    监控事件 → ErrorEvent → 策略判断 → 自动修复 / 升级通知 → rerun 验证 → rollback → 通知 → 审计记录
    展开。
    
    标题规则：
    1. 如果 report_context_type=monitoring_auto_recovery 且 report_intent=event_troubleshooting，最终报告必须直接以：
       "# 事件排障报告"
       开头。
    2. 如果 report_context_type=monitoring_auto_recovery 且 report_intent=post_notification，最终报告必须直接以：
       "# 通知后状态报告"
       开头。
    3. 如果 report_context_type=traditional_troubleshooting，最终报告必须直接以：
       "# 多 Agent Linux 排障报告"
       开头。
    4. 不要输出“好的”“下面是”“这是报告”等寒暄或引导语。
    
    证据规则：
    1. 只基于 AgentResult、route、raw_output_excerpt、AutoRecoveryAgent、NotificationAgent、SessionOutcomeAgent 等已提供证据写报告。
    2. 不要编造未提供的路径、命令、diff、备份、通知结果、负责人响应或 API 调用结果。
    3. 如果结构化结果没有明确提供当前系统命令输出，不要写“来自当前系统检查”。
    4. 如果当前环境和故障环境不同，必须说明当前命令结果只能作为参考，不能代表故障节点。
    5. ExecutionPlan 只用于说明 V3 如何选择 Agent，不是故障证据本身。
    6. skipped Agent 不能作为证据来源。
    7. 只有 status=ok 的领域 Agent 才能作为“来自领域 Agent”的证据。
    
    Stage 6 自动恢复状态规则：
    1. 如果 recovered=True，或 apply_success=True 且 rerun_success=True，必须说明：
       - Agent 已完成自动恢复；
       - apply / remote-apply 成功；
       - rerun / remote-rerun 验证成功；
       - 当前复现命令已恢复。
    2. 如果 action=manual_escalation，必须说明：
       - Agent 已识别事件；
       - 该问题不在自动修复范围内；
       - 已进入负责人通知 / 人工处理流程；
       - 不要把它写成自动修复成功。
    3. 如果 rollback_executed=True，必须说明：
       - 自动恢复未通过验证；
       - 已执行 rollback；
       - 最终状态不能写 recovered。
    4. 如果 action=report_only，必须说明系统只生成报告，没有执行修复。
    5. 如果同一轮存在多个事件，必须逐个列出事件级处理结果，不能只写最后一个事件。
       每个 event 必须有独立 evidence、处置策略和状态说明。
    6. 如果主事件已恢复，但次要事件仍需人工处理，必须明确区分：
       - 已恢复事件；
       - 未恢复 / 已升级事件；
       - 后续负责人需要处理的风险。
    7. 多事件之间只能写“可能相关”或“需要进一步确认”，不得把可能关联写成确定因果。
       
    状态口径规则：
    1. event_recovery_status 表示当前事件的自动恢复状态。
    2. residual_risk_status 表示自动恢复后仍需人工关注的残留风险。
    3. 不得把 residual_risk_status=has_manual_risks 或 not_evaluated_by_auto_recovery 写成事件未恢复。
    4. 如果 apply_success=True、rerun_success=True、rollback_executed=False、recovered=True，则当前事件必须写为 recovered。
    5. 如果所有已处理事件 recovered=True，则自动恢复总体状态必须写为 recovered。
    6. 如果仍存在 disk/python_env 等未自动处理风险，应写成“残留风险”或“需要人工关注”，不能把总体恢复状态改写为 partially_recovered。
    7. partially_recovered 只能用于同时存在 recovered=True 和 recovered=False 的已处理事件。
       
    一致性硬规则：
    - 不允许把 recovered=False 的事件写成已恢复。
    - 不允许把 rollback_executed=True 的事件写成已恢复。
    - 如果一个事件 apply_success=True 但 rerun_success=False，该事件状态必须是 unresolved 或 rollback_done，不能是 recovered。
    - 如果多个事件中只有部分事件 recovered=True，总体状态必须是 partially_recovered。
    - 如果上下文提供了 cycle summary report 或 overall_status，必须按该状态写总体结论。
    
    远程与受控修改规则：
    1. 如果存在 remote_safe_apply 或 latest_remote_apply_success=True，必须说明远程配置修改是通过 /remote-apply 受控执行完成的。
    2. 如果存在 latest_remote_apply_success=True，必须说明应用了哪个 fix_id、修改了远程哪个配置、是否生成备份和 diff。
    3. 如果存在 remote_project_rerun 且最后一次返回码为 0，必须说明远程修复已通过 rerun 验证。
    4. 不要把远程 apply 说成本地 apply。
    5. 不要声称 Agent 执行了 sudo、rm、kill、scancel 等操作。
    6. 如果存在 safe_apply 或 latest_apply_success=True，必须说明本地修复是通过 /apply 受控执行完成的。
    7. 如果 latest_diff_path 或 latest_remote_diff_path 存在，应说明已生成 diff，可用于审计和回滚。
    
    传统排障质量要求：
    1. 明确区分：
       - 主故障；
       - 次要问题；
       - 连锁影响；
       - 证据来源；
       - 当前环境与故障环境差异。
    2. 不要使用过度绝对化表述，例如“唯一决定性因素”“必然导致”。可以说“直接原因”“主要原因”“可能加剧”。
    3. 如果 SessionOutcomeAgent.status=resolved，最终报告必须优先体现“问题已完成修复验证”，不要继续写成未解决状态。
    4. 如果问题已解决，只保留必要的后续验证建议，例如短轮次验证、正式训练前复查资源。
    5. 如果问题未解决，才重点给出继续排查命令。
    
    命令安全要求：
    1. bash 代码块中只能放只读检查命令或低风险查询命令。
    2. 命令要尽量朴素，不要写复杂的链式脚本。
    3. 避免使用 `cmd1 || cmd2 || echo ...` 这种复合命令。
    4. 以下危险命令不能出现在 bash 代码块中：
       - rm -rf
       - kill -9
       - scancel
       - sudo rm
       - chmod -R
       - chown -R
       - mkfs
       - dd
       - systemctl restart
       - reboot
       - shutdown
    5. 需要人工确认的操作只能用自然语言描述，不要放进 bash 代码块。
    6. 如果必须提到清理目录、终止进程、取消作业，只能写成：
       - 确认缓存目录不再被任务使用后，可由用户手动清理。
       - 确认进程归属后，可由用户手动终止。
       - 确认作业已失败且无需保留后，可由用户手动取消。
    7. 不要把不同错误行拼接成新的证据。
       端口冲突、磁盘空间不足、Python 依赖错误必须分别引用原始证据。
       如果原始日志中没有某个完整错误行，不要组合生成新的错误行。
       
    """

    @staticmethod
    def _build_user_prompt(context: str) -> str:
        is_stage6 = "report_context_type: monitoring_auto_recovery" in context
        is_post_notification = "report_intent: post_notification" in context

        if is_stage6:
            if is_post_notification:
                return f"""
    下面是 Monitoring & Auto-Recovery Agent 已经完成的结构化上下文，请生成通知后状态报告。

    重要要求：
    1. 报告必须直接以 "# 通知后状态报告" 开头。
    2. 报告必须明显短于事件排障报告。
    3. 不重复完整根因分析。
    4. 不重复长篇 raw evidence，只保留必要摘要。
    5. 只说明通知结果、事件状态、已生成工件和后续建议。
    6. 不要把 manual_escalation 写成 recovered。
    7. 不要声称系统执行了上下文中没有提供的操作。

    报告结构必须严格包含：

    # 通知后状态报告

    ## 一、通知结果
    说明 notification_status、notification_channels、通知是否完成、alert 或通知归档路径。

    ## 二、事件状态
    说明 event_type、action、status 或 event_recovery_status、是否 auto_recover、是否 manual_escalation、是否 recovered。

    ## 三、已生成工件
    列出已知 report 路径、post_notification report 路径、alert 路径、cycle summary 路径。缺失时写“当前上下文未提供”。

    ## 四、后续建议
    简短说明下一步：已恢复则继续观察；人工升级则查看事件排障报告并由负责人确认；未恢复则继续人工排查。
    如果涉及 kill、rm、pip install、systemctl、kubectl 等操作，只能写“需人工确认”，不能建议自动执行。

    下面是结构化上下文：

    {context}
    """

            return f"""
    下面是 Monitoring & Auto-Recovery Agent 已经完成的结构化上下文，请生成事件排障报告。

    重要要求：
    1. 报告必须直接以 "# 事件排障报告" 开头。
    2. 报告主线必须是：
       监控事件 → ErrorEvent → 策略判断 → 自动修复 / 升级通知 → rerun 验证 → rollback → 通知 → 审计。
    3. 必须包含 event_type、事件时间、action/status、是否 manual_escalation、是否 auto_recover、report/alert 工件路径和 raw evidence 摘要。
    4. 必须明确“当前系统已做了什么”和“仍需人工确认什么”。
    5. 如果有多个事件，必须逐个列出事件处理结果；每个 event 必须有独立 evidence、处置策略和状态说明。
    6. 多事件之间只能写“可能相关”“需要进一步确认”“从当前证据看”“尚不能确定”，不能把可能关联写成确定因果。
    7. 不要把 manual_escalation 写成 recovered。
    8. 不要把 recovered 事件和未处理风险混为一谈。
    9. 不要编造未提供的 diff、备份、通知渠道、负责人响应或命令输出。
    
    一致性硬规则：
    - 不允许把 recovered=False 的事件写成已恢复。
    - 不允许把 rollback_executed=True 的事件写成已恢复。
    - 如果一个事件 apply_success=True 但 rerun_success=False，该事件状态必须是 unresolved 或 rollback_done，不能是 recovered。
    - 如果多个事件中只有部分事件 recovered=True，总体状态必须是 partially_recovered。
    - 如果上下文提供了 cycle summary report 或 overall_status，必须按该状态写总体结论。
    
    如果当前报告是 event-specific report：
    - 只能对当前事件给出 recovered / rollback_done / unresolved 结论；
    - 不要给出本轮 overall_status；
    - 不要总结其他事件是否恢复；
    - 其他事件只能放在“相关上下文”中简单提及；
    - 多事件总体状态以 cycle_summary_report.md 为准。
    如果上下文包含 event_report_scope=single_event，报告标题仍为事件排障报告，但“事件概览”必须限定为当前事件，不得输出本轮 overall_status。
    
    请严格区分两个状态：
    - event_recovery_status：本次自动恢复是否解决了已处理事件；
    - residual_risk_status：是否仍存在未自动处理的次要风险。
    
    如果上下文中所有已处理事件均满足：
    apply_success=True, rerun_success=True, rollback_executed=False, recovered=True
    则“自动恢复最终状态”必须写为 recovered。
    
    如果还有 disk/python_env 等问题，只能写入“次要问题与后续风险”或“残留风险”，不得因此把 recovered 改成 partially_recovered。

    报告结构必须严格包含：

    # 事件排障报告

    ## 一、事件概览
    说明：
    - 监控到的项目；
    - event_type；
    - 事件时间；
    - fingerprint 或 event id；
    - action/status；
    - 是否 manual_escalation；
    - 是否 auto_recover；
    - report/alert 工件路径。

    ## 二、检测结果
    说明 Monitor、Detector、Policy 的处理结果，以及当前系统已做了什么。

    ## 三、关键证据
    提供 raw evidence 摘要、日志来源、关键错误行、fingerprint。不要拼接或改写原始证据。

    ## 四、影响判断
    说明从当前证据看可能影响什么服务或任务；不确定处必须写“可能”“需要进一步确认”或“尚不能确定”。

    ## 五、根因分析
    区分已确认事实、合理推断和信息缺口。不得把 process_crash 的根因直接归到 container_k8s，也不得反过来强行归因。

    ## 六、处置策略
    说明 action、fix_id、apply_success、rerun_success、rollback_executed、recovered，以及仍需人工确认什么。

    ## 七、安全边界
    必须说明：
    - 本次是否执行自动恢复；
    - 是否需要人工升级；
    - 是否存在危险操作；
    - 未经人工确认，不建议执行 kill、rm、pip install、systemctl、kubectl 等命令；
    - 如果事件是 manual_escalation，必须说明系统没有自动修复。

    ## 八、建议的人工排查步骤
    用自然语言列出人工排查步骤。危险操作只写“需人工确认”，不要放进 bash 代码块。

    ## 九、验证方式
    优先给出安全只读命令，例如 systemctl status、journalctl、tail、grep、df -h、free -h、nvidia-smi、kubectl get、kubectl describe。
    不要直接建议破坏性操作。

    ## 十、后续观察建议
    说明观察窗口、观察指标、重复出现时的升级方式和仍缺失的信息。

    下面是结构化上下文：

    {context}
    """

        return f"""
    下面是 V3 / Stage 4 / Stage 5 多 Agent 系统已经完成的结构化诊断结果，请你生成最终 Markdown 排障报告。

    重要要求：
    1. 报告风格要对齐“工程排障报告”，不要写成单纯的 Agent 执行日志。
    2. V3 ExecutionPlan 只作为辅助说明，不能替代故障分析。
    3. 主线必须是：主故障 → 次要问题 → 时间线 → 证据 → 检查命令 → 修复建议 → 风险提醒。
    4. 最终报告必须直接以 "# 多 Agent Linux 排障报告" 开头。

    报告结构必须包含：

    # 多 Agent Linux 排障报告

    ## 1. 总体结论
    直接说明主故障是什么、为什么它是最终失败原因、其他问题为什么只是次要问题。
    如果 SessionOutcomeAgent.status=resolved，必须说明最后一次 /rerun 返回码为 0，当前复现命令已不再报错，即当前问题已解决。

    ## 2. 修复与验证结果
    如果存在 SessionOutcomeAgent，请说明：
    - 初始运行如何失败；
    - 生成了什么修复计划；
    - 修复是用户手动完成，还是通过 /apply 受控执行完成；
    - 如果通过 /apply，应用了哪个 fix_id，修改了什么配置，是否生成备份和 diff；
    - 最后一次 /rerun 的返回码；
    - 当前复现命令是否已不再报错；
    - 问题是否可以认为已解决。
    如果存在远程修复证据，请说明：
    - 是否通过 /remote-apply 修改了远程配置；
    - 应用了哪个 fix_id；
    - 修改了哪个远程配置字段；
    - 是否生成远程备份和 diff；
    - 是否通过 /remote-rerun 验证成功。

    ## 3. V3 执行计划与多 Agent 协作结果
    简要说明：
    - agent_depth；
    - required_agents、optional_agents、skipped_agents；
    - 实际执行了哪些 Agent；
    - 这些 Agent 的结论是否一致。
    这一节要简洁，不要让执行计划喧宾夺主。

    ## 4. 主故障分析
    围绕主故障展开：
    - 关键证据；
    - 发生原因；
    - 为什么它比其他问题优先级更高；
    - 它如何直接导致任务失败。

    ## 5. 次要问题与连锁影响
    逐项分析 GPU、Python 环境、磁盘、端口、Slurm 等问题。
    每一项都要说明：
    - 证据；
    - 影响；
    - 是否直接导致失败；
    - 与主故障的关系。

    ## 6. 关键时间线
    使用表格：
    | 时间 | 事件 | 说明 |

    ## 7. 关键证据
    按来源分类：
    ### 来自初始日志
    ### 来自本地项目上下文扫描
    ### 来自远程服务器只读命令
    ### 来自远程日志
    ### 来自远程项目上下文
    ### 来自远程 /remote-apply 配置修改
    ### 来自远程 /remote-rerun
    ### 来自 /apply 配置修改和 diff
    ### 来自项目重新运行 / rerun
    ### 来自领域 Agent

    ## 8. 建议继续执行的只读检查命令
    如果问题已解决，重点给出：
    - 保留当前修复配置；
    - 做短轮次验证；
    - 正式训练前复查资源；
    - 保存修复记录。
    如果问题未解决，才给出继续排查命令。

    ## 9. 低风险修复建议
    如果最后一次 /rerun 返回码为 0，则问题已解决，不需要给出大量修复建议。

    ## 10. 需要人工确认的操作
    只用自然语言描述，不要把 rm、kill、scancel 放进 bash 代码块。

    ## 11. 风险提醒与信息不足项
    说明：
    - 当前分析是否只基于日志；
    - 是否缺少故障节点实时命令；
    - 哪些操作有风险；
    - 哪些信息还需要补充。

    下面是结构化诊断结果：

    {context}
    """
