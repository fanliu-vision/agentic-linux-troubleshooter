from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.log_tool import diagnose_mixed_log_file
from tools.gpu_tool import check_gpu_status
from tools.disk_tool import check_disk_usage
from tools.slurm_tool import diagnose_slurm_text, check_slurm_queue, check_slurm_nodes
from tools.python_env_tool import diagnose_python_error_text, check_python_environment
from tools.shell_tool import run_shell_command


@dataclass
class AgentResult:
    """
    Standard result object returned by every domain agent.

    Each domain agent should return structured evidence instead of a free-form essay.
    ReportAgent will use these fields to produce the final report.
    """

    agent_name: str
    issue_type: str
    status: str
    is_primary: bool = False
    summary: str = ""
    evidence: List[str] = field(default_factory=list)
    analysis: List[str] = field(default_factory=list)
    recommended_checks: List[str] = field(default_factory=list)
    low_risk_actions: List[str] = field(default_factory=list)
    manual_confirm_actions: List[str] = field(default_factory=list)
    risk_notes: List[str] = field(default_factory=list)
    raw_output: str = ""

    def to_markdown(self) -> str:
        lines = [
            f"## {self.agent_name}",
            f"- issue_type: `{self.issue_type}`",
            f"- status: `{self.status}`",
            f"- is_primary: `{self.is_primary}`",
            f"- summary: {self.summary or '<empty>'}",
        ]

        def add_list(title: str, items: List[str]) -> None:
            lines.append(f"\n### {title}")
            if items:
                for item in items:
                    lines.append(f"- {item}")
            else:
                lines.append("- <empty>")

        add_list("Evidence", self.evidence)
        add_list("Analysis", self.analysis)
        add_list("Recommended Checks", self.recommended_checks)
        add_list("Low Risk Actions", self.low_risk_actions)
        add_list("Manual Confirm Actions", self.manual_confirm_actions)
        add_list("Risk Notes", self.risk_notes)

        return "\n".join(lines)


def _extract_section(text: str, section_name: str) -> str:
    """
    Extract a section from tool output.

    Example section header:
    [TIMELINE]
    """
    pattern = rf"\[{re.escape(section_name)}\]\n(.*?)(?=\n\[[A-Z0-9_]+\]|\Z)"
    match = re.search(pattern, text, flags=re.DOTALL)
    return match.group(1).strip() if match else ""


def _extract_field(text: str, field_name: str) -> str:
    prefix = f"{field_name}:"
    for line in text.splitlines():
        if line.startswith(prefix):
            return line.split(":", 1)[1].strip()
    return ""


def _contains_any(text: str, keywords: List[str]) -> bool:
    lower_text = text.lower()
    return any(keyword.lower() in lower_text for keyword in keywords)


def _collect_lines(text: str, keywords: List[str], max_lines: int = 12) -> List[str]:
    lines = []
    for line in text.splitlines():
        if _contains_any(line, keywords):
            lines.append(line.strip())
    return lines[-max_lines:]

def _build_domain_search_text(mixed_diagnosis: str) -> str:
    """
    Build a cleaner text block for domain agents to extract evidence.

    We intentionally avoid using the whole [MATCHED_ERROR_PATTERNS] section,
    because it is often a compact dictionary containing all issue keywords,
    which can pollute every domain agent's evidence.
    """
    timeline = _extract_section(mixed_diagnosis, "TIMELINE")
    rule_diagnosis = _extract_section(mixed_diagnosis, "RULE_BASED_DIAGNOSIS")
    next_checks = _extract_section(mixed_diagnosis, "RECOMMENDED_NEXT_CHECKS")

    primary_failure_line = _extract_field(mixed_diagnosis, "primary_failure_line")
    primary_issue_type = _extract_field(mixed_diagnosis, "primary_issue_type")
    secondary_issue_types = _extract_field(mixed_diagnosis, "secondary_issue_types")

    parts = [
        f"primary_issue_type: {primary_issue_type}",
        f"secondary_issue_types: {secondary_issue_types}",
        f"primary_failure_line: {primary_failure_line}",
        "",
        "[RULE_BASED_DIAGNOSIS]",
        rule_diagnosis,
        "",
        "[TIMELINE]",
        timeline,
        "",
        "[RECOMMENDED_NEXT_CHECKS]",
        next_checks,
    ]

    return "\n".join(parts)


