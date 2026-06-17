from __future__ import annotations

from typing import Any, Dict, List, Set

from agents.agent_protocol import AgentDecision, AgentDepth, AgentTask, ExecutionPlan


ISSUE_TO_AGENT_KEY = {
    "gpu": "gpu",
    "disk": "disk",
    "python_env": "python_env",
    "network_port": "network_port",
    "slurm": "slurm",
}


class DynamicManagerAgent:
    """
    V3 ManagerAgent.

    It generates an ExecutionPlan instead of directly running domain agents.
    """

    def __init__(self, route: Dict[str, Any], agent_depth: AgentDepth = "balanced") -> None:
        self.route = route
        self.agent_depth = agent_depth

    def plan(self) -> ExecutionPlan:
        primary_issue = self._primary_issue_type()
        secondary_issues = self._secondary_issue_types()
        all_issues = self._all_issue_types(primary_issue, secondary_issues)

        required_issue_types = self._required_issue_types(primary_issue, secondary_issues)
        optional_issue_types = self._optional_issue_types(primary_issue, secondary_issues, required_issue_types)
        skipped_issue_types = self._skipped_issue_types(all_issues, required_issue_types, optional_issue_types)

        required_tasks = [
            self._build_task(issue_type, run_mode="required", is_primary=(issue_type == primary_issue))
            for issue_type in required_issue_types
        ]

        optional_tasks = [
            self._build_task(issue_type, run_mode="optional", is_primary=False)
            for issue_type in optional_issue_types
        ]

        skipped_tasks = [
            self._build_task(issue_type, run_mode="skipped", is_primary=False)
            for issue_type in skipped_issue_types
        ]

        decisions = []
        for task in required_tasks:
            decisions.append(
                AgentDecision(
                    agent_key=task.agent_key,
                    issue_type=task.issue_type,
                    decision="required",
                    reason=task.reason,
                    confidence=0.9,
                )
            )

        for task in optional_tasks:
            decisions.append(
                AgentDecision(
                    agent_key=task.agent_key,
                    issue_type=task.issue_type,
                    decision="optional",
                    reason=task.reason,
                    confidence=0.7,
                )
            )

        for task in skipped_tasks:
            decisions.append(
                AgentDecision(
                    agent_key=task.agent_key,
                    issue_type=task.issue_type,
                    decision="skipped",
                    reason=task.reason,
                    confidence=0.6,
                )
            )

        return ExecutionPlan(
            primary_issue_type=primary_issue,
            secondary_issue_types=secondary_issues,
            all_detected_issue_types=all_issues,
            agent_depth=self.agent_depth,
            required_tasks=required_tasks,
            optional_tasks=optional_tasks,
            skipped_tasks=skipped_tasks,
            decisions=decisions,
            report_focus=self._build_report_focus(primary_issue, secondary_issues),
            budget_note=self._build_budget_note(),
        )

    def _primary_issue_type(self) -> str:
        return str(
            self.route.get("primary_issue_type")
            or self.route.get("issue_type")
            or "unknown"
        )

    def _secondary_issue_types(self) -> List[str]:
        values = self.route.get("secondary_issue_types") or []
        return [x for x in values if x not in {"unknown", "log"}]

    def _all_issue_types(self, primary: str, secondary: List[str]) -> List[str]:
        values = self.route.get("all_detected_issue_types") or []
        cleaned = [x for x in values if x not in {"unknown", "log"}]

        if primary not in cleaned and primary not in {"unknown", "log"}:
            cleaned.insert(0, primary)

        for item in secondary:
            if item not in cleaned:
                cleaned.append(item)

        return cleaned

    def _required_issue_types(self, primary: str, secondary: List[str]) -> List[str]:
        if primary in {"unknown", "log"}:
            return []

        if self.agent_depth == "minimal":
            return [primary]

        if self.agent_depth == "full":
            return self._all_issue_types(primary, secondary)

        # balanced
        required = [primary]

        # Slurm 与 GPU OOM 强相关：如果日志里同时有 slurmstepd oom-kill，SlurmAgent 应作为 required
        if primary == "gpu" and "slurm" in secondary:
            required.append("slurm")

        # 如果主问题是 Slurm，GPU 作为资源/加速器相关问题也应该被重视
        if primary == "slurm" and "gpu" in secondary:
            required.append("gpu")

        return self._dedupe(required)

    def _optional_issue_types(
        self,
        primary: str,
        secondary: List[str],
        required: List[str],
    ) -> List[str]:
        if self.agent_depth == "minimal":
            return []

        if self.agent_depth == "full":
            return []

        # balanced：执行与主问题有一定关联的次要 Agent，跳过弱相关项
        optional = []

        for issue in secondary:
            if issue in required:
                continue

            if primary == "gpu":
                # disk / python_env 可能影响训练流程，network_port 多数只是监控端口问题
                if issue in {"disk", "python_env"}:
                    optional.append(issue)
                elif issue == "network_port":
                    # port 冲突通常不直接导致训练失败，balanced 下先跳过
                    continue

            elif primary == "slurm":
                if issue in {"gpu", "disk", "python_env"}:
                    optional.append(issue)

            elif primary == "disk":
                if issue in {"python_env", "slurm"}:
                    optional.append(issue)

            elif primary == "python_env":
                if issue in {"disk", "slurm"}:
                    optional.append(issue)

            else:
                # 其他场景保守执行一个次要 Agent
                optional.append(issue)

        return self._dedupe(optional)

    def _skipped_issue_types(
        self,
        all_issues: List[str],
        required: List[str],
        optional: List[str],
    ) -> List[str]:
        return [
            issue
            for issue in all_issues
            if issue not in set(required + optional)
        ]

    def _build_task(self, issue_type: str, run_mode: str, is_primary: bool) -> AgentTask:
        agent_key = ISSUE_TO_AGENT_KEY.get(issue_type, issue_type)

        reason = self._task_reason(issue_type, run_mode, is_primary)

        return AgentTask(
            agent_key=agent_key,
            issue_type=issue_type,
            run_mode=run_mode,  # type: ignore[arg-type]
            is_primary=is_primary,
            task_description=self._task_description(issue_type, is_primary),
            reason=reason,
            expected_output=self._expected_output(issue_type),
        )

    def _task_reason(self, issue_type: str, run_mode: str, is_primary: bool) -> str:
        if is_primary:
            return f"{issue_type} 是路由判断出的主故障类型，必须运行对应 Agent。"

        if run_mode == "required":
            return f"{issue_type} 与主故障存在强关联，需要作为 required Agent 执行。"

        if run_mode == "optional":
            return f"{issue_type} 是次要问题，balanced 模式下作为 optional Agent 执行。"

        return f"{issue_type} 是弱相关次要问题，当前 agent_depth={self.agent_depth}，暂不执行。"

    def _task_description(self, issue_type: str, is_primary: bool) -> str:
        prefix = "主故障分析" if is_primary else "次要问题分析"

        descriptions = {
            "gpu": "分析 CUDA/HIP/DCU OOM、显存峰值、显存碎片、Slurm oom-kill 关系。",
            "disk": "分析 No space left on device、inode、缓存目录和 checkpoint 写入问题。",
            "python_env": "分析 Python 解释器、pip/conda 环境不一致和依赖缺失问题。",
            "network_port": "分析端口占用、服务监听和 TensorBoard/Exporter 启动失败问题。",
            "slurm": "分析 Slurm Pending reason、节点状态、资源申请和 slurmstepd 错误。",
        }

        return f"{prefix}：{descriptions.get(issue_type, '分析该领域相关问题。')}"

    def _expected_output(self, issue_type: str) -> List[str]:
        outputs = {
            "gpu": [
                "GPU/DCU OOM 证据",
                "是否为主故障",
                "显存相关低风险修复建议",
            ],
            "disk": [
                "磁盘空间不足证据",
                "是否影响主流程",
                "只读检查命令",
            ],
            "python_env": [
                "解释器与 pip 是否一致",
                "缺失依赖",
                "修复环境的低风险建议",
            ],
            "network_port": [
                "端口冲突证据",
                "是否影响主任务",
                "端口检查命令",
            ],
            "slurm": [
                "作业状态",
                "Pending reason",
                "slurmstepd 错误与资源约束关系",
            ],
        }

        return outputs.get(issue_type, ["结构化诊断结果"])

    def _build_report_focus(self, primary: str, secondary: List[str]) -> str:
        if primary == "gpu":
            return (
                "重点说明 GPU/DCU OOM 是否为最终失败原因，"
                "并区分 Slurm oom-kill、磁盘、Python 环境和端口问题的次要影响。"
            )

        if primary == "slurm":
            return "重点说明 Slurm 调度状态、Pending reason、节点状态和资源申请是否合理。"

        if primary == "disk":
            return "重点说明磁盘空间或 inode 问题是否直接导致任务失败。"

        if primary == "python_env":
            return "重点说明 Python 环境不一致和依赖缺失如何影响运行。"

        if primary == "network_port":
            return "重点说明端口占用是否影响服务启动或训练监控。"

        return "重点说明主故障和次要问题之间的关系。"

    def _build_budget_note(self) -> str:
        if self.agent_depth == "minimal":
            return "minimal 模式：只执行主故障相关 Agent，适合快速定位。"
        if self.agent_depth == "full":
            return "full 模式：执行所有检测到的问题 Agent，适合完整复盘。"
        return "balanced 模式：执行主故障 Agent 和强相关次要 Agent，适合默认排障。"

    @staticmethod
    def _dedupe(items: List[str]) -> List[str]:
        result = []
        for item in items:
            if item not in result:
                result.append(item)
        return result