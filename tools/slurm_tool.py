import re
import shutil
import subprocess
import shlex
from typing import List
from pathlib import Path

from smolagents import tool


PENDING_REASON_HINTS = {
    "Resources": "资源不足，当前没有满足作业请求的空闲节点、GPU/DCU 或 CPU。",
    "Priority": "作业优先级不足，正在等待更高优先级作业完成。",
    "ReqNodeNotAvail": "请求的节点不可用，可能处于 DOWN、DRAIN、RESERVED 或维护状态。",
    "Dependency": "作业依赖的其他作业尚未完成。",
    "QOSMax": "超过 QoS 限制，例如最大作业数、最大 GPU 数或最大运行时间。",
    "AssocMax": "超过账号或用户关联资源限制。",
    "PartitionNodeLimit": "请求的节点数量超出分区限制。",
    "TimeLimit": "作业请求时间超过分区限制。",
    "InvalidAccount": "账号或项目组配置不正确。",
    "BadConstraints": "节点约束条件无法满足。",
}


def _run_command(command: List[str], timeout: int = 15) -> tuple[int, str, str]:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except FileNotFoundError:
        return 127, "", "COMMAND_NOT_FOUND"
    except subprocess.TimeoutExpired:
        return 124, "", "COMMAND_TIMEOUT"
    except Exception as exc:
        return 1, "", f"COMMAND_ERROR: {type(exc).__name__}: {exc}"


def _format_command(command: List[str], return_code: int, stdout: str, stderr: str, max_chars: int = 10000) -> str:
    if len(stdout) > max_chars:
        stdout = stdout[:max_chars] + "\n[STDOUT_TRUNCATED]"
    if len(stderr) > max_chars:
        stderr = stderr[:max_chars] + "\n[STDERR_TRUNCATED]"

    return (
        f"$ {' '.join(shlex.quote(x) for x in command)}\n"
        f"return_code: {return_code}\n"
        f"stdout:\n{stdout if stdout else '<empty>'}\n"
        f"stderr:\n{stderr if stderr else '<empty>'}"
    )


def _slurm_available() -> bool:
    return shutil.which("squeue") is not None or shutil.which("sinfo") is not None


def _extract_reasons(text: str) -> List[str]:
    reasons = set()

    invalid_reasons = {
        "REASON",
        "null",
        "NULL",
        "None",
        "none",
        "Dependency",
    }

    def add_reason(value: str) -> None:
        value = value.strip()
        if not value:
            return
        if value in invalid_reasons:
            return
        if value.isdigit():
            return
        reasons.add(value)

    # 1. scontrol show job 中的 Reason=Resources
    for match in re.findall(r"\bReason=([A-Za-z][A-Za-z0-9_]+)", text):
        add_reason(match)

    # 2. squeue 中的 NODELIST(REASON) 最后一列，例如 (Resources)
    for line in text.splitlines():
        stripped = line.strip()

        # 跳过表头
        if "NODELIST(REASON)" in stripped:
            continue

        # 跳过 UserId=lf(1000)、Dependency=(null) 这类字段
        if "UserId=" in stripped or "Dependency=" in stripped:
            continue

        match = re.search(r"\(([A-Za-z][A-Za-z0-9_]+)\)\s*$", stripped)
        if match:
            add_reason(match.group(1))

    # 3. 兜底：只识别已知 Slurm reason
    for known in PENDING_REASON_HINTS:
        if re.search(rf"\b{re.escape(known)}\b", text, flags=re.IGNORECASE):
            add_reason(known)

    return sorted(reasons)


def _build_reason_explanation(reasons: List[str]) -> str:
    if not reasons:
        return "未识别到明确 Pending reason，需要查看 scontrol show job 或 squeue 的 Reason 字段。"

    lines = []
    for reason in reasons:
        explanation = PENDING_REASON_HINTS.get(reason, "该原因需要结合集群策略和管理员配置进一步确认。")
        lines.append(f"- {reason}: {explanation}")
    return "\n".join(lines)


