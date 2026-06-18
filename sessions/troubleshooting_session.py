from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from agents.multi_agent_orchestrator_v3 import MultiAgentOrchestratorV3
from agents.report_agent import LLMReportAgent, ReportAgent
from routers import classify_issue_dict, format_route_context
from tools.readonly_executor import ReadonlyCommandExecutor
from agents.domain_agents import AgentResult
from fixers import RemediationPlanner, FixPlan
from tools.project_runner import ProjectRunner
from fixers.apply_executor import SafeApplyExecutor
from context import ProjectContextCollector, ProjectContext
from fixers.remote_apply_executor import RemoteSafeApplyExecutor

from tools.remote_ssh_executor import (
    RemoteSSHProfile,
    RemoteReadonlySSHExecutor,
)



@dataclass
class EvidenceItem:
    source: str
    title: str
    content: str
    command: str = ""
    issue_type: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    def to_text(self) -> str:
        return (
            f"\n[Evidence]\n"
            f"source: {self.source}\n"
            f"title: {self.title}\n"
            f"issue_type: {self.issue_type}\n"
            f"command: {self.command if self.command else '<none>'}\n"
            f"created_at: {self.created_at}\n\n"
            f"{self.content}\n"
        )


@dataclass
class NextActionPlan:
    issue_type: str
    commands: List[str]
    explanation: List[str]
    missing_info: List[str]

    def to_markdown(self) -> str:
        lines = [
            "## 下一步检查计划",
            f"- 当前主问题类型：`{self.issue_type}`",
            "",
            "### 建议执行的只读检查命令",
        ]

        if self.commands:
            lines.append("```bash")
            for command in self.commands:
                lines.append(command)
            lines.append("```")
        else:
            lines.append("- 暂无建议命令。")

        lines.append("")
        lines.append("### 命令用途说明")
        for item in self.explanation:
            lines.append(f"- {item}")

        lines.append("")
        lines.append("### 当前仍缺少的信息")
        for item in self.missing_info:
            lines.append(f"- {item}")

        return "\n".join(lines)


