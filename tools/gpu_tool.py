import re
import shutil
import subprocess
import shlex

from smolagents import tool


TRAINING_KEYWORDS = [
    "python",
    "torch",
    "train",
    "deepspeed",
    "accelerate",
    "llama",
    "vllm",
    "transformers",
    "finetune",
    "sft",
]


def _run_command(command: list[str], timeout: int = 15) -> tuple[int, str, str]:
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


def _format_command_result(command: list[str], return_code: int, stdout: str, stderr: str, max_chars: int = 6000) -> str:
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


def _get_training_processes(max_lines: int = 30) -> str:
    cmd = ["ps", "-eo", "user,pid,ppid,%cpu,%mem,stat,start,time,cmd"]
    return_code, stdout, stderr = _run_command(cmd, timeout=10)

    if return_code != 0:
        return _format_command_result(cmd, return_code, stdout, stderr)

    lines = stdout.splitlines()
    if not lines:
        return "<empty>"

    header = lines[0]
    matched = []

    for line in lines[1:]:
        lower_line = line.lower()
        if any(keyword in lower_line for keyword in TRAINING_KEYWORDS):
            matched.append(line)

    matched = matched[:max_lines]

    if not matched:
        return "未发现明显训练相关进程。"

    return "\n".join([header] + matched)


def _parse_nvidia_query(stdout: str) -> str:
    """
    Parse nvidia-smi csv output:
    index,name,memory.total,memory.used,memory.free,utilization.gpu
    """
    if not stdout.strip():
        return "未获取到 NVIDIA GPU 摘要。"

    lines = []
    for raw in stdout.splitlines():
        parts = [x.strip() for x in raw.split(",")]
        if len(parts) < 6:
            lines.append(raw)
            continue

        index, name, total, used, free, util = parts[:6]

        try:
            total_i = int(total)
            used_i = int(used)
            free_i = int(free)
            util_i = int(util)
            used_ratio = used_i / total_i * 100 if total_i else 0
            lines.append(
                f"GPU {index}: {name} | total={total_i}MiB | used={used_i}MiB "
                f"({used_ratio:.1f}%) | free={free_i}MiB | util={util_i}%"
            )
        except ValueError:
            lines.append(raw)

    return "\n".join(lines)


@tool
def check_gpu_status(include_processes: bool = True, compact: bool = True) -> str:
    """
    Check NVIDIA GPU or Hygon DCU status for CUDA/HIP out-of-memory troubleshooting.

    Args:
        include_processes: Whether to include filtered training process information.
        compact: Whether to return compact output instead of full raw command output.

    Returns:
        A structured GPU/DCU status report.
    """
    sections = ["[GPU_DCU_STATUS_CHECK]"]

    has_nvidia_smi = shutil.which("nvidia-smi") is not None
    has_hy_smi = shutil.which("hy-smi") is not None

    if has_hy_smi:
        current_env = "Hygon DCU / HIP"
    elif has_nvidia_smi:
        current_env = "NVIDIA GPU / CUDA-visible environment"
    else:
        current_env = "No GPU management command found"

    sections.append("\n[CURRENT_RUNTIME_ENVIRONMENT]")
    sections.append(current_env)

    sections.append("\n[COMMAND_AVAILABILITY]")
    sections.append(f"nvidia-smi: {'available' if has_nvidia_smi else 'not found'}")
    sections.append(f"hy-smi: {'available' if has_hy_smi else 'not found'}")

    if has_nvidia_smi:
        query_cmd = [
            "nvidia-smi",
            "--query-gpu=index,name,memory.total,memory.used,memory.free,utilization.gpu",
            "--format=csv,noheader,nounits",
        ]
        return_code, stdout, stderr = _run_command(query_cmd, timeout=15)

        sections.append("\n[NVIDIA_GPU_SUMMARY]")
        if return_code == 0:
            sections.append(_parse_nvidia_query(stdout))
        else:
            sections.append(_format_command_result(query_cmd, return_code, stdout, stderr))

    if has_hy_smi:
        hy_cmd = ["hy-smi"]
        return_code, stdout, stderr = _run_command(hy_cmd, timeout=15)
        sections.append("\n[HY_SMI_SUMMARY]")
        if compact and stdout:
            lines = stdout.splitlines()
            sections.append("\n".join(lines[:80]))
            if len(lines) > 80:
                sections.append("[HY_SMI_OUTPUT_TRUNCATED]")
        else:
            sections.append(_format_command_result(hy_cmd, return_code, stdout, stderr))

    if include_processes:
        sections.append("\n[TRAINING_RELATED_PROCESSES]")
        sections.append(_get_training_processes())

    sections.append("\n[ENVIRONMENT_NOTE]")
    if has_nvidia_smi and not has_hy_smi:
        sections.append(
            "当前运行环境检测到 NVIDIA GPU，但没有 hy-smi。"
            "如果日志中出现 HIP/DCU 报错，说明日志可能来自另一台 DCU/ROCm 机器，"
            "当前 GPU 状态只能作为本机参考，不能直接代表日志发生时的真实机器状态。"
        )
    elif has_hy_smi:
        sections.append("当前运行环境支持 hy-smi，适合进一步排查 HIP/DCU 显存占用。")
    else:
        sections.append("当前环境未发现 nvidia-smi 或 hy-smi，无法直接读取 GPU/DCU 状态。")

    sections.append("\n[DIAGNOSIS_HINT]")
    sections.append(
        "如果日志显示 OOM，但当前 free 显存充足，需要区分日志发生环境与当前运行环境；"
        "如果日志发生在远程训练节点，应在对应节点上执行 GPU/DCU 检查命令。"
    )

    return "\n".join(sections)