@tool
def diagnose_slurm_text(slurm_text: str) -> str:
    """
    Diagnose pasted Slurm output or error text, such as squeue, sinfo,
    scontrol show job, or sbatch error messages.

    Args:
        slurm_text: Raw Slurm command output or user pasted Slurm error text.

    Returns:
        A structured Slurm diagnosis with recognized state, reasons and next checks.
    """
    text = slurm_text.strip()
    if not text:
        return "[SLURM_TEXT_DIAGNOSIS]\nstatus: empty_text\nmessage: 未提供 Slurm 输出文本。"

    lower = text.lower()
    detected = []

    if " pending" in lower or " pd " in lower or "state=pending" in lower:
        detected.append("job_pending")
    if "down" in lower:
        detected.append("node_down")
    if "drain" in lower:
        detected.append("node_drain")
    if "not_responding" in lower or "not responding" in lower:
        detected.append("node_not_responding")
    if "invalid account" in lower:
        detected.append("invalid_account")
    if "batch job submission failed" in lower:
        detected.append("sbatch_failed")
    if "gres" in lower or "gpu" in lower or "dcu" in lower:
        detected.append("accelerator_resource_related")

    reasons = _extract_reasons(text)

    return (
        "[SLURM_TEXT_DIAGNOSIS]\n"
        f"status: ok\n"
        f"detected_signals: {detected if detected else []}\n"
        f"pending_reasons: {reasons if reasons else []}\n\n"
        "[REASON_EXPLANATION]\n"
        f"{_build_reason_explanation(reasons)}\n\n"
        "[SUGGESTED_NEXT_CHECKS]\n"
        "- squeue -u $USER 查看用户作业状态\n"
        "- scontrol show job <JOB_ID> 查看详细 Pending reason\n"
        "- sinfo -N -l 查看节点状态\n"
        "- scontrol show node <NODE_NAME> 查看节点 DOWN/DRAIN 原因\n"
        "- 检查作业脚本中的 partition、gres、time、account、constraint 设置"
    )


@tool
def check_slurm_queue(user_only: bool = True, max_lines: int = 30) -> str:
    """
    Check Slurm queue status using squeue.

    Args:
        user_only: Whether to show only current user's jobs.
        max_lines: Maximum output lines returned.

    Returns:
        A compact squeue report and pending reason explanation.
    """
    if shutil.which("squeue") is None:
        return (
            "[SLURM_QUEUE]\n"
            "status: command_not_found\n"
            "message: 当前环境未找到 squeue，可能不是 Slurm 登录节点或未加载 Slurm 环境。"
        )

    if user_only:
        cmd = ["squeue", "-u", "$USER", "-o", "%.18i %.9P %.30j %.8u %.2t %.10M %.6D %R"]
        # shell=False 时 $USER 不会展开，所以直接从环境中取更稳
        import os
        user = os.environ.get("USER")
        cmd = ["squeue", "-u", user, "-o", "%.18i %.9P %.30j %.8u %.2t %.10M %.6D %R"] if user else ["squeue"]
    else:
        cmd = ["squeue", "-o", "%.18i %.9P %.30j %.8u %.2t %.10M %.6D %R"]

    return_code, stdout, stderr = _run_command(cmd, timeout=15)

    lines = stdout.splitlines()
    if len(lines) > max_lines:
        stdout = "\n".join(lines[:max_lines]) + "\n[SQUEUE_OUTPUT_TRUNCATED]"

    reasons = _extract_reasons(stdout + "\n" + stderr)

    return (
        "[SLURM_QUEUE]\n"
        f"{_format_command(cmd, return_code, stdout, stderr)}\n\n"
        "[PENDING_REASON_EXPLANATION]\n"
        f"{_build_reason_explanation(reasons)}"
    )


@tool
def check_slurm_job(job_id: str) -> str:
    """
    Check detailed Slurm job information using scontrol show job.

    Args:
        job_id: Slurm job id.

    Returns:
        Detailed job state, reason and suggested interpretation.
    """
    if shutil.which("scontrol") is None:
        return (
            "[SLURM_JOB]\n"
            "status: command_not_found\n"
            "message: 当前环境未找到 scontrol，可能不是 Slurm 登录节点或未加载 Slurm 环境。"
        )

    if not re.fullmatch(r"[0-9]+(?:_[0-9]+)?", job_id.strip()):
        return (
            "[SLURM_JOB]\n"
            "status: invalid_job_id\n"
            f"job_id: {job_id}\n"
            "message: job_id 格式不合法，只允许数字或数组任务格式，例如 12345 或 12345_1。"
        )

    cmd = ["scontrol", "show", "job", job_id.strip()]
    return_code, stdout, stderr = _run_command(cmd, timeout=15)
    reasons = _extract_reasons(stdout + "\n" + stderr)

    state_match = re.search(r"JobState=([A-Z_]+)", stdout)
    reason_match = re.search(r"Reason=([A-Za-z0-9_]+)", stdout)

    return (
        "[SLURM_JOB]\n"
        f"{_format_command(cmd, return_code, stdout, stderr)}\n\n"
        "[PARSED_JOB_INFO]\n"
        f"job_state: {state_match.group(1) if state_match else '<unknown>'}\n"
        f"reason: {reason_match.group(1) if reason_match else '<unknown>'}\n\n"
        "[REASON_EXPLANATION]\n"
        f"{_build_reason_explanation(reasons)}"
    )


