import re
from pathlib import Path
from smolagents import tool


ERROR_PATTERNS = {
    "disk_full": [
        "No space left on device",
        "Disk quota exceeded",
        "not enough space",
        "ENOSPC",
        "Errno 28",
    ],
    "cuda_oom": [
        "CUDA out of memory",
        "torch.cuda.OutOfMemoryError",
        "CUBLAS_STATUS_ALLOC_FAILED",
        "PYTORCH_CUDA_ALLOC_CONF",
    ],
    "hip_oom": [
        "HIP out of memory",
        "torch.OutOfMemoryError",
        "PYTORCH_HIP_ALLOC_CONF",
        "HSA_STATUS_ERROR_OUT_OF_RESOURCES",
    ],
    "permission": [
        "Permission denied",
        "Access denied",
        "pam_slurm_adopt",
    ],
    "module_missing": [
        "ModuleNotFoundError",
        "ImportError",
        "No module named",
    ],
    "file_missing": [
        "FileNotFoundError",
        "No such file or directory",
    ],
    "port_conflict": [
        "Address already in use",
        "port is already allocated",
        "bind: address already in use",
        "Errno 98",
    ],
    "connection": [
        "Connection refused",
        "Connection timed out",
        "Could not connect",
        "Failed to connect",
    ],
    "slurm_pending": [
        "JobState=PENDING",
        "Reason=Resources",
        "NODELIST(REASON)",
        "Submitted batch job",
        "squeue",
        "sbatch",
    ],
    "slurm_oom": [
        "slurmstepd",
        "oom-kill",
        "exceeded memory or accelerator memory constraints",
        "StepId=",
    ],
    "python_env_mismatch": [
        "Python interpreter and pip path do not belong to the same environment",
        "python=/usr/bin/python3",
        "pip=/home",
        "CONDA_PREFIX=",
        "VIRTUAL_ENV=<not set>",
    ],
}


def _safe_read_text(path: Path, max_chars: int) -> str:
    text = path.read_text(encoding="utf-8", errors="ignore")
    if len(text) > max_chars:
        return text[-max_chars:]
    return text


def _tail_lines(text: str, lines: int) -> str:
    all_lines = text.splitlines()
    return "\n".join(all_lines[-lines:])


def _match_patterns(text: str) -> dict[str, list[str]]:
    matched = {}
    lower_text = text.lower()

    for category, patterns in ERROR_PATTERNS.items():
        hits = []
        for p in patterns:
            if p.lower() in lower_text:
                hits.append(p)
        if hits:
            matched[category] = hits

    return matched


def _build_log_diagnosis(matched: dict[str, list[str]]) -> str:
    if not matched:
        return "未匹配到内置错误模式，需要结合完整日志和系统命令继续判断。"

    explanations = []

    if "disk_full" in matched:
        explanations.append(
            "检测到磁盘空间不足或配额不足相关错误，优先检查 df -h、quota -s、du -sh ~/.cache 等。"
        )

    if "cuda_oom" in matched:
        explanations.append(
            "检测到 CUDA 显存不足，常见原因包括 batch size 过大、其他进程占用显存、模型过大或显存碎片。"
        )

    if "hip_oom" in matched:
        explanations.append(
            "检测到 HIP/DCU 显存不足，常见于 Hygon DCU 或 ROCm/DTK 环境，需要检查 hy-smi、训练进程和 PYTORCH_HIP_ALLOC_CONF。"
        )

    if "permission" in matched:
        explanations.append(
            "检测到权限问题，可能是没有 Slurm 分配的作业却登录计算节点，或当前用户无目录访问权限。"
        )

    if "module_missing" in matched:
        explanations.append(
            "检测到 Python 依赖缺失，可能是环境未激活、依赖未安装或 Python 解释器不一致。"
        )

    if "file_missing" in matched:
        explanations.append(
            "检测到文件路径不存在，建议检查工作目录、相对路径、数据集路径和配置文件路径。"
        )

    if "port_conflict" in matched:
        explanations.append(
            "检测到端口被占用，建议使用 ss -lntp 或 lsof -i 检查端口占用进程。"
        )

    if "connection" in matched:
        explanations.append(
            "检测到连接失败，可能是服务未启动、防火墙限制、端口未监听或网络不可达。"
        )

    if "slurm_pending" in matched:
        explanations.append(
            "检测到 Slurm 作业 Pending 或资源等待信息，建议检查 squeue、scontrol show job、sinfo 和 Pending reason。"
        )

    if "slurm_oom" in matched:
        explanations.append(
            "检测到 Slurm step 的 OOM kill 或资源约束错误，说明作业可能因内存或加速器显存超限被调度系统终止。"
        )

    if "python_env_mismatch" in matched:
        explanations.append(
            "检测到 Python 解释器与 pip/conda 环境不一致，可能导致包已安装但运行时仍 ModuleNotFoundError。"
        )

    return "\n".join(f"- {item}" for item in explanations)


