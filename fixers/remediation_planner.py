from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class FixAction:
    fix_id: str
    issue_type: str
    title: str
    risk_level: str
    action_type: str
    description: str
    suggested_steps: List[str] = field(default_factory=list)
    verify_commands: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    apply_supported: bool = False

    def to_markdown(self) -> str:
        lines = [
            f"### {self.fix_id}: {self.title}",
            f"- issue_type: `{self.issue_type}`",
            f"- risk_level: `{self.risk_level}`",
            f"- action_type: `{self.action_type}`",
            f"- apply_supported: `{self.apply_supported}`",  # 新增字段
            "",
            self.description,
            "",
            "**建议操作：",
        ]

        # 如果支持 apply，就在 Markdown 中提示
        if self.apply_supported:
            lines.append("")
            lines.append("**可控执行：")
            lines.append(f"- 可使用 `/apply {self.fix_id}` 让 Agent 备份配置、修改配置并生成 diff。")

        for step in self.suggested_steps:
            lines.append(f"- {step}")

        lines.append("")
        lines.append("**验证命令：")
        if self.verify_commands:
            lines.append("```bash")
            for command in self.verify_commands:
                lines.append(command)
            lines.append("```")
        else:
            lines.append("- 暂无。")

        lines.append("")
        lines.append("**注意事项：")
        for note in self.notes:
            lines.append(f"- {note}")

        return "\n".join(lines)


@dataclass
class FixPlan:
    primary_issue_type: str
    secondary_issue_types: List[str]
    fixes: List[FixAction]

    def to_markdown(self) -> str:
        lines = [
            "# 修复计划",
            "",
            f"- 主问题类型：`{self.primary_issue_type}`",
            f"- 次要问题类型：`{self.secondary_issue_types}`",
            "",
            "## 修复顺序建议",
        ]

        if not self.fixes:
            lines.append("- 当前证据不足，暂时无法生成明确修复计划。")
            return "\n".join(lines)

        for fix in self.fixes:
            lines.append(f"- `{fix.fix_id}`：{fix.title}（{fix.risk_level}）")

        lines.append("")
        lines.append("## 详细修复方案")

        for fix in self.fixes:
            lines.append("")
            lines.append(fix.to_markdown())

        lines.append("")
        lines.append("## 使用方式")
        lines.append("- 如果某个修复项 `apply_supported=True`，可以使用 `/apply <fix_id>` 让 Agent 执行可控配置修改。")
        lines.append("- `/apply` 会先备份配置文件，再写入修改，并生成 diff。")
        lines.append("- 可以使用 `/diff` 查看最近一次修改差异。")
        lines.append("- 可以使用 `/rollback` 回滚最近一次 `/apply`。")
        lines.append("- 修改完成后，在交互模式中输入 `/rerun` 重新运行项目。")
        lines.append("- 如果重新运行仍失败，Agent 会自动把新错误加入证据并继续排查。")

        return "\n".join(lines)