def _first_matching_line(text: str, keywords: List[str]) -> str:
    """
    Return the first line that contains any of the keywords.
    """
    for line in text.splitlines():
        if _contains_any(line, keywords):
            return line.strip()
    return ""


def _make_summary_from_evidence(
    issue_type: str,
    evidence: List[str],
    is_primary: bool,
) -> str:
    """
    Build a diagnosis-style summary for each domain agent.
    """
    if not evidence:
        return f"未发现 {issue_type} 相关证据。"

    joined = "\n".join(evidence)

    if issue_type == "gpu":
        if _contains_any(joined, ["HIP out of memory", "torch.OutOfMemoryError", "oom-kill"]):
            if is_primary:
                return "日志显示训练发生 HIP/DCU 显存不足，并伴随 Slurm oom-kill，说明这是最终导致作业失败的主故障。"
            return "日志中存在 GPU/DCU 显存不足信号，可能是任务失败或性能异常的重要因素。"
        if _contains_any(joined, ["CUDA out of memory"]):
            return "日志显示训练发生 CUDA 显存不足，需要检查 batch size、混合精度和显存占用。"
        return "日志中存在 GPU/DCU 相关异常，需要结合显存占用和训练配置继续判断。"

    if issue_type == "disk":
        return "日志显示存在磁盘空间不足或缓存写入失败，可能影响数据缓存、训练效率或检查点保存。"

    if issue_type == "python_env":
        return "日志显示 Python 解释器、pip 路径或依赖安装存在不一致，可能导致模块缺失和运行环境混乱。"

    if issue_type == "network_port":
        return "日志显示端口被占用，主要影响 TensorBoard、监控服务或 Web 服务启动。"

    if issue_type == "slurm":
        if _contains_any(joined, ["oom-kill", "accelerator memory constraints"]):
            return "日志显示 Slurm 在运行阶段检测到 oom-kill，说明作业因内存或加速器资源超限被终止。"
        if _contains_any(joined, ["JobState=PENDING", "Reason=Resources"]):
            return "日志显示 Slurm 作业曾因 Resources 处于 Pending，属于资源等待问题。"
        return "日志中存在 Slurm 调度或节点状态相关信息，需要结合 squeue、scontrol 和 sinfo 判断。"

    return f"检测到 {issue_type} 相关证据。"


class ManagerAgent:
    """
    ManagerAgent controls the multi-agent workflow.

    It does not call LLM. It decides which domain agents should run
    based on the content-aware route result.
    """

    def __init__(self, route: Dict[str, Any]) -> None:
        self.route = route

    def selected_issue_types(self) -> List[str]:
        issue_types = self.route.get("all_detected_issue_types") or []
        if not issue_types:
            issue_type = self.route.get("issue_type", "unknown")
            issue_types = [issue_type]
        return [x for x in issue_types if x not in {"unknown", "log"}]

    def primary_issue_type(self) -> str:
        return str(self.route.get("primary_issue_type") or self.route.get("issue_type") or "unknown")

    def should_run_log_agent(self) -> bool:
        return bool(self.route.get("log_path"))

    def to_result(self) -> AgentResult:
        return AgentResult(
            agent_name="ManagerAgent",
            issue_type="manager",
            status="ok",
            summary="完成内容感知路由和子 Agent 调度规划。",
            evidence=[
                f"primary_issue_type={self.primary_issue_type()}",
                f"secondary_issue_types={self.route.get('secondary_issue_types')}",
                f"all_detected_issue_types={self.route.get('all_detected_issue_types')}",
                f"log_path={self.route.get('log_path')}",
                f"confidence={self.route.get('confidence')}",
            ],
            analysis=[
                "ManagerAgent 根据路由结果选择需要运行的领域 Agent。",
                "复杂日志场景优先运行 LogDiagnosisAgent，再把结构化结果交给领域 Agent 分析。",
            ],
            recommended_checks=self.route.get("recommended_checks", []),
            raw_output=str(self.route),
        )