@tool
def read_log_file(file_path: str, tail_lines: int = 120, max_chars: int = 20000) -> str:
    """
    Read a local log file and return the last N lines for troubleshooting.

    Args:
        file_path: Path to the log file.
        tail_lines: Number of lines to return from the end of the file.
        max_chars: Maximum number of characters to read from the file.

    Returns:
        Log content and a lightweight rule-based diagnosis.
    """
    path = Path(file_path).expanduser().resolve()

    if not path.exists():
        return (
            "[LOG_FILE_NOT_FOUND]\n"
            f"file_path: {file_path}\n"
            "reason: 日志文件不存在，请检查路径是否正确。"
        )

    if not path.is_file():
        return (
            "[LOG_PATH_INVALID]\n"
            f"file_path: {file_path}\n"
            "reason: 当前路径不是文件。"
        )

    try:
        text = _safe_read_text(path, max_chars=max_chars)
    except Exception as exc:
        return (
            "[LOG_READ_ERROR]\n"
            f"file_path: {file_path}\n"
            f"error: {type(exc).__name__}: {exc}"
        )

    tailed = _tail_lines(text, tail_lines)
    matched = _match_patterns(tailed)
    diagnosis = _build_log_diagnosis(matched)

    return (
        "[LOG_CONTENT]\n"
        f"file_path: {path}\n"
        f"tail_lines: {tail_lines}\n\n"
        "[MATCHED_ERROR_PATTERNS]\n"
        f"{matched if matched else '{}'}\n\n"
        "[RULE_BASED_DIAGNOSIS]\n"
        f"{diagnosis}\n\n"
        "[LOG_TAIL]\n"
        f"{tailed}"
    )


@tool
def analyze_log_text(log_text: str) -> str:
    """
    Analyze pasted log text and identify common Linux, GPU, Slurm, Python and network errors.

    Args:
        log_text: Raw error log text pasted by the user.

    Returns:
        Matched error patterns and troubleshooting hints.
    """
    if not log_text.strip():
        return "[EMPTY_LOG_TEXT]\n用户没有提供日志文本。"

    matched = _match_patterns(log_text)
    diagnosis = _build_log_diagnosis(matched)

    return (
        "[LOG_TEXT_ANALYSIS]\n\n"
        "[MATCHED_ERROR_PATTERNS]\n"
        f"{matched if matched else '{}'}\n\n"
        "[RULE_BASED_DIAGNOSIS]\n"
        f"{diagnosis}"
    )

