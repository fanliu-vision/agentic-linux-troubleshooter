from __future__ import annotations

from typing import Any, Dict, List

from agents.agent_protocol import AgentDepth, AgentTask
from agents.agent_registry import get_domain_agent_class
from agents.domain_agents import AgentResult, LogDiagnosisAgent
from agents.manager_agent import DynamicManagerAgent


class MultiAgentOrchestratorV3:
    """
    V3 orchestrator with dynamic execution planning.

    Compared with V2:
    - V2 runs agents according to all_detected_issue_types.
    - V3 first creates ExecutionPlan, then runs required/optional agents
      according to agent_depth.
    """

    def __init__(
        self,
        route: Dict[str, Any],
        agent_depth: AgentDepth = "balanced",
        run_optional: bool | None = None,
    ) -> None:
        self.route = route
        self.agent_depth = agent_depth

        # If not explicitly specified:
        # minimal: do not run optional
        # balanced/full: run optional included in plan
        if run_optional is None:
            self.run_optional = agent_depth in {"balanced", "full"}
        else:
            self.run_optional = run_optional

        self.manager = DynamicManagerAgent(route=route, agent_depth=agent_depth)

    def run(self) -> Dict[str, Any]:
        plan = self.manager.plan()

        results: List[AgentResult] = []

        manager_result = self._manager_result(plan)
        results.append(manager_result)

        log_path = self.route.get("log_path") or ""
        mixed_diagnosis = ""

        if log_path:
            log_result = LogDiagnosisAgent().run(str(log_path))
            results.append(log_result)
            mixed_diagnosis = log_result.raw_output

        tasks_to_run = plan.required_tasks[:]

        if self.run_optional:
            tasks_to_run.extend(plan.optional_tasks)

        executed_agent_keys = set()

        for task in tasks_to_run:
            if task.issue_type in executed_agent_keys:
                continue

            result = self._run_domain_agent(task, mixed_diagnosis)
            results.append(result)
            executed_agent_keys.add(task.issue_type)

        # Record skipped agents as lightweight results, so ReportAgent can explain why not executed.
        for task in plan.skipped_tasks:
            results.append(
                AgentResult(
                    agent_name=f"{self._agent_display_name(task.issue_type)}",
                    issue_type=task.issue_type,
                    status="skipped",
                    is_primary=False,
                    summary=f"根据 V3 执行计划跳过：{task.reason}",
                    analysis=[task.reason],
                )
            )

        return {
            "route": self.route,
            "execution_plan": plan,
            "results": results,
            "mixed_diagnosis": mixed_diagnosis,
        }

    def _run_domain_agent(self, task: AgentTask, mixed_diagnosis: str) -> AgentResult:
        agent_class = get_domain_agent_class(task.issue_type)

        if agent_class is None:
            return AgentResult(
                agent_name=f"UnknownAgent[{task.issue_type}]",
                issue_type=task.issue_type,
                status="skipped",
                is_primary=task.is_primary,
                summary=f"未找到 issue_type={task.issue_type} 对应的领域 Agent。",
                risk_notes=["请检查 agents/agent_registry.py 是否注册了对应 Agent。"],
            )

        agent = agent_class()

        # 当前 DomainAgent 的统一接口是 run(mixed_diagnosis, is_primary)
        try:
            return agent.run(mixed_diagnosis, is_primary=task.is_primary)
        except TypeError:
            return AgentResult(
                agent_name=f"{agent_class.__name__}",
                issue_type=task.issue_type,
                status="error",
                is_primary=task.is_primary,
                summary="领域 Agent 接口不兼容，期望 run(mixed_diagnosis, is_primary)。",
                risk_notes=["请检查 domain_agents.py 中该 Agent 的 run 方法签名。"],
            )
        except Exception as exc:
            return AgentResult(
                agent_name=f"{agent_class.__name__}",
                issue_type=task.issue_type,
                status="error",
                is_primary=task.is_primary,
                summary=f"领域 Agent 运行失败：{type(exc).__name__}: {exc}",
                risk_notes=["请检查该领域 Agent 的证据提取逻辑。"],
            )

    def _manager_result(self, plan) -> AgentResult:
        return AgentResult(
            agent_name="DynamicManagerAgent",
            issue_type="manager",
            status="ok",
            is_primary=False,
            summary="已生成 V3 动态执行计划。",
            evidence=[
                f"primary_issue_type={plan.primary_issue_type}",
                f"secondary_issue_types={plan.secondary_issue_types}",
                f"all_detected_issue_types={plan.all_detected_issue_types}",
                f"agent_depth={plan.agent_depth}",
            ],
            analysis=[
                plan.report_focus,
                plan.budget_note,
            ],
            recommended_checks=[],
            raw_output=plan.to_markdown(),
        )

    @staticmethod
    def _agent_display_name(issue_type: str) -> str:
        mapping = {
            "gpu": "GPUAgent",
            "disk": "DiskAgent",
            "python_env": "PythonEnvAgent",
            "network_port": "NetworkAgent",
            "slurm": "SlurmAgent",
        }
        return mapping.get(issue_type, f"SkippedAgent[{issue_type}]")