class LogDiagnosisAgent:
    """
    LogDiagnosisAgent performs the main mixed-log diagnosis.
    """

    def run(self, log_path: str) -> AgentResult:
        if not log_path:
            return AgentResult(
                agent_name="LogDiagnosisAgent",
                issue_type="log",
                status="skipped",
                summary="没有提供日志文件路径，跳过日志诊断。",
            )

        diagnosis = diagnose_mixed_log_file(log_path)

        primary = _extract_field(diagnosis, "primary_issue_type")
        secondary = _extract_field(diagnosis, "secondary_issue_types")
        primary_failure_line = _extract_field(diagnosis, "primary_failure_line")
        timeline = _extract_section(diagnosis, "TIMELINE")
        matched = _extract_section(diagnosis, "MATCHED_ERROR_PATTERNS")
        next_checks = _extract_section(diagnosis, "RECOMMENDED_NEXT_CHECKS")

        return AgentResult(
            agent_name="LogDiagnosisAgent",
            issue_type="log",
            status="ok",
            is_primary=False,
            summary=f"完成混合日志诊断，主问题为 {primary or '<unknown>'}。",
            evidence=[
                f"log_path={log_path}",
                f"primary_issue_type={primary}",
                f"secondary_issue_types={secondary}",
                f"primary_failure_line={primary_failure_line}",
            ],
            analysis=[
                "日志诊断工具已提取主故障、次要问题、错误模式和时间线。",
                "后续领域 Agent 将基于该结构化结果进行专项分析。",
            ],
            recommended_checks=[
                line.lstrip("- ").strip()
                for line in next_checks.splitlines()
                if line.strip()
            ],
            raw_output=diagnosis,
        )


class GPUDiagnosisAgent:
    """
    GPUAgent analyzes CUDA/HIP/DCU OOM related evidence.
    """

    def run(self, mixed_diagnosis: str, is_primary: bool) -> AgentResult:
        search_text = _build_domain_search_text(mixed_diagnosis)

        evidence = _collect_lines(
            search_text,
            [
                "HIP out of memory",
                "CUDA out of memory",
                "OutOfMemoryError",
                "oom-kill",
                "accelerator memory constraints",
                "hy-smi snapshot",
                "PYTORCH_HIP_ALLOC_CONF",
                "PYTORCH_CUDA_ALLOC_CONF",
                "vram",
                "primary_failure_line",
            ],
        )

        status = "ok" if evidence else "not_relevant"

        analysis = []
        if evidence:
            analysis.extend(
                [
                    "检测到 GPU/DCU 显存不足或 OOM kill 相关证据。",
                    "如果日志中出现 slurmstepd oom-kill，说明作业可能被调度系统因内存或加速器显存超限终止。",
                    "需要优先检查 batch size、精度设置、梯度检查点和显存分配策略。",
                ]
            )

        return AgentResult(
            agent_name="GPUAgent",
            issue_type="gpu",
            status=status,
            is_primary=is_primary,
            summary=_make_summary_from_evidence("gpu", evidence, is_primary),
            evidence=evidence,
            analysis=analysis,
            recommended_checks=[
                "hy-smi",
                "echo $PYTORCH_HIP_ALLOC_CONF",
                "检查训练配置中的 batch size、precision、gradient checkpointing",
                "确认是否存在残留训练进程占用显存",
            ],
            low_risk_actions=[
                "降低 batch size 后重新测试",
                "启用 bf16/fp16 混合精度训练",
                "启用 gradient checkpointing 降低显存峰值",
                "设置 PYTORCH_HIP_ALLOC_CONF=expandable_segments:True 缓解显存碎片问题",
            ],
            manual_confirm_actions=[
                "确认进程归属后，由用户手动终止残留训练进程。",
            ],
            risk_notes=[
                "当前机器 GPU 状态不能直接代表远程故障节点。",
                "不要把 kill -9 作为首选操作。",
                "显存分配策略只能缓解碎片问题，不能替代降低 batch size。",
            ],
            raw_output="\n".join(evidence),
        )