@tool
def diagnose_log_file(file_path: str, tail_lines: int = 160, max_chars: int = 20000) -> str:
    """
    Diagnose a local log file in one step: read log, match known error patterns,
    infer runtime environment, and return compact troubleshooting evidence.

    Args:
        file_path: Path to the log file.
        tail_lines: Number of lines to inspect from the end of the file.
        max_chars: Maximum number of characters to read.

    Returns:
        A compact diagnosis report with error category, key evidence and next checks.
    """
    path = Path(file_path).expanduser().resolve()

    if not path.exists():
        return (
            "[LOG_DIAGNOSIS]\n"
            f"status: file_not_found\n"
            f"file_path: {file_path}\n"
            "message: 日志文件不存在，请检查路径是否正确。"
        )

    if not path.is_file():
        return (
            "[LOG_DIAGNOSIS]\n"
            f"status: invalid_path\n"
            f"file_path: {file_path}\n"
            "message: 当前路径不是文件。"
        )

    try:
        text = _safe_read_text(path, max_chars=max_chars)
    except Exception as exc:
        return (
            "[LOG_DIAGNOSIS]\n"
            f"status: read_error\n"
            f"file_path: {file_path}\n"
            f"error: {type(exc).__name__}: {exc}"
        )

    tailed = _tail_lines(text, tail_lines)
    matched = _match_patterns(tailed)
    diagnosis = _build_log_diagnosis(matched)

    key_lines = []
    important_keywords = [
        "error",
        "exception",
        "out of memory",
        "no space left",
        "permission denied",
        "connection refused",
        "address already in use",
        "modulenotfounderror",
        "filenotfounderror",
        "traceback",
        "pytorch_hip_alloc_conf",
    ]

    for line in tailed.splitlines():
        lower_line = line.lower()
        if any(k in lower_line for k in important_keywords):
            key_lines.append(line)

    key_lines = key_lines[-20:]

    if "hip_oom" in matched:
        inferred_env = "HIP/DCU/ROCm-like"
        problem_type = "gpu_oom_hip"
        next_checks = [
            "hy-smi",
            "ps -ef 查看训练进程",
            "echo $PYTORCH_HIP_ALLOC_CONF",
            "检查 batch size / gradient checkpointing / mixed precision 配置",
        ]
    elif "cuda_oom" in matched:
        inferred_env = "CUDA/NVIDIA"
        problem_type = "gpu_oom_cuda"
        next_checks = [
            "nvidia-smi",
            "ps -ef 查看训练进程",
            "检查 batch size / gradient checkpointing / mixed precision 配置",
        ]
    elif "disk_full" in matched:
        inferred_env = "Linux filesystem"
        problem_type = "disk_full"
        next_checks = [
            "df -h",
            "df -ih",
            "du -sh ~/.cache",
            "du -sh 当前项目目录",
        ]
    elif "port_conflict" in matched or "connection" in matched:
        inferred_env = "network/service"
        problem_type = "network_or_port"
        next_checks = [
            "ss -lntp",
            "检查服务是否启动",
            "检查端口是否被占用",
        ]
    elif "module_missing" in matched:
        inferred_env = "python_environment"
        problem_type = "python_dependency"
        next_checks = [
            "which python",
            "python -m pip show 缺失包",
            "确认虚拟环境是否激活",
        ]
    else:
        inferred_env = "unknown"
        problem_type = "unknown"
        next_checks = [
            "补充完整日志",
            "检查最近的 Traceback",
            "结合系统状态命令继续排查",
        ]

    return (
        "[LOG_DIAGNOSIS]\n"
        f"status: ok\n"
        f"file_path: {path}\n"
        f"problem_type: {problem_type}\n"
        f"inferred_log_environment: {inferred_env}\n\n"
        "[MATCHED_ERROR_PATTERNS]\n"
        f"{matched if matched else '{}'}\n\n"
        "[RULE_BASED_DIAGNOSIS]\n"
        f"{diagnosis}\n\n"
        "[KEY_EVIDENCE_LINES]\n"
        f"{chr(10).join(key_lines) if key_lines else '<no key lines extracted>'}\n\n"
        "[SUGGESTED_NEXT_CHECKS]\n"
        + "\n".join(f"- {item}" for item in next_checks)
    )

def _extract_timestamp(line: str) -> str:
    """
    Extract timestamp from a log line.

    Supported format:
    [2026-05-11 09:40:16] message
    """
    match = re.search(r"\[(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\]", line)
    return match.group(1) if match else ""