@tool
def check_slurm_nodes(max_lines: int = 60) -> str:
    """
    Check Slurm node and partition status using sinfo.

    Args:
        max_lines: Maximum output lines returned.

    Returns:
        A compact node status report.
    """
    if shutil.which("sinfo") is None:
        return (
            "[SLURM_NODES]\n"
            "status: command_not_found\n"
            "message: 当前环境未找到 sinfo，可能不是 Slurm 登录节点或未加载 Slurm 环境。"
        )

    cmd = ["sinfo", "-N", "-l"]
    return_code, stdout, stderr = _run_command(cmd, timeout=15)

    lines = stdout.splitlines()
    if len(lines) > max_lines:
        stdout = "\n".join(lines[:max_lines]) + "\n[SINFO_OUTPUT_TRUNCATED]"

    lower = stdout.lower()
    signals = []
    if "down" in lower:
        signals.append("存在 DOWN 节点")
    if "drain" in lower:
        signals.append("存在 DRAIN 节点")
    if "idle" in lower:
        signals.append("存在 IDLE 节点")
    if "alloc" in lower:
        signals.append("存在 ALLOCATED 节点")

    return (
        "[SLURM_NODES]\n"
        f"{_format_command(cmd, return_code, stdout, stderr)}\n\n"
        "[NODE_STATUS_SIGNALS]\n"
        f"{signals if signals else []}\n\n"
        "[DIAGNOSIS_HINT]\n"
        "如果作业长期 PD 且节点大多 ALLOCATED，通常是资源不足；"
        "如果目标节点 DOWN/DRAIN，需要查看 scontrol show node <NODE_NAME> 的 Reason。"
    )

@tool
def diagnose_slurm_file(file_path: str, tail_lines: int = 120, max_chars: int = 20000) -> str:
    """
    Read and diagnose a Slurm-related log file, such as squeue output,
    scontrol show job output, sbatch error, or sinfo output.

    Args:
        file_path: Path to the Slurm log file.
        tail_lines: Number of lines to inspect from the end of the file.
        max_chars: Maximum number of characters to read.

    Returns:
        A structured Slurm diagnosis based on file content.
    """
    path = Path(file_path).expanduser().resolve()

    if not path.exists():
        return (
            "[SLURM_FILE_DIAGNOSIS]\n"
            "status: file_not_found\n"
            f"file_path: {file_path}\n"
            "message: Slurm 日志文件不存在，请检查路径。"
        )

    if not path.is_file():
        return (
            "[SLURM_FILE_DIAGNOSIS]\n"
            "status: invalid_path\n"
            f"file_path: {file_path}\n"
            "message: 当前路径不是文件。"
        )

    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception as exc:
        return (
            "[SLURM_FILE_DIAGNOSIS]\n"
            "status: read_error\n"
            f"file_path: {file_path}\n"
            f"error: {type(exc).__name__}: {exc}"
        )

    if len(text) > max_chars:
        text = text[-max_chars:]

    lines = text.splitlines()
    tailed = "\n".join(lines[-tail_lines:])

    slurm_result = diagnose_slurm_text(tailed)

    return (
        "[SLURM_FILE_DIAGNOSIS]\n"
        "status: ok\n"
        f"file_path: {path}\n"
        f"tail_lines: {tail_lines}\n\n"
        "[SLURM_TEXT_ANALYSIS]\n"
        f"{slurm_result}\n\n"
        "[SLURM_LOG_TAIL]\n"
        f"{tailed}"
    )