class DiskDiagnosisAgent:
    """
    DiskAgent analyzes disk full and inode related evidence.
    """

    def run(self, mixed_diagnosis: str, is_primary: bool) -> AgentResult:
        search_text = _build_domain_search_text(mixed_diagnosis)
        evidence = _collect_lines(
            search_text,
            [
                "No space left on device",
                "Errno 28",
                "df -h",
                "df -ih",
                "du -sh",
                "/tmp",
                "cache",
                "disk_full",
            ],
        )

        status = "ok" if evidence else "not_relevant"

        return AgentResult(
            agent_name="DiskAgent",
            issue_type="disk",
            status=status,
            is_primary=is_primary,
            summary=_make_summary_from_evidence("disk", evidence, is_primary),
            evidence=evidence,
            analysis=[
                "检测到磁盘空间不足或缓存写入失败相关信号。",
                "如果 No space left on device 出现在 /tmp 缓存目录，可能导致缓存构建失败、数据加载变慢或 checkpoint 写入失败。",
                "需要确认是文件系统空间满、inode 用尽，还是用户配额限制。",
            ] if evidence else [],
            recommended_checks=[
                "df -h /tmp",
                "df -ih /tmp",
                "du -sh /tmp/$USER",
                "du -sh ~/.cache",
            ],
            low_risk_actions=[
                "将缓存目录切换到空间更大的路径。",
                "减少数据缓存或关闭非必要缓存。",
            ],
            manual_confirm_actions=[
                "确认缓存目录不再被任务使用后，由用户手动清理对应目录。",
            ],
            risk_notes=[
                "不要在报告中直接复制执行 rm -rf。",
                "清理缓存前应确认没有正在运行的任务依赖该目录。",
            ],
            raw_output="\n".join(evidence),
        )


class PythonEnvDiagnosisAgent:
    """
    PythonEnvAgent analyzes Python interpreter, pip mismatch and missing packages.
    """

    def run(self, mixed_diagnosis: str, is_primary: bool) -> AgentResult:
        search_text = _build_domain_search_text(mixed_diagnosis)
        evidence = _collect_lines(
            search_text,
            [
                "ModuleNotFoundError",
                "No module named",
                "ImportError",
                "python interpreter",
                "pip path",
                "do not belong to the same environment",
                "CONDA_PREFIX",
                "VIRTUAL_ENV",
                "python=/",
                "pip=/",
                "PyYAML",
            ],
        )

        status = "ok" if evidence else "not_relevant"

        return AgentResult(
            agent_name="PythonEnvAgent",
            issue_type="python_env",
            status=status,
            is_primary=is_primary,
            summary=_make_summary_from_evidence("python_env", evidence, is_primary),
            evidence=evidence,
            analysis=[
                "检测到 Python 解释器与 pip/conda 环境不一致或依赖缺失。",
                "这类问题常导致包已经安装但运行时仍然 ModuleNotFoundError。",
                "应使用当前运行脚本的解释器执行 python -m pip，而不是直接使用不确定来源的 pip。",
            ] if evidence else [],
            recommended_checks=[
                "which python",
                "which pip",
                "python -c \"import sys; print(sys.executable)\"",
                "python -m pip --version",
                "python -m pip show PyYAML",
            ],
            low_risk_actions=[
                "激活正确的虚拟环境或 conda 环境。",
                "使用当前解释器执行 python -m pip install 缺失包。",
            ],
            manual_confirm_actions=[],
            risk_notes=[
                "不要盲目使用 sudo pip install。",
                "不要在系统 Python 和项目虚拟环境之间混装依赖。",
            ],
            raw_output="\n".join(evidence),
        )


class NetworkDiagnosisAgent:
    """
    NetworkAgent analyzes port conflict and service binding problems.
    """

    def run(self, mixed_diagnosis: str, is_primary: bool) -> AgentResult:
        search_text = _build_domain_search_text(mixed_diagnosis)
        evidence = _collect_lines(
            search_text,
            [
                "Address already in use",
                "Errno 98",
                "port 9100",
                "9100",
                "TensorBoard",
                "ss -lntp",
                "lsof",
                "bind",
                "connection refused",
            ],
        )

        status = "ok" if evidence else "not_relevant"

        return AgentResult(
            agent_name="NetworkAgent",
            issue_type="network_port",
            status=status,
            is_primary=is_primary,
            summary=_make_summary_from_evidence("network_port", evidence, is_primary),
            evidence=evidence,
            analysis=[
                "检测到端口占用相关错误，常见于 TensorBoard、Node Exporter 或本地服务重复启动。",
                "端口冲突通常不会直接导致训练失败，但会影响监控或服务暴露。",
            ] if evidence else [],
            recommended_checks=[
                "ss -lntp | grep 9100",
                "lsof -i :9100",
                "确认服务绑定地址是 127.0.0.1 还是 0.0.0.0",
            ],
            low_risk_actions=[
                "更换 TensorBoard 或服务端口，例如改用 9101。",
                "避免多个服务绑定同一个端口。",
            ],
            manual_confirm_actions=[
                "确认进程归属后，由用户手动终止占用端口的进程。",
            ],
            risk_notes=[
                "不要在未确认进程归属前直接 kill 进程。",
            ],
            raw_output="\n".join(evidence),
        )