def _extract_key_timeline(text: str) -> list[str]:
    """
    Extract key timeline events from log text.

    If an important line has no timestamp, inherit the nearest previous timestamp.
    This makes traceback lines and error lines easier to place in chronological order.
    """
    timeline_keywords = [
        "Submitted batch job",
        "JobState=PENDING",
        "Reason=Resources",
        "started on node",
        "python interpreter",
        "ModuleNotFoundError",
        "No module named",
        "Address already in use",
        "No space left on device",
        "hy-smi snapshot",
        "CUDA out of memory",
        "HIP out of memory",
        "OutOfMemoryError",
        "slurmstepd",
        "oom-kill",
        "primary_failure",
        "secondary_issues",
    ]

    timeline = []
    current_timestamp = ""

    for line in text.splitlines():
        line_timestamp = _extract_timestamp(line)
        if line_timestamp:
            current_timestamp = line_timestamp

        lower_line = line.lower()
        if any(k.lower() in lower_line for k in timeline_keywords):
            clean_line = line.strip()

            # 如果本行没有时间戳，就继承最近时间戳
            if not line_timestamp and current_timestamp:
                timeline.append(f"[{current_timestamp}] {clean_line}")
            else:
                timeline.append(clean_line)

    # 去重，保持顺序
    deduped = []
    for item in timeline:
        if item not in deduped:
            deduped.append(item)

    return deduped[-80:]


def _detect_issue_types_from_matched(matched: dict[str, list[str]]) -> list[str]:
    issue_map = {
        "hip_oom": "gpu",
        "cuda_oom": "gpu",
        "slurm_oom": "gpu",
        "disk_full": "disk",
        "module_missing": "python_env",
        "python_env_mismatch": "python_env",
        "port_conflict": "network_port",
        "connection": "network_port",
        "slurm_pending": "slurm",
        "permission": "permission",
        "file_missing": "file_missing",
    }

    detected = []
    for pattern_type in matched:
        issue_type = issue_map.get(pattern_type)
        if issue_type and issue_type not in detected:
            detected.append(issue_type)

    return detected


def _rank_primary_issue(text: str, detected_issue_types: list[str], matched: dict[str, list[str]]) -> tuple[str, list[str]]:
    lower = text.lower()

    # 1. 如果日志明确写了 primary_failure，以它为准
    primary_match = re.search(r"primary_failure\s*=\s*([^\n\r]+)", lower)
    if primary_match:
        value = primary_match.group(1)
        if "hip out of memory" in value or "cuda out of memory" in value or "oom" in value:
            primary = "gpu"
        elif "no space" in value or "disk" in value:
            primary = "disk"
        elif "module" in value or "python" in value or "import" in value:
            primary = "python_env"
        elif "port" in value or "address already" in value:
            primary = "network_port"
        elif "slurm" in value or "pending" in value:
            primary = "slurm"
        else:
            primary = detected_issue_types[0] if detected_issue_types else "unknown"

        secondary = [x for x in detected_issue_types if x != primary]
        return primary, secondary

    # 2. 强终止类错误优先
    if "gpu" in detected_issue_types and (
        "hip out of memory" in lower
        or "cuda out of memory" in lower
        or "torch.outofmemoryerror" in lower
        or "oom-kill" in lower
        or "accelerator memory constraints" in lower
    ):
        primary = "gpu"
        secondary = [x for x in detected_issue_types if x != primary]
        return primary, secondary

    # 3. 权重排序
    priority = {
        "gpu": 10,
        "slurm": 20,
        "disk": 30,
        "python_env": 40,
        "network_port": 50,
        "permission": 60,
        "file_missing": 70,
    }

    if detected_issue_types:
        primary = sorted(detected_issue_types, key=lambda x: priority.get(x, 99))[0]
        secondary = [x for x in detected_issue_types if x != primary]
        return primary, secondary

    return "unknown", []


def _extract_primary_failure_line(text: str) -> str:
    for line in text.splitlines():
        if "primary_failure" in line.lower():
            return line.strip()

    key_failure_keywords = [
        "torch.OutOfMemoryError",
        "CUDA out of memory",
        "HIP out of memory",
        "slurmstepd",
        "No space left on device",
        "ModuleNotFoundError",
        "Address already in use",
    ]

    for line in reversed(text.splitlines()):
        if any(k.lower() in line.lower() for k in key_failure_keywords):
            return line.strip()

    return "<not detected>"