class RemediationPlanner:
    """
    Generate fix plans from diagnosis route and collected evidence.

    This planner does not execute modifications.
    It only gives safe, step-by-step remediation instructions.
    """

    def build_fix_plan(
            self,
            route: Dict[str, object],
            evidence_text: str,
            allowed_issue_types: List[str] | None = None,
            project_context: Any | None = None,
    ) -> FixPlan:
        primary = str(route.get("primary_issue_type") or route.get("issue_type") or "unknown")
        secondary = route.get("secondary_issue_types") or []

        fixes: List[FixAction] = []

        ordered_types = [primary] + [x for x in secondary if x != primary]

        if allowed_issue_types is not None:
            allowed_set = set(allowed_issue_types)
            ordered_types = [issue for issue in ordered_types if issue in allowed_set]

        for issue_type in ordered_types:
            if issue_type == "gpu":
                fixes.extend(self._gpu_fixes(evidence_text, project_context))
            elif issue_type == "python_env":
                fixes.extend(self._python_env_fixes(evidence_text, project_context))
            elif issue_type == "disk":
                fixes.extend(self._disk_fixes(evidence_text, project_context))
            elif issue_type == "network_port":
                fixes.extend(self._network_fixes(evidence_text, project_context))
            elif issue_type == "slurm":
                fixes.extend(self._slurm_fixes(evidence_text, project_context))

        # 去重
        seen = set()
        deduped = []
        for fix in fixes:
            if fix.fix_id not in seen:
                deduped.append(fix)
                seen.add(fix.fix_id)

        return FixPlan(
            primary_issue_type=primary,
            secondary_issue_types=list(secondary),
            fixes=deduped,
        )

    def _gpu_fixes(self, evidence_text: str, project_context: Any | None = None) -> List[FixAction]:
        batch_cfg, batch_value = self._find_json_config_value(project_context, "batch_size")
        precision_cfg, precision_value = self._find_json_config_value(project_context, "precision")
        ckpt_cfg, ckpt_value = self._find_json_config_value(project_context, "gradient_checkpointing")

        return [
            FixAction(
                fix_id="fix-gpu-1",
                issue_type="gpu",
                title="降低训练 batch size",
                risk_level="confirm_required",
                action_type="manual_config_edit",
                description=(
                    "日志显示存在 CUDA/HIP/DCU OOM 或显存不足。最直接的低风险修复方式是降低 batch size。"
                ),
                suggested_steps=[
                    (
                        f"项目上下文发现 {batch_cfg} 中 batch_size={batch_value}，"
                        "建议优先降低到 4 或 8。"
                        if batch_cfg
                        else "找到训练配置文件中的 batch_size、train_batch_size 或 samples_per_gpu 字段。"
                    ),
                    "如果使用命令行参数启动训练，则在运行命令中降低 --batch-size。",
                    "修改后使用 `/rerun` 验证是否还出现 OOM。",
                ],
                verify_commands=[
                    "hy-smi",
                    "nvidia-smi",
                ],
                notes=[
                    "降低 batch size 可能影响训练速度和梯度稳定性，可配合梯度累积。",
                    "如果修改后仍 OOM，再考虑混合精度或梯度检查点。",
                ],
                apply_supported=True
            ),
            FixAction(
                fix_id="fix-gpu-2",
                issue_type="gpu",
                title="启用混合精度或梯度检查点",
                risk_level="confirm_required",
                action_type="manual_config_edit",
                description=(
                    "如果模型显存峰值较高，可以启用 bf16/fp16 混合精度或 gradient checkpointing 降低显存占用。"
                ),
                suggested_steps=[
                    (
                        f"项目上下文发现 {precision_cfg} 中 precision={precision_value}，"
                        "可尝试改为 bf16 或 fp16。"
                        if precision_cfg
                        else "检查配置文件中是否有 precision、amp、fp16、bf16、mixed_precision 等字段。"
                    ),
                    (
                        f"项目上下文发现 {ckpt_cfg} 中 gradient_checkpointing={ckpt_value}，"
                        "可尝试改为 true。"
                        if ckpt_cfg
                        else "如果模型支持 gradient_checkpointing，可将其开启。"
                    ),
                    "修改后使用 `/rerun` 做短轮次验证。",
                ],
                verify_commands=[
                    "hy-smi",
                    "echo $PYTORCH_HIP_ALLOC_CONF",
                    "echo $PYTORCH_CUDA_ALLOC_CONF",
                ],
                notes=[
                    "混合精度可能影响数值稳定性，建议先短轮次验证。",
                    "gradient checkpointing 会降低显存占用，但可能增加训练时间。",
                ],
                apply_supported=True
            ),
            FixAction(
                fix_id="fix-gpu-3",
                issue_type="gpu",
                title="为下一次运行设置 PyTorch 显存分配策略",
                risk_level="confirm_required",
                action_type="rerun_env",
                description=(
                    "如果日志提示 reserved but unallocated memory 较大，可在重新运行时设置显存分配环境变量缓解碎片问题。"
                ),
                suggested_steps=[
                    "HIP/DCU 环境可在下一次运行中设置 PYTORCH_HIP_ALLOC_CONF=expandable_segments:True。",
                    "CUDA 环境可在下一次运行中设置 PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True。",
                    "这一步不能替代降低 batch size，只能缓解显存碎片。",
                ],
                verify_commands=[
                    "echo $PYTORCH_HIP_ALLOC_CONF",
                    "echo $PYTORCH_CUDA_ALLOC_CONF",
                ],
                notes=[
                    "环境变量应作用于重新运行项目的命令环境。",
                    "如果 batch size 本身过大，仍然需要降低 batch size。",
                ],
                apply_supported=True
            ),
        ]

    def _python_env_fixes(self, evidence_text: str, project_context: Any | None = None) -> List[FixAction]:
        simulate_py_cfg, simulate_py_value = self._find_json_config_value(project_context,
                                                                          "simulate_python_env_mismatch")
        package = "PyYAML" if "yaml" in evidence_text.lower() or "pyyaml" in evidence_text.lower() else "<missing-package>"

        return [
            FixAction(
                fix_id="fix-python-1",
                issue_type="python_env",
                title="修复 Python 解释器与 pip 环境不一致",
                risk_level="confirm_required",
                action_type="manual_env_fix",
                description=(
                    "如果日志显示 ModuleNotFoundError 或 python/pip 路径不一致，应先确认运行脚本使用的解释器。"
                ),
                suggested_steps=[
                    "确认项目运行时使用的 python 路径。",
                    "使用当前解释器执行 python -m pip，而不是直接使用 pip。",
                    f"确认缺失包是否安装在当前解释器环境中，例如 python -m pip show {package}。",
                    f"该 Demo 项目发现 {simulate_py_cfg} 中 simulate_python_env_mismatch={simulate_py_value}，"
                    "可通过 /apply fix-python-1 关闭该模拟环境告警。"
                    if simulate_py_cfg
                    else "如确认缺失，可由用户手动执行当前解释器对应的安装命令安装缺失包。",
                ],
                verify_commands=[
                    "which python",
                    "which pip",
                    "python -c \"import sys; print(sys.executable)\"",
                    "python -m pip --version",
                    f"python -m pip show {package}",
                ],
                notes=[
                    "不要盲目使用 sudo pip install。",
                    "pip install 会修改环境，因此不由 Agent 自动执行。",
                ],
                apply_supported=True
            )
        ]

    def _disk_fixes(self, evidence_text: str, project_context: Any | None = None) -> List[FixAction]:
        cache_cfg, cache_value = self._find_json_config_value(project_context, "cache_dir")
        simulate_disk_cfg, simulate_disk_value = self._find_json_config_value(project_context, "simulate_disk_full")
        return [
            FixAction(
                fix_id="fix-disk-1",
                issue_type="disk",
                title="切换缓存目录或减少缓存写入",
                risk_level="confirm_required",
                action_type="manual_config_edit",
                description=(
                    "如果日志显示 /tmp 或缓存目录 No space left on device，优先考虑将缓存目录切换到空间更大的路径。"
                ),
                suggested_steps=[
                    (
                        f"项目上下文发现 {cache_cfg} 中 cache_dir={cache_value}，"
                        "建议检查该路径空间，或改到空间更大的磁盘。"
                        if cache_cfg
                        else "检查项目配置中 cache_dir、tmp_dir、dataset_cache、LMDB path 等字段。"
                    ),
                    (
                        f"该 Demo 项目还发现 {simulate_disk_cfg} 中 simulate_disk_full={simulate_disk_value}，"
                        "可通过 /apply fix-disk-1 关闭该模拟故障。"
                        if simulate_disk_cfg
                        else "如果缓存不是必须，可临时关闭缓存构建。"
                    ),
                    "修改后使用 `/rerun` 验证缓存问题是否消失。",
                ],
                verify_commands=[
                    "df -h /tmp",
                    "df -ih /tmp",
                    "du -sh /tmp/$USER",
                ],
                notes=[
                    "不建议 Agent 自动删除缓存目录。",
                    "如需清理缓存，应由用户确认目录归属和任务状态后手动处理。",
                ],
                apply_supported=True
            )
        ]

    def _network_fixes(self, evidence_text: str, project_context: Any | None = None) -> List[FixAction]:
        port_cfg, port_value = self._find_json_config_value(project_context, "metrics_port")
        return [
            FixAction(
                fix_id="fix-network-1",
                issue_type="network_port",
                title="更换冲突端口",
                risk_level="confirm_required",
                action_type="manual_command_or_config_edit",
                description=(
                    "如果 TensorBoard、Exporter 或服务启动时报 Address already in use，优先更换端口。"
                ),
                suggested_steps=[
                    "确认当前端口占用进程。",
                    (
                        f"项目上下文发现 {port_cfg} 中 metrics_port={port_value}，"
                        "建议改为 9101 或其他未占用端口。"
                        if port_cfg
                        else "将 TensorBoard、Exporter 或服务端口从 9100 改为 9101、9102 等未占用端口。"
                    ),
                    "修改后使用 `/rerun` 验证服务是否能正常启动。",
                ],
                verify_commands=[
                    "ss -lntp | grep 9100",
                    "lsof -i :9100",
                ],
                notes=[
                    "不建议自动 kill 占用端口的进程。",
                    "只有确认进程归属后，用户才应手动终止对应进程。",
                ],
                apply_supported=True
            )
        ]

    def _slurm_fixes(self, evidence_text: str, project_context: Any | None = None) -> List[FixAction]:
        return [
            FixAction(
                fix_id="fix-slurm-1",
                issue_type="slurm",
                title="检查并调整 Slurm 资源申请",
                risk_level="manual_only",
                action_type="manual_slurm_script_edit",
                description=(
                    "如果 Slurm 中出现 Pending Resources 或资源约束错误，需要检查作业脚本中的分区、GRES、内存和时间限制。"
                ),
                suggested_steps=[
                    "检查 sbatch 脚本中的 --partition、--gres、--mem、--time 配置。",
                    "如果是 Resources，可能只是资源暂时不足，也可能是申请条件过高。",
                    "如果出现 slurmstepd oom-kill，需要结合 GPU/内存 OOM 修复。",
                ],
                verify_commands=[
                    "squeue",
                    "scontrol show job <JOB_ID>",
                    "sinfo -N -l",
                ],
                notes=[
                    "不要自动 scancel 作业。",
                    "节点 DOWN/DRAIN 通常需要管理员处理。",
                ],
                apply_supported=False
            )
        ]

    def _find_json_config_value(self, project_context: Any | None, key: str) -> tuple[str, Any]:
        """
        Return (config_path, value) if the key is found in scanned JSON config files.
        """
        if not project_context:
            return "", None

        config_files = getattr(project_context, "config_files", []) or []

        for cfg in config_files:
            parsed = getattr(cfg, "parsed_json", {}) or {}
            path = getattr(cfg, "path", "")

            if key in parsed:
                return path, parsed.get(key)

        return "", None

    def _context_fix_note(self, project_context: Any | None, keyword: str) -> list[str]:
        """
        Return context fix hints that contain the keyword.
        """
        if not project_context:
            return []

        hints = getattr(project_context, "fix_hints", []) or []
        return [hint for hint in hints if keyword.lower() in hint.lower()]