class SlurmDiagnosisAgent:
    """
    SlurmAgent analyzes Slurm pending, node status and Slurm OOM kill evidence.
    """

    def run(self, mixed_diagnosis: str, is_primary: bool) -> AgentResult:
        search_text = _build_domain_search_text(mixed_diagnosis)
        evidence = _collect_lines(
            search_text,
            [
                "Submitted batch job",
                "JobState=PENDING",
                "Reason=Resources",
                "NODELIST(REASON)",
                "slurmstepd",
                "oom-kill",
                "squeue",
                "scontrol",
                "sinfo",
                "ReqNodeNotAvail",
                "DOWN",
                "DRAIN",
                "accelerator memory constraints",
            ],
        )

        status = "ok" if evidence else "not_relevant"

        return AgentResult(
            agent_name="SlurmAgent",
            issue_type="slurm",
            status=status,
            is_primary=is_primary,
            summary=_make_summary_from_evidence("slurm", evidence, is_primary),
            evidence=evidence,
            analysis=[
                "检测到 Slurm 调度或作业状态相关信息。",
                "如果只是 Reason=Resources 且作业之后启动，则它是早期等待状态，不是最终失败原因。",
                "如果出现 slurmstepd oom-kill，则表示作业在运行阶段因内存或加速器资源超限被终止。",
            ] if evidence else [],
            recommended_checks=[
                "squeue -j <JOB_ID>",
                "scontrol show job <JOB_ID>",
                "sinfo -N -l",
                "scontrol show node <NODE_NAME>",
            ],
            low_risk_actions=[
                "根据 Pending reason 调整资源申请或等待资源释放。",
                "检查作业脚本中的 partition、gres、time、mem 配置。",
            ],
            manual_confirm_actions=[
                "确认作业已失败且不需要保留后，由用户手动取消作业。",
            ],
            risk_notes=[
                "不要尝试修改节点状态，节点 DOWN/DRAIN 需要管理员处理。",
                "不要取消其他用户作业。",
            ],
            raw_output="\n".join(evidence),
        )


class MultiAgentOrchestrator:
    """
    Orchestrates all domain agents.

    This is the Stage-3 V1 manager pipeline.
    """

    def __init__(self, route: Dict[str, Any]) -> None:
        self.route = route
        self.manager = ManagerAgent(route)

    def run(self) -> Dict[str, Any]:
        results: List[AgentResult] = []

        manager_result = self.manager.to_result()
        results.append(manager_result)

        log_path = self.route.get("log_path") or ""
        primary_type = self.manager.primary_issue_type()
        issue_types = self.manager.selected_issue_types()

        log_result: Optional[AgentResult] = None
        mixed_diagnosis_text = ""

        if self.manager.should_run_log_agent():
            log_agent = LogDiagnosisAgent()
            log_result = log_agent.run(str(log_path))
            results.append(log_result)
            mixed_diagnosis_text = log_result.raw_output

        # 如果没有日志诊断结果，则让领域 Agent 基于空文本运行，通常会返回 not_relevant
        domain_text = mixed_diagnosis_text

        if "gpu" in issue_types:
            results.append(GPUDiagnosisAgent().run(domain_text, is_primary=(primary_type == "gpu")))

        if "disk" in issue_types:
            results.append(DiskDiagnosisAgent().run(domain_text, is_primary=(primary_type == "disk")))

        if "python_env" in issue_types:
            results.append(PythonEnvDiagnosisAgent().run(domain_text, is_primary=(primary_type == "python_env")))

        if "network_port" in issue_types:
            results.append(NetworkDiagnosisAgent().run(domain_text, is_primary=(primary_type == "network_port")))

        if "slurm" in issue_types:
            results.append(SlurmDiagnosisAgent().run(domain_text, is_primary=(primary_type == "slurm")))

        return {
            "route": self.route,
            "results": results,
            "mixed_diagnosis": mixed_diagnosis_text,
        }