@tool
def diagnose_mixed_log_file(file_path: str, tail_lines: int = 220, max_chars: int = 30000) -> str:
    """
    Diagnose a complex Linux project log that may contain multiple issues.
    It returns primary issue, secondary issues, matched patterns, timeline,
    severity ordering and recommended next checks.

    Args:
        file_path: Path to the log file.
        tail_lines: Number of lines to inspect from the end of the file.
        max_chars: Maximum number of characters to read.

    Returns:
        A structured mixed-log diagnosis report.
    """
    path = Path(file_path).expanduser().resolve()

    if not path.exists():
        return (
            "[MIXED_LOG_DIAGNOSIS]\n"
            "status: file_not_found\n"
            f"file_path: {file_path}\n"
            "message: 日志文件不存在，请检查路径。"
        )

    if not path.is_file():
        return (
            "[MIXED_LOG_DIAGNOSIS]\n"
            "status: invalid_path\n"
            f"file_path: {file_path}\n"
            "message: 当前路径不是文件。"
        )

    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception as exc:
        return (
            "[MIXED_LOG_DIAGNOSIS]\n"
            "status: read_error\n"
            f"file_path: {file_path}\n"
            f"error: {type(exc).__name__}: {exc}"
        )

    if len(text) > max_chars:
        text = text[-max_chars:]

    lines = text.splitlines()
    tailed = "\n".join(lines[-tail_lines:])

    matched = _match_patterns(tailed)
    rule_diagnosis = _build_log_diagnosis(matched)

    detected_issue_types = _detect_issue_types_from_matched(matched)
    primary, secondary = _rank_primary_issue(tailed, detected_issue_types, matched)
    timeline = _extract_key_timeline(tailed)
    primary_failure_line = _extract_primary_failure_line(tailed)

    next_checks = []

    if primary == "gpu" or "gpu" in detected_issue_types:
        next_checks.extend([
            "hy-smi 或 nvidia-smi 查看加速器显存占用",
            "检查 batch size、precision、gradient checkpointing",
            "echo $PYTORCH_HIP_ALLOC_CONF 或 echo $PYTORCH_CUDA_ALLOC_CONF",
        ])

    if "disk" in detected_issue_types:
        next_checks.extend([
            "df -h /tmp 或 df -h 目标缓存目录",
            "df -ih /tmp 检查 inode",
            "du -sh /tmp/$USER 或 du -sh 缓存目录",
        ])

    if "python_env" in detected_issue_types:
        next_checks.extend([
            "which python && which pip",
            "python -m pip --version",
            "python -c \"import sys; print(sys.executable)\"",
            "python -m pip show PyYAML",
        ])

    if "network_port" in detected_issue_types:
        next_checks.extend([
            "ss -lntp | grep 9100",
            "lsof -i :9100",
            "更换 TensorBoard 端口，例如 --port 9101",
        ])

    if "slurm" in detected_issue_types:
        next_checks.extend([
            "squeue -u $USER",
            "scontrol show job <JOB_ID>",
            "sinfo -N -l",
            "scontrol show node <NODE_NAME>",
        ])

    # 去重
    next_checks = list(dict.fromkeys(next_checks))

    return (
        "[MIXED_LOG_DIAGNOSIS]\n"
        f"status: ok\n"
        f"file_path: {path}\n"
        f"primary_issue_type: {primary}\n"
        f"secondary_issue_types: {secondary}\n"
        f"all_detected_issue_types: {[primary] + secondary if primary != 'unknown' else secondary}\n"
        f"primary_failure_line: {primary_failure_line}\n\n"
        "[MATCHED_ERROR_PATTERNS]\n"
        f"{matched if matched else '{}'}\n\n"
        "[RULE_BASED_DIAGNOSIS]\n"
        f"{rule_diagnosis}\n\n"
        "[TIMELINE]\n"
        + ("\n".join(f"- {item}" for item in timeline) if timeline else "- <no timeline extracted>")
        + "\n\n[RECOMMENDED_NEXT_CHECKS]\n"
        + ("\n".join(f"- {item}" for item in next_checks) if next_checks else "- 补充完整日志后继续判断")
    )