class TroubleshootingSession:
    """
    Stage 4 interactive troubleshooting session.

    It starts from logs or pasted error text, then supports multi-turn evidence collection,
    readonly command execution and final report generation.
    """

    def __init__(
            self,
            session_id: str | None = None,
            output_root: str = "outputs/sessions",
            agent_depth: str = "balanced",
            report_mode: str = "auto",
            project_dir: str = "",
            run_command: str = "",
            rerun_timeout: int = 120,
    ) -> None:
        self.session_id = session_id or uuid.uuid4().hex[:8]
        self.output_dir = Path(output_root) / self.session_id
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.agent_depth = agent_depth
        self.report_mode = report_mode

        self.evidence_items: List[EvidenceItem] = []
        self.route: Dict[str, Any] = {}
        self.route_context: str = ""
        self.combined_log_path = self.output_dir / "combined_evidence.log"
        self.executor = ReadonlyCommandExecutor()
        self.project_dir = project_dir
        self.run_command = run_command
        self.rerun_timeout = rerun_timeout
        self.latest_fix_plan: FixPlan | None = None
        self.has_fix_plan_generated = False
        self.latest_rerun_success = False
        self.latest_rerun_log_path = ""
        self.latest_rerun_return_code: int | None = None
        self.latest_rerun_command = ""
        self.latest_apply_success = False
        self.latest_apply_fix_id = ""
        self.latest_apply_summary = ""
        self.latest_diff_path = ""
        self.project_context: ProjectContext | None = None
        self.latest_context_path = ""
        self.remote_profile: RemoteSSHProfile | None = None
        self.latest_remote_rerun_success: bool = False
        self.latest_remote_rerun_stdout: str = ""
        self.latest_remote_rerun_stderr: str = ""
        self.latest_remote_rerun_return_code: int | None = None
        self.remote_executor = RemoteReadonlySSHExecutor()
        self.latest_remote_context_path = ""
        self.latest_remote_rerun_success = False
        self.latest_remote_rerun_return_code: int | None = None
        self.latest_remote_rerun_log_path = ""
        self.latest_remote_rerun_command = ""
        self.latest_remote_project_dir = ""
        self.latest_remote_apply_success = False
        self.latest_remote_apply_fix_id = ""
        self.latest_remote_apply_summary = ""
        self.latest_remote_diff_path = ""
        self.latest_remote_apply_project_dir = ""
        self.latest_auto_recovery_result_text = ""
        self.latest_auto_recovery_action = ""
        self.latest_auto_recovery_fix_id = ""
        self.latest_auto_recovery_apply_success = False
        self.latest_auto_recovery_rerun_success = False
        self.latest_auto_recovery_rollback_executed = False
        self.latest_notification_result_text = ""
        self.latest_notification_status = ""
        self.latest_notification_channels = []

    def set_project_dir(self, project_dir: str) -> str:
        path = Path(project_dir).expanduser().resolve()

        if not path.exists():
            return f"项目目录不存在：{path}"

        if not path.is_dir():
            return f"当前路径不是目录：{path}"

        self.project_dir = str(path)
        return f"项目目录已设置为：{self.project_dir}"

    def set_run_command(self, run_command: str) -> str:
        run_command = run_command.strip()

        if not run_command:
            return "运行命令不能为空。"

        self.run_command = run_command
        return f"项目运行命令已设置为：{self.run_command}"

    def set_remote_profile(self, user: str, host: str, port: int = 22, name: str = "default") -> str:
        self.remote_profile = RemoteSSHProfile(
            user=user,
            host=host,
            port=port,
            name=name,
        )

        return (
            "远程 SSH Profile 已设置：\n"
            f"- name: `{name}`\n"
            f"- user: `{user}`\n"
            f"- host: `{host}`\n"
            f"- port: `{port}`\n\n"
            "说明：当前版本只支持远程只读命令，不支持远程 apply / rm / kill / scancel / sudo。"
        )

    def remote_status(self) -> str:
        if not self.remote_profile:
            return "尚未设置远程 SSH Profile。请使用 `/remote-set <user>@<host> [port]`。"

        return (
            "## 当前远程 SSH Profile\n\n"
            f"- name: `{self.remote_profile.name}`\n"
            f"- user: `{self.remote_profile.user}`\n"
            f"- host: `{self.remote_profile.host}`\n"
            f"- port: `{self.remote_profile.port}`\n"
            "\n当前远程能力：只读命令、远程日志读取、远程项目上下文扫描。"
        )

    def run_remote_readonly_command(self, command: str) -> str:
        if not self.remote_profile:
            return "尚未设置远程 SSH Profile。请先使用 `/remote-set <user>@<host> [port]`。"

        result = self.remote_executor.run(self.remote_profile, command)
        evidence_text = result.to_evidence_text()

        self.evidence_items.append(
            EvidenceItem(
                source="remote_readonly_command",
                title=f"Remote command result: {command}",
                content=evidence_text,
                command=f"/remote-run {command}",
                issue_type="remote",
            )
        )

        self._refresh_route()

        return evidence_text

    def read_remote_log(self, remote_path: str, lines: int = 400) -> str:
        if not self.remote_profile:
            return "尚未设置远程 SSH Profile。请先使用 `/remote-set <user>@<host> [port]`。"

        result = self.remote_executor.read_remote_log_tail(
            self.remote_profile,
            remote_path=remote_path,
            lines=lines,
        )

        evidence_text = result.to_evidence_text()

        self.evidence_items.append(
            EvidenceItem(
                source="remote_log",
                title=f"Remote log tail: {remote_path}",
                content=evidence_text,
                command=f"/remote-log {remote_path} {lines}",
                issue_type="remote_log",
            )
        )

        self._refresh_route()

        return (
                evidence_text
                + "\n\n远程日志尾部已加入当前 session 证据。"
        )

    def collect_remote_context(self, remote_project_dir: str) -> str:
        if not self.remote_profile:
            return "尚未设置远程 SSH Profile。请先使用 `/remote-set <user>@<host> [port]`。"

        result = self.remote_executor.collect_remote_project_context(
            self.remote_profile,
            remote_project_dir=remote_project_dir,
        )

        evidence_text = result.to_evidence_text()

        context_path = self.output_dir / "remote_project_context.txt"
        context_path.write_text(evidence_text, encoding="utf-8")
        self.latest_remote_context_path = str(context_path)

        self.evidence_items.append(
            EvidenceItem(
                source="remote_project_context",
                title=f"Remote project context: {remote_project_dir}",
                content=evidence_text,
                command=f"/remote-context {remote_project_dir}",
                issue_type="remote_context",
            )
        )

        self._refresh_route()

        return (
            "## 远程项目上下文扫描结果\n\n"
            f"- remote_project_dir: `{remote_project_dir}`\n"
            f"- saved_to: `{context_path}`\n\n"
            "```text\n"
            f"{evidence_text}\n"
            "```\n\n"
            "远程项目上下文已加入当前 session 证据。"
        )

    def rerun_remote_project(self, remote_project_dir: str = "") -> str:
        """
        Stage 5B: rerun a project command on a remote server.

        It executes the configured self.run_command under remote_project_dir.
        The result is added to EvidenceStore and used by later /fix and /report.
        """
        if not self.remote_profile:
            return "尚未设置远程 SSH Profile。请先使用 `/remote-set <user>@<host> [port]`。"

        if not self.run_command:
            return "尚未设置项目运行命令。请先使用 `/command <运行命令>` 或启动参数 `--run-command` 设置。"

        remote_dir = remote_project_dir.strip() or self.project_dir.strip()

        if not remote_dir:
            return "尚未设置远程项目目录。请使用 `/remote-rerun <远程项目目录>`。"

        result = self.remote_executor.run_remote_project(
            profile=self.remote_profile,
            remote_project_dir=remote_dir,
            run_command=self.run_command,
        )

        evidence_text = result.to_evidence_text()

        self.evidence_items.append(
            EvidenceItem(
                source="remote_project_rerun",
                title=f"Remote project rerun: {self.run_command}",
                content=evidence_text,
                command=f"/remote-rerun {remote_dir}",
                issue_type="remote_rerun",
            )
        )

        self.latest_remote_rerun_success = result.return_code == 0
        self.latest_remote_rerun_return_code = result.return_code
        self.latest_remote_rerun_command = self.run_command
        self.latest_remote_project_dir = remote_dir

        # 复用已有 resolved 状态，方便 SessionOutcomeAgent 和报告层识别
        self.latest_rerun_success = result.return_code == 0
        self.latest_rerun_return_code = result.return_code
        self.latest_rerun_command = f"remote:{self.run_command}"
        self.latest_rerun_log_path = f"remote:{self.remote_profile.target}:{remote_dir}"

        self._refresh_route()

        if result.return_code == 0:
            return (
                "## 远程重新运行结果：成功\n\n"
                f"- remote: `{self.remote_profile.target}`\n"
                f"- remote_project_dir: `{remote_dir}`\n"
                f"- run_command: `{self.run_command}`\n"
                f"- return_code: `{result.return_code}`\n\n"
                "远程项目命令已成功运行。当前远程复现命令可以认为已不再报错。"
            )

        return (
            "## 远程重新运行结果：失败\n\n"
            f"- remote: `{self.remote_profile.target}`\n"
            f"- remote_project_dir: `{remote_dir}`\n"
            f"- run_command: `{self.run_command}`\n"
            f"- return_code: `{result.return_code}`\n\n"
            "远程运行错误已自动加入当前会话证据，并已更新诊断上下文。\n"
            "你可以继续输入：\n\n"
            "- `/next` 查看下一步检查命令；\n"
            "- `/fix` 生成修复计划；\n"
            "- `/report` 生成最终报告。"
        )

    def remote_apply_fix(self, fix_id: str, remote_project_dir: str) -> str:
        """
        Stage 5C: apply a supported fix on remote server.

        This modifies only registered JSON config fields under remote_project_dir.
        """
        if not self.remote_profile:
            return "尚未设置远程 SSH Profile。请先使用 `/remote-set <user>@<host> [port]`。"

        fix_id = fix_id.strip()
        remote_project_dir = remote_project_dir.strip()

        if not fix_id:
            return "fix_id 不能为空。例如：`/remote-apply fix-network-1 /path/to/project`"

        if not remote_project_dir:
            return "remote_project_dir 不能为空。例如：`/remote-apply fix-network-1 /path/to/project`"

        executor = RemoteSafeApplyExecutor(
            profile=self.remote_profile,
            session_dir=str(self.output_dir),
        )

        result = executor.apply(fix_id=fix_id, remote_project_dir=remote_project_dir)
        evidence_text = result.to_markdown()

        self.latest_remote_apply_success = result.success
        self.latest_remote_apply_fix_id = fix_id
        self.latest_remote_apply_summary = result.message
        self.latest_remote_apply_project_dir = remote_project_dir

        diff_paths = [
            item.diff_path for item in result.edit_results if item.diff_path
        ]
        self.latest_remote_diff_path = diff_paths[-1] if diff_paths else ""

        self.evidence_items.append(
            EvidenceItem(
                source="remote_safe_apply",
                title=f"Remote safe apply result: {fix_id}",
                content=evidence_text,
                command=f"/remote-apply {fix_id} {remote_project_dir}",
                issue_type="remote_apply",
            )
        )

        self._refresh_route()

        if result.success:
            return (
                    result.to_markdown()
                    + "\n\n"
                    + "远程配置修改已完成。建议继续执行 `/remote-diff` 查看差异，"
                    + "然后执行 `/remote-rerun <远程项目目录>` 验证。"
            )

        return (
                result.to_markdown()
                + "\n\n"
                + "远程 apply 未成功。请检查该 fix_id 是否支持，或远程项目中是否存在对应配置字段。"
        )

    def show_latest_remote_diff(self) -> str:
        if not self.remote_profile:
            return "尚未设置远程 SSH Profile。"

        executor = RemoteSafeApplyExecutor(
            profile=self.remote_profile,
            session_dir=str(self.output_dir),
        )

        ok, diff_path, text = executor.read_latest_diff()

        if not ok:
            return f"读取远程 diff 失败：{text}"

        self.latest_remote_diff_path = diff_path

        return (
            "## 最近一次远程配置 diff\n\n"
            f"- remote: `{self.remote_profile.target}`\n"
            f"- diff_path: `{diff_path}`\n\n"
            "```diff\n"
            f"{text}\n"
            "```"
        )

    def remote_rollback_latest_apply(self) -> str:
        if not self.remote_profile:
            return "尚未设置远程 SSH Profile。"

        executor = RemoteSafeApplyExecutor(
            profile=self.remote_profile,
            session_dir=str(self.output_dir),
        )

        result = executor.rollback_latest()
        evidence_text = result.to_markdown()

        self.evidence_items.append(
            EvidenceItem(
                source="remote_safe_apply_rollback",
                title="Remote rollback latest safe apply",
                content=evidence_text,
                command="/remote-rollback",
                issue_type="remote_rollback",
            )
        )

        self._refresh_route()

        return (
                result.to_markdown()
                + "\n\n"
                + "远程回滚后可以使用 `/remote-rerun <远程项目目录>` 验证项目状态。"
        )

    def remote_recover_with_fix(self, fix_id: str, remote_project_dir: str) -> str:
        """
        Stage 5C automated remote recovery loop:

        1. remote rerun
        2. if failed, remote apply fix
        3. remote rerun again
        """
        if not self.remote_profile:
            return "尚未设置远程 SSH Profile。"

        if not self.run_command:
            return "尚未设置 run_command。请先使用 `/command <运行命令>`。"

        lines = [
            "# 远程自动修复闭环",
            "",
            f"- fix_id: `{fix_id}`",
            f"- remote_project_dir: `{remote_project_dir}`",
            "",
            "## Step 1: 远程 rerun",
        ]

        first = self.rerun_remote_project(remote_project_dir)
        lines.append(first)

        if self.latest_remote_rerun_success:
            lines.append("")
            lines.append("远程项目首次 rerun 已成功，无需 apply。")
            return "\n\n".join(lines)

        lines.append("")
        lines.append("## Step 2: 远程 apply")
        apply_result = self.remote_apply_fix(fix_id, remote_project_dir)
        lines.append(apply_result)

        if not self.latest_remote_apply_success:
            lines.append("")
            lines.append("远程 apply 失败，停止自动修复闭环。")
            return "\n\n".join(lines)

        lines.append("")
        lines.append("## Step 3: 再次远程 rerun")
        second = self.rerun_remote_project(remote_project_dir)
        lines.append(second)

        if self.latest_remote_rerun_success:
            lines.append("")
            lines.append("远程自动修复闭环完成：apply 后 rerun 成功。")
        else:
            lines.append("")
            lines.append("远程 apply 后 rerun 仍失败，需要继续排查或 rollback。")

        return "\n\n".join(lines)

    def project_status(self) -> str:
        return (
            "## 当前项目运行配置\n\n"
            f"- project_dir: `{self.project_dir if self.project_dir else '<not set>'}`\n"
            f"- run_command: `{self.run_command if self.run_command else '<not set>'}`\n"
            f"- rerun_timeout: `{self.rerun_timeout}` 秒\n"
        )

    def collect_project_context(self) -> str:
        """
        Read-only scan project context and store it as evidence.
        """
        if not self.project_dir:
            return "尚未设置项目目录。请先使用 `/project <项目目录>` 设置。"

        collector = ProjectContextCollector(
            project_dir=self.project_dir,
            run_command=self.run_command,
        )

        context = collector.collect()
        self.project_context = context

        context_text = context.to_markdown()
        context_path = self.output_dir / "project_context.md"
        context_path.write_text(context_text, encoding="utf-8")
        self.latest_context_path = str(context_path)

        self.evidence_items.append(
            EvidenceItem(
                source="project_context",
                title="Read-only project context scan",
                content=context_text,
                command="/context",
                issue_type="project_context",
            )
        )

        self._refresh_route()

        return (
                context_text
                + "\n\n"
                + f"项目上下文已保存到：`{context_path}`\n"
                + "后续 `/fix` 会结合该上下文生成更准确的修复计划。"
        )

    def _strong_fix_issue_types(self) -> List[str]:
        """
        Decide which issue types have strong enough evidence to generate fix actions.
        This avoids generating unrelated fixes, such as python_env fixes triggered only by .venv.
        """
        text = self._combined_evidence_text().lower()
        route_primary = str(
            self.route.get("primary_issue_type")
            or self.route.get("issue_type")
            or "unknown"
        )
        route_secondary = self.route.get("secondary_issue_types") or []
        route_types = [route_primary] + [x for x in route_secondary if x != route_primary]

        strong_types: List[str] = []

        def add(issue_type: str) -> None:
            if issue_type in route_types and issue_type not in strong_types:
                strong_types.append(issue_type)

        if any(k in text for k in [
            "hip out of memory",
            "cuda out of memory",
            "outofmemoryerror",
            "oom-kill",
            "accelerator memory constraints",
            "vram used memory",
            "vram free memory",
        ]):
            add("gpu")

        if any(k in text for k in [
            "no space left on device",
            "errno 28",
            "disk quota exceeded",
            "df -h /tmp",
            "df -ih /tmp",
        ]):
            add("disk")

        if any(k in text for k in [
            "modulenotfounderror",
            "importerror",
            "no module named",
            "do not belong to the same environment",
            "python interpreter",
            "python=/",
            "pip=/",
        ]):
            add("python_env")

        if any(k in text for k in [
            "address already in use",
            "errno 98",
            "connection refused",
            "ss -lntp",
            "lsof -i",
        ]):
            add("network_port")

        if any(k in text for k in [
            "slurmstepd",
            "jobstate=pending",
            "reason=resources",
            "submitted batch job",
            "scontrol show job",
            "squeue",
            "sinfo",
        ]):
            add("slurm")

        # 主问题如果没有强证据，但 route 置信度高，仍允许生成主问题修复计划
        confidence = float(self.route.get("confidence") or 0)
        if not strong_types and route_primary not in {"unknown", "log"} and confidence >= 0.7:
            strong_types.append(route_primary)

        return strong_types

    def generate_fix_plan(self) -> str:
        """
        Generate remediation plan based on current route and evidence.
        The plan does not execute modifications.
        """
        self._refresh_route()

        evidence_text = self._combined_evidence_text()
        if self.project_context is None and self.project_dir:
            collector = ProjectContextCollector(
                project_dir=self.project_dir,
                run_command=self.run_command,
            )
            self.project_context = collector.collect()
            context_path = self.output_dir / "project_context.md"
            context_path.write_text(self.project_context.to_markdown(), encoding="utf-8")
            self.latest_context_path = str(context_path)

        planner = RemediationPlanner()
        allowed_issue_types = self._strong_fix_issue_types()
        plan = planner.build_fix_plan(
            self.route,
            evidence_text,
            allowed_issue_types=allowed_issue_types,
            project_context=self.project_context,
        )

        self.latest_fix_plan = plan
        self.has_fix_plan_generated = True

        save_path = self.output_dir / "fix_plan.md"
        save_path.write_text(plan.to_markdown(), encoding="utf-8")

        return (
                plan.to_markdown()
                + "\n\n"
                + f"用于生成修复计划的强证据问题类型：`{allowed_issue_types}`\n\n"
                + f"修复计划已保存到：`{save_path}`\n"
                + "请根据修复计划手动修改项目或环境，完成后输入 `/rerun` 重新运行项目。"
        )

    def apply_fix(self, fix_id: str) -> str:
        """
        Apply a supported fix in a controlled and reversible way.

        This method only supports registered safe config edits.
        It creates backup and diff through SafeApplyExecutor.
        """
        if not self.project_dir:
            return "尚未设置项目目录。请先使用 `/project <项目目录>` 设置。"

        fix_id = fix_id.strip()
        if not fix_id:
            return "fix_id 不能为空。例如：`/apply fix-network-1`"

        executor = SafeApplyExecutor(
            project_dir=self.project_dir,
            session_dir=str(self.output_dir),
        )

        result = executor.apply(fix_id)

        self.latest_apply_success = result.success
        self.latest_apply_fix_id = fix_id
        self.latest_apply_summary = result.message

        diff_paths = [
            item.diff_path for item in result.edit_results if item.diff_path
        ]
        self.latest_diff_path = diff_paths[-1] if diff_paths else ""

        evidence_text = result.to_markdown()

        self.evidence_items.append(
            EvidenceItem(
                source="safe_apply",
                title=f"Safe apply result: {fix_id}",
                content=evidence_text,
                command=f"/apply {fix_id}",
                issue_type="safe_apply",
            )
        )

        self._refresh_route()

        if result.success:
            return (
                    result.to_markdown()
                    + "\n\n"
                    + "配置修改已完成。建议继续执行 `/diff` 查看修改差异，"
                    + "然后执行 `/rerun` 验证项目是否恢复。"
            )

        return (
                result.to_markdown()
                + "\n\n"
                + "该 fix_id 未成功应用。请检查是否支持自动 apply，"
                + "或根据 `/fix` 中的建议手动修复。"
        )

    def rollback_latest_apply(self) -> str:
        """
        Roll back the latest safe apply.
        """
        if not self.project_dir:
            return "尚未设置项目目录。请先使用 `/project <项目目录>` 设置。"

        executor = SafeApplyExecutor(
            project_dir=self.project_dir,
            session_dir=str(self.output_dir),
        )

        result = executor.rollback_latest()

        evidence_text = result.to_markdown()

        self.evidence_items.append(
            EvidenceItem(
                source="safe_apply_rollback",
                title="Rollback latest safe apply",
                content=evidence_text,
                command="/rollback",
                issue_type="rollback",
            )
        )

        self._refresh_route()

        return (
                result.to_markdown()
                + "\n\n"
                + "回滚后可以使用 `/rerun` 验证项目是否回到修改前状态。"
        )

    def show_latest_diff(self) -> str:
        """
        Show the latest diff file generated by /apply or /rollback.
        """
        if self.latest_diff_path:
            path = Path(self.latest_diff_path)
            if path.exists():
                return (
                        f"## 最近一次配置 diff\n\n"
                        f"- diff_path: `{path}`\n\n"
                        "```diff\n"
                        + path.read_text(encoding="utf-8", errors="ignore")
                        + "\n```"
                )

        patch_dir = self.output_dir / "patches"
        if not patch_dir.exists():
            return "当前还没有 diff 记录。"

        diff_files = sorted(patch_dir.glob("*.diff"), key=lambda p: p.stat().st_mtime)
        if not diff_files:
            return "当前还没有 diff 记录。"

        latest = diff_files[-1]
        self.latest_diff_path = str(latest)

        return (
                f"## 最近一次配置 diff\n\n"
                f"- diff_path: `{latest}`\n\n"
                "```diff\n"
                + latest.read_text(encoding="utf-8", errors="ignore")
                + "\n```"
        )

    def rerun_project(self) -> str:
        """
        Rerun the configured project command.

        If success:
            mark as solved in evidence.
        If failed:
            add new stdout/stderr as evidence and refresh route.
        """
        if not self.project_dir:
            return "尚未设置项目目录。请先使用 `/project <项目目录>` 设置。"

        if not self.run_command:
            return "尚未设置项目运行命令。请先使用 `/command <运行命令>` 设置。"

        runner = ProjectRunner(
            project_dir=self.project_dir,
            run_command=self.run_command,
            output_dir=str(self.output_dir),
            timeout=self.rerun_timeout,
        )

        result = runner.run()
        evidence_text = result.to_evidence_text()

        if result.success:
            self.latest_rerun_success = True
            self.latest_rerun_log_path = result.log_path
            self.latest_rerun_return_code = result.return_code
            self.latest_rerun_command = self.run_command

            self.evidence_items.append(
                EvidenceItem(
                    source="project_rerun",
                    title="Project rerun succeeded",
                    content=evidence_text,
                    command=self.run_command,
                    issue_type="resolved",
                )
            )
            self._refresh_route()

            solved_path = self.output_dir / "resolved.md"
            solved_text = (
                "# 项目重新运行成功\n\n"
                f"- project_dir: `{self.project_dir}`\n"
                f"- run_command: `{self.run_command}`\n"
                f"- rerun_log: `{result.log_path}`\n\n"
                "当前重新运行命令返回码为 0，未检测到新的运行错误。"
            )
            solved_path.write_text(solved_text, encoding="utf-8")

            return (
                "## 重新运行结果：成功\n\n"
                f"- return_code: `{result.return_code}`\n"
                f"- log_path: `{result.log_path}`\n\n"
                "项目命令已成功运行。当前问题可以认为已解决，或至少当前复现命令不再报错。\n"
                f"结果摘要已保存到：`{solved_path}`"
            )

        self.latest_rerun_success = False
        self.latest_rerun_log_path = result.log_path
        self.latest_rerun_return_code = result.return_code
        self.latest_rerun_command = self.run_command

        self.evidence_items.append(
            EvidenceItem(
                source="project_rerun",
                title="Project rerun failed",
                content=evidence_text,
                command=self.run_command,
                issue_type="rerun_failure",
            )
        )
        self._refresh_route()

        return (
            "## 重新运行结果：失败\n\n"
            f"- return_code: `{result.return_code}`\n"
            f"- timed_out: `{result.timed_out}`\n"
            f"- log_path: `{result.log_path}`\n\n"
            "新的运行错误已自动加入当前会话证据，并已更新诊断上下文。\n"
            "你可以继续输入：\n\n"
            "- `/next` 查看下一步检查命令；\n"
            "- `/fix` 生成更新后的修复计划；\n"
            "- `/report` 生成新的最终报告。"
        )

    def _combined_evidence_text(self) -> str:
        return "\n".join(item.content for item in self.evidence_items)

    def _has_evidence_keywords(self, keywords: list[str]) -> bool:
        text = self._combined_evidence_text().lower()
        return any(keyword.lower() in text for keyword in keywords)

    def _build_interactive_evidence_result(
            self,
            evidence_items: List[EvidenceItem] | None = None,
    ) -> AgentResult:
        """
        Build an AgentResult that explicitly summarizes interactive evidence
        collected during the session. This helps LLMReportAgent use user-added
        evidence and readonly command outputs.
        """
        items = list(evidence_items) if evidence_items is not None else self.evidence_items

        evidence_lines = []
        analysis_lines = []

        for item in items:
            evidence_lines.append(
                f"[{item.source}] {item.title} command={item.command if item.command else '<none>'}"
            )

            content_lower = item.content.lower()

            if item.source == "remote_readonly_command":
                analysis_lines.append(
                    "会话中包含远程服务器只读命令结果，可用于判断真实服务器环境状态。"
                )

            if item.source == "remote_log":
                analysis_lines.append(
                    "会话中包含远程日志尾部内容，可用于基于真实服务器日志更新诊断。"
                )

            if item.source == "remote_project_context":
                analysis_lines.append(
                    "会话中包含远程项目上下文扫描结果，可用于定位远程配置文件、日志文件和依赖文件。"
                )

            if item.source == "remote_project_rerun":
                analysis_lines.append(
                    "会话中包含远程项目 rerun 结果，可用于判断远程复现命令是否已经恢复。"
                )

            if item.source == "safe_apply":
                analysis_lines.append(
                    "会话中包含 /apply 受控配置修改结果，可用于判断修复动作是否已由 Agent 安全执行。"
                )

            if item.source == "safe_apply_rollback":
                analysis_lines.append(
                    "会话中包含 /rollback 回滚结果，可用于判断配置是否已恢复到修改前状态。"
                )

            if item.source == "project_context":
                analysis_lines.append(
                    "会话中包含项目上下文扫描结果，可用于定位配置文件、依赖文件和可修复字段。"
                )

            if item.source == "remote_safe_apply":
                analysis_lines.append(
                    "会话中包含远程 /remote-apply 受控配置修改结果，可用于判断远程修复动作是否已执行。"
                )

            if item.source == "remote_safe_apply_rollback":
                analysis_lines.append(
                    "会话中包含远程 /remote-rollback 回滚结果，可用于判断远程配置是否已恢复。"
                )

            if "vram used memory" in content_lower or "vram free memory" in content_lower:
                analysis_lines.append(
                    "交互式证据中包含显存占用信息，可用于判断 OOM 是否与实时显存不足相关。"
                )

            if "df -h /tmp" in content_lower or ("filesystem" in content_lower and "use%" in content_lower):
                analysis_lines.append(
                    "交互式只读命令中包含 /tmp 文件系统使用情况，可用于区分当前环境与故障节点环境。"
                )

            if "command_result" in content_lower:
                analysis_lines.append(
                    "会话中包含只读命令执行结果，这些结果只代表当前命令执行环境。"
                )

        raw_parts = []
        for item in items:
            raw_parts.append(item.to_text())

        return AgentResult(
            agent_name="InteractiveEvidenceAgent",
            issue_type="interactive_evidence",
            status="ok",
            is_primary=False,
            summary="汇总交互式会话中用户补充的证据和只读命令执行结果。",
            evidence=evidence_lines,
            analysis=self._dedupe_text(analysis_lines),
            recommended_checks=[],
            low_risk_actions=[],
            manual_confirm_actions=[],
            risk_notes=[
                "交互式命令结果只代表命令实际执行的当前环境，不一定等同于故障节点。",
                "如果当前环境与故障节点不同，应优先以故障节点上的命令结果为准。",
            ],
            raw_output="\n\n".join(raw_parts),
        )

    def _build_rerun_history_text(
            self,
            evidence_items: List[EvidenceItem] | None = None,
    ) -> str:
        """
        Collect all project rerun evidence as raw text.
        This helps the report layer understand whether the final rerun succeeded.
        """
        items = list(evidence_items) if evidence_items is not None else self.evidence_items
        items = [
            item.to_text()
            for item in items
            if item.source == "project_rerun"
        ]
        return "\n\n".join(items)

    def start_from_log_file(self, log_path: str) -> str:
        path = Path(log_path).expanduser()

        if not path.exists():
            path = Path.cwd() / log_path

        if not path.exists() or not path.is_file():
            return f"日志文件不存在：{log_path}"

        content = path.read_text(encoding="utf-8", errors="ignore")

        self.evidence_items.append(
            EvidenceItem(
                source="log_file",
                title=f"Initial log file: {log_path}",
                content=content,
            )
        )

        self._refresh_route()

        return self.initial_diagnosis_summary()

    def start_from_paste(self, pasted_text: str, title: str = "Pasted runtime error") -> str:
        if not pasted_text.strip():
            return "粘贴内容为空。"

        self.evidence_items.append(
            EvidenceItem(
                source="user_paste",
                title=title,
                content=pasted_text,
            )
        )

        self._refresh_route()

        return self.initial_diagnosis_summary()

    def add_evidence(
        self,
        content: str,
        source: str = "user_paste",
        title: str = "Additional evidence",
        command: str = "",
        issue_type: str = "",
    ) -> str:
        if not content.strip():
            return "证据内容为空。"

        self.evidence_items.append(
            EvidenceItem(
                source=source,
                title=title,
                content=content,
                command=command,
                issue_type=issue_type,
            )
        )

        self._refresh_route()

        return "证据已添加，并已更新当前诊断上下文。"

    def add_monitor_event(
            self,
            event_text: str,
            event_type: str,
            title: str = "Monitor detected error event",
    ) -> str:
        """
        Add monitor-detected event to the session and refresh diagnosis.
        """
        return self.add_evidence(
            content=event_text,
            source="monitor_error_event",
            title=title,
            issue_type=event_type,
        )

    def run_readonly_command(self, command: str) -> str:
        result = self.executor.run(command)
        evidence_text = result.to_evidence_text()

        self.evidence_items.append(
            EvidenceItem(
                source="readonly_command",
                title=f"Command result: {command}",
                content=evidence_text,
                command=command,
            )
        )

        self._refresh_route()

        return evidence_text


    def _filter_commands_by_existing_evidence(self, commands: List[str]) -> List[str]:
        """
        Avoid repeatedly suggesting checks when related evidence already exists.
        """
        filtered = []

        for command in commands:
            lower = command.lower()

            if "hy-smi" in lower and self._has_evidence_keywords(["hy-smi", "vram used memory", "vram free memory"]):
                continue

            if "nvidia-smi" in lower and self._has_evidence_keywords(["nvidia-smi", "gpu memory"]):
                continue

            if "df -h /tmp" in lower and self._has_evidence_keywords(["df -h /tmp", "filesystem", "avail", "use%"]):
                continue

            if "python -m pip --version" in lower and self._has_evidence_keywords(["python -m pip --version", "pip"]):
                continue

            if "python -m pip show pyyaml" in lower and self._has_evidence_keywords(
                    ["pyyaml", "module_not_found", "module not found"]):
                continue

            filtered.append(command)

        return filtered

    def _filter_missing_info_by_existing_evidence(self, missing_info: List[str]) -> List[str]:
        filtered = []

        for item in missing_info:
            lower = item.lower()

            if "显存" in item and self._has_evidence_keywords(["hy-smi", "vram used memory", "vram free memory"]):
                continue

            if "/tmp" in item and self._has_evidence_keywords(["df -h /tmp", "filesystem", "avail", "use%"]):
                continue

            filtered.append(item)

        return filtered

    def suggest_next_actions(self) -> NextActionPlan:
        primary = str(
            self.route.get("primary_issue_type")
            or self.route.get("issue_type")
            or "unknown"
        )
        secondary = self.route.get("secondary_issue_types") or []

        commands: List[str] = []
        explanation: List[str] = []
        missing_info: List[str] = []

        if primary == "gpu" or "gpu" in secondary:
            commands.extend([
                "hy-smi",
                "nvidia-smi",
                "echo $PYTORCH_HIP_ALLOC_CONF",
                "echo $PYTORCH_CUDA_ALLOC_CONF",
                "ps -eo user,pid,%cpu,%mem,cmd | grep python",
            ])
            explanation.extend([
                "hy-smi / nvidia-smi 用于查看故障节点 GPU/DCU 显存占用。",
                "PYTORCH_*_ALLOC_CONF 用于判断是否配置了显存分配策略。",
                "ps 命令用于检查是否存在残留训练进程。",
            ])
            missing_info.extend([
                "故障节点实时显存占用情况。",
                "训练配置中的 batch size、precision、gradient checkpointing。",
                "是否存在残留进程占用显存。",
            ])

        if primary == "disk" or "disk" in secondary:
            commands.extend([
                "df -h /tmp",
                "df -ih /tmp",
                "du -sh /tmp/$USER",
                "du -sh ~/.cache",
            ])
            explanation.extend([
                "df -h 用于检查文件系统空间。",
                "df -ih 用于检查 inode 是否耗尽。",
                "du -sh 用于定位缓存目录占用。",
            ])
            missing_info.append("故障节点 /tmp 或缓存目录的实际空间占用。")

        if primary == "python_env" or "python_env" in secondary:
            commands.extend([
                "which python",
                "which pip",
                "python -c \"import sys; print(sys.executable)\"",
                "python -m pip --version",
                "python -m pip show PyYAML",
            ])
            explanation.extend([
                "which/python -m pip 用于判断当前解释器和 pip 是否属于同一环境。",
                "pip show 用于确认缺失包是否安装在当前环境。",
            ])
            missing_info.append("当前运行脚本使用的 Python 解释器路径和依赖安装状态。")

        if primary == "network_port" or "network_port" in secondary:
            commands.extend([
                "ss -lntp | grep 9100",
                "lsof -i :9100",
            ])
            explanation.extend([
                "ss/lsof 用于检查端口 9100 是否被其他进程占用。",
            ])
            missing_info.append("端口占用进程归属。")

        if primary == "slurm" or "slurm" in secondary:
            job_id = self._guess_job_id()
            if job_id:
                commands.extend([
                    f"squeue -j {job_id}",
                    f"scontrol show job {job_id}",
                ])
            else:
                commands.append("squeue")
            commands.extend([
                "sinfo -N -l",
            ])
            explanation.extend([
                "squeue 用于查看作业状态。",
                "scontrol show job 用于查看 Pending reason、资源申请和作业配置。",
                "sinfo 用于查看节点和分区状态。",
            ])
            missing_info.append("Slurm 作业详细状态和节点状态。")

        commands = self._dedupe_commands(commands)
        commands = self._filter_commands_by_existing_evidence(commands)

        missing_info = self._filter_missing_info_by_existing_evidence(missing_info)

        return NextActionPlan(
            issue_type=primary,
            commands=commands,
            explanation=self._dedupe_text(explanation),
            missing_info=self._dedupe_text(missing_info),
        )

    def initial_diagnosis_summary(self) -> str:
        if not self.route:
            return "当前还没有可用于诊断的日志或报错信息。"

        lines = [
            "## 初步诊断结果",
            "",
            self.route_context,
            "",
            "### 简要结论",
            f"- 主问题类型：`{self.route.get('primary_issue_type')}`",
            f"- 次要问题类型：`{self.route.get('secondary_issue_types')}`",
            f"- 置信度：`{self.route.get('confidence')}`",
            "",
            "你可以输入 `/next` 获取下一步检查命令，或输入 `/report` 生成当前证据下的最终报告。",
        ]

        return "\n".join(lines)

    def evidence_summary(self) -> str:
        if not self.evidence_items:
            return "当前没有证据。"

        lines = ["## 当前证据列表"]
        for idx, item in enumerate(self.evidence_items, start=1):
            lines.append(
                f"{idx}. [{item.source}] {item.title} "
                f"(command={item.command if item.command else '<none>'}, time={item.created_at})"
            )

        lines.append("")
        lines.append(f"组合证据文件：`{self.combined_log_path}`")
        return "\n".join(lines)

    def record_auto_recovery_result(
            self,
            result_text: str,
            action: str,
            fix_id: str,
            apply_success: bool,
            rerun_success: bool,
            rollback_executed: bool,
    ) -> None:
        self.latest_auto_recovery_result_text = result_text
        self.latest_auto_recovery_action = action
        self.latest_auto_recovery_fix_id = fix_id
        self.latest_auto_recovery_apply_success = apply_success
        self.latest_auto_recovery_rerun_success = rerun_success
        self.latest_auto_recovery_rollback_executed = rollback_executed

        self.add_evidence(
            content=result_text,
            source="auto_recovery",
            title="Stage 6C auto recovery result",
            issue_type="auto_recovery",
        )

    def _build_auto_recovery_result(self) -> AgentResult | None:
        if not self.latest_auto_recovery_result_text:
            return None

        recovered = (
                self.latest_auto_recovery_apply_success
                and self.latest_auto_recovery_rerun_success
        )

        status = "resolved" if recovered else "ok"

        summary = (
            "Stage 6C 自动恢复已完成，并通过 rerun 验证。"
            if recovered
            else "Stage 6C 自动恢复已执行，但尚未完成成功 rerun 验证或已进入升级处理。"
        )

        return AgentResult(
            agent_name="AutoRecoveryAgent",
            issue_type="auto_recovery",
            status=status,
            is_primary=False,
            summary=summary,
            evidence=[
                f"action={self.latest_auto_recovery_action}",
                f"fix_id={self.latest_auto_recovery_fix_id}",
                f"apply_success={self.latest_auto_recovery_apply_success}",
                f"rerun_success={self.latest_auto_recovery_rerun_success}",
                f"rollback_executed={self.latest_auto_recovery_rollback_executed}",
            ],
            analysis=[
                self.latest_auto_recovery_result_text,
            ],
            recommended_checks=[],
            low_risk_actions=[],
            manual_confirm_actions=[],
            risk_notes=[],
            raw_output=self.latest_auto_recovery_result_text,
        )


    def generate_report(
            self,
            report_intent: str = "event_troubleshooting",
            evidence_items: List[EvidenceItem] | None = None,
    ) -> tuple[str, Path, str]:
        if evidence_items is None:
            self._refresh_route()
            report_route = dict(self.route)
            report_evidence_items = self.evidence_items
        else:
            report_evidence_items = list(evidence_items)
            self._write_combined_evidence()
            report_route = classify_issue_dict(
                "帮我分析当前监控事件 evidence",
                content_preview="\n".join(item.content for item in report_evidence_items),
            )

        orchestrator = MultiAgentOrchestratorV3(
            route=report_route,
            agent_depth=self.agent_depth,  # type: ignore[arg-type]
        )
        workflow_result = orchestrator.run()
        workflow_result["route"] = dict(workflow_result["route"])
        workflow_result["route"]["report_intent"] = report_intent
        interactive_result = self._build_interactive_evidence_result(
            evidence_items=report_evidence_items,
        )
        workflow_result["results"].append(interactive_result)
        session_outcome_result = self._build_session_outcome_result(
            route=report_route,
            evidence_items=report_evidence_items,
        )
        workflow_result["results"].append(session_outcome_result)
        include_scoped_auto_recovery = (
            evidence_items is None
            or any(item.source == "auto_recovery" for item in report_evidence_items)
        )
        include_scoped_notification = (
            evidence_items is None
            or any(item.source == "notification" for item in report_evidence_items)
        )

        auto_recovery_result = (
            self._build_auto_recovery_result()
            if include_scoped_auto_recovery
            else None
        )
        if auto_recovery_result:
            workflow_result["results"].append(auto_recovery_result)

        notification_result = (
            self._build_notification_result()
            if include_scoped_notification
            else None
        )
        if notification_result:
            workflow_result["results"].append(notification_result)

        if self.report_mode == "rule":
            report_agent = ReportAgent()
            report = report_agent.build_report(
                route=workflow_result["route"],
                results=workflow_result["results"],
            )
            save_path = self.output_dir / "final_rule_report.md"
            source = "Rule ReportAgent"

        elif self.report_mode == "llm":
            report_agent = LLMReportAgent()
            report = report_agent.build_report(
                route=workflow_result["route"],
                results=workflow_result["results"],
            )
            save_path = self.output_dir / "final_llm_report.md"
            source = "LLMReportAgent"

        else:
            try:
                report_agent = LLMReportAgent()
                report = report_agent.build_report(
                    route=workflow_result["route"],
                    results=workflow_result["results"],
                )
                save_path = self.output_dir / "final_llm_report.md"
                source = "LLMReportAgent"
            except Exception:
                report_agent = ReportAgent()
                report = report_agent.build_report(
                    route=workflow_result["route"],
                    results=workflow_result["results"],
                )
                save_path = self.output_dir / "final_rule_report.md"
                source = "Rule ReportAgent fallback"

        save_path.write_text(report, encoding="utf-8")
        return report, save_path, source

    def _refresh_route(self) -> None:
        self._write_combined_evidence()
        question = f"帮我分析 {self.combined_log_path}"
        self.route = classify_issue_dict(question)
        self.route_context = format_route_context(self.route)

    def _write_combined_evidence(self) -> None:
        lines = [
            "# Combined Evidence for Interactive Troubleshooting Session",
            f"session_id: {self.session_id}",
            f"created_at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
        ]

        for item in self.evidence_items:
            lines.append(item.to_text())

        self.combined_log_path.write_text("\n".join(lines), encoding="utf-8")

    def _guess_job_id(self) -> str:
        text = "\n".join(item.content for item in self.evidence_items)

        patterns = [
            r"Submitted batch job\s+(\d+)",
            r"JobId=(\d+)",
            r"StepId=(\d+)",
            r"\bJOBID\b.*?\n\s*(\d+)",
        ]

        for pattern in patterns:
            match = re_search(pattern, text)
            if match:
                return match.group(1)

        return ""

    def _infer_initial_failure_summary(
            self,
            route: Dict[str, Any] | None = None,
            evidence_items: List[EvidenceItem] | None = None,
    ) -> str:
        """
        Infer initial failure type from route and evidence.
        """
        route_data = route if route is not None else self.route
        items = list(evidence_items) if evidence_items is not None else self.evidence_items

        primary = str(
            route_data.get("primary_issue_type")
            or route_data.get("issue_type")
            or "unknown"
        )

        text = "\n".join(item.content for item in items).lower()

        if "hip out of memory" in text:
            return "初始运行失败主要表现为 HIP/DCU 显存不足。"

        if "cuda out of memory" in text:
            return "初始运行失败主要表现为 CUDA 显存不足。"

        if "modulenotfounderror" in text or "no module named" in text:
            return "初始运行失败主要表现为 Python 依赖缺失。"

        if "no space left on device" in text:
            return "初始运行失败主要表现为磁盘空间不足。"

        if "address already in use" in text:
            return "初始运行失败主要表现为端口占用。"

        return f"初始运行失败主类型为 {primary}。"

    def _build_session_outcome_result(
            self,
            route: Dict[str, Any] | None = None,
            evidence_items: List[EvidenceItem] | None = None,
    ) -> AgentResult:
        """
        Build a resolution-oriented AgentResult for Stage 4C.

        This result tells LLMReportAgent whether the final rerun succeeded.
        If it succeeded, the final report should become a fix-verification report,
        not just a diagnosis report.
        """
        initial_failure = self._infer_initial_failure_summary(
            route=route,
            evidence_items=evidence_items,
        )

        if self.latest_rerun_success:
            summary = (
                "Stage 4C 修复验证闭环已完成：最后一次 /rerun 返回码为 0，"
                "当前复现命令已不再报错，问题可以认为已解决。"
            )
            status = "resolved"
            evidence = [
                "resolution_status=resolved",
                f"initial_failure={initial_failure}",
                f"fix_plan_generated={self.has_fix_plan_generated}",
                f"project_context_collected={self.project_context is not None}",
                f"latest_context_path={self.latest_context_path}",
                f"remote_profile_set={self.remote_profile is not None}",
                f"latest_remote_context_path={self.latest_remote_context_path}",
                f"latest_apply_success={self.latest_apply_success}",
                f"latest_apply_fix_id={self.latest_apply_fix_id}",
                f"latest_apply_summary={self.latest_apply_summary}",
                f"latest_diff_path={self.latest_diff_path}",
                f"latest_rerun_success=True",
                f"latest_rerun_return_code={self.latest_rerun_return_code}",
                f"latest_rerun_command={self.latest_rerun_command}",
                f"latest_rerun_log_path={self.latest_rerun_log_path}",
                f"latest_remote_rerun_success={self.latest_remote_rerun_success}",
                f"latest_remote_rerun_return_code={self.latest_remote_rerun_return_code}",
                f"latest_remote_rerun_command={self.latest_remote_rerun_command}",
                f"latest_remote_project_dir={self.latest_remote_project_dir}",
                f"latest_remote_apply_success={self.latest_remote_apply_success}",
                f"latest_remote_apply_fix_id={self.latest_remote_apply_fix_id}",
                f"latest_remote_apply_summary={self.latest_remote_apply_summary}",
                f"latest_remote_diff_path={self.latest_remote_diff_path}",
                f"latest_remote_apply_project_dir={self.latest_remote_apply_project_dir}",
            ]
            analysis = [
                initial_failure,
                "系统在初始失败后生成了修复计划。",
                "用户根据修复计划完成手动修复。",
                "最后一次重新运行项目命令返回码为 0，说明当前复现路径已通过验证。",
                "最终报告应优先体现“初始失败—修复计划—手动修复—rerun 成功”的闭环过程。",
            ]
            low_risk_actions = [
                "保留已验证成功的配置修改。",
                "如进入正式训练，建议先进行短轮次验证。",
                "保存本次修复前后的配置差异，方便后续复现。",
            ]
        else:
            summary = "当前尚未完成成功 rerun 验证，问题仍处于待修复或待验证状态。"
            status = "unresolved"
            evidence = [
                "resolution_status=unresolved",
                f"initial_failure={initial_failure}",
                f"fix_plan_generated={self.has_fix_plan_generated}",
                f"project_context_collected={self.project_context is not None}",
                f"latest_context_path={self.latest_context_path}",
                f"remote_profile_set={self.remote_profile is not None}",
                f"latest_remote_context_path={self.latest_remote_context_path}",
                f"latest_rerun_success=False",
                f"latest_rerun_return_code={self.latest_rerun_return_code}",
                f"latest_rerun_command={self.latest_rerun_command}",
                f"latest_rerun_log_path={self.latest_rerun_log_path}",
                f"latest_apply_success={self.latest_apply_success}",
                f"latest_apply_fix_id={self.latest_apply_fix_id}",
                f"latest_apply_summary={self.latest_apply_summary}",
                f"latest_diff_path={self.latest_diff_path}",
                f"latest_remote_rerun_success={self.latest_remote_rerun_success}",
                f"latest_remote_rerun_return_code={self.latest_remote_rerun_return_code}",
                f"latest_remote_rerun_command={self.latest_remote_rerun_command}",
                f"latest_remote_project_dir={self.latest_remote_project_dir}",
                f"latest_remote_apply_success={self.latest_remote_apply_success}",
                f"latest_remote_apply_fix_id={self.latest_remote_apply_fix_id}",
                f"latest_remote_apply_summary={self.latest_remote_apply_summary}",
                f"latest_remote_diff_path={self.latest_remote_diff_path}",
                f"latest_remote_apply_project_dir={self.latest_remote_apply_project_dir}",
            ]
            analysis = [
                initial_failure,
                "当前没有成功的项目重新运行结果。",
                "应继续根据 /fix 计划修复，并使用 /rerun 验证。",
            ]
            low_risk_actions = []

        return AgentResult(
            agent_name="SessionOutcomeAgent",
            issue_type="resolution",
            status=status,
            is_primary=False,
            summary=summary,
            evidence=evidence,
            analysis=analysis,
            recommended_checks=[],
            low_risk_actions=low_risk_actions,
            manual_confirm_actions=[],
            risk_notes=[
                "rerun 成功只能证明当前复现命令在当前配置下不再报错。",
                "如果正式训练参数、数据规模、运行节点或 Slurm 资源配置变化，仍需再次验证。",
            ],
            raw_output=self._build_rerun_history_text(evidence_items=evidence_items),
        )

    def record_notification_result(
            self,
            result_text: str,
            status: str = "",
            channels: list[str] | None = None,
    ) -> None:
        self.latest_notification_result_text = result_text
        self.latest_notification_status = status
        self.latest_notification_channels = channels or []

        self.add_evidence(
            content=result_text,
            source="notification",
            title="Stage 6D notification result",
            issue_type="notification",
        )

    def _build_notification_result(self) -> AgentResult | None:
        if not self.latest_notification_result_text:
            return None

        return AgentResult(
            agent_name="NotificationAgent",
            issue_type="notification",
            status="ok",
            is_primary=False,
            summary="Stage 6D 已完成通知负责人或写入通知审计记录。",
            evidence=[
                f"notification_status={self.latest_notification_status}",
                f"notification_channels={self.latest_notification_channels}",
            ],
            analysis=[
                self.latest_notification_result_text,
            ],
            recommended_checks=[],
            low_risk_actions=[],
            manual_confirm_actions=[],
            risk_notes=[],
            raw_output=self.latest_notification_result_text,
        )


    @staticmethod
    def _dedupe_commands(commands: List[str]) -> List[str]:
        result = []
        for command in commands:
            if command not in result:
                result.append(command)
        return result

    @staticmethod
    def _dedupe_text(items: List[str]) -> List[str]:
        result = []
        for item in items:
            if item not in result:
                result.append(item)
        return result


def re_search(pattern: str, text: str):
    import re
    return re.search(pattern, text, flags=re.DOTALL)

