import re
from pathlib import Path
from typing import Dict, List, Tuple

from smolagents import tool


ISSUE_PRIORITY = {
    # 越小优先级越高
    "gpu": 10,
    "slurm": 20,
    "disk": 30,
    "python_env": 40,
    "network_port": 50,
    "log": 80,
    "unknown": 99,
}


ROUTE_RULES: Dict[str, Dict[str, List[str]]] = {
    "disk": {
        "keywords": [
            "no space left on device",
            "disk quota exceeded",
            "errno 28",
            "quota",
            "df -h",
            "df -ih",
            "du -sh",
            "inode",
            "/tmp no space",
            "磁盘",
            "空间不足",
            "没有空间",
            "配额",
            "硬盘满",
            "目录太大",
            "缓存太大",
        ],
        "tools": [
            "check_disk_usage",
            "diagnose_log_file",
            "diagnose_mixed_log_file",
            "run_shell_command",
        ],
        "checks": [
            "df -h 查看文件系统空间",
            "df -ih 查看 inode 是否耗尽",
            "du -sh 查看目录占用",
            "检查 /tmp、~/.cache、conda/pip/huggingface 缓存",
        ],
    },
    "gpu": {
        "keywords": [
            "cuda out of memory",
            "hip out of memory",
            "outofmemoryerror",
            "oom-kill",
            "oom kill",
            "accelerator memory constraints",
            "exceeded memory",
            "oom",
            "显存",
            "gpu",
            "dcu",
            "cuda",
            "hip",
            "nvidia-smi",
            "hy-smi",
            "rocm-smi",
            "pytorch_cuda_alloc_conf",
            "pytorch_hip_alloc_conf",
            "vram used memory",
            "vram free memory",
        ],
        "tools": [
            "diagnose_mixed_log_file",
            "diagnose_log_file",
            "check_gpu_status",
            "run_shell_command",
        ],
        "checks": [
            "检查日志中的 CUDA/HIP OOM 关键行",
            "检查 nvidia-smi / hy-smi 显存占用",
            "检查 batch size、混合精度、梯度检查点",
            "检查是否存在残留训练进程",
        ],
    },
    "network_port": {
        "keywords": [
            "address already in use",
            "errno 98",
            "connection refused",
            "connection timed out",
            "port",
            "tensorboard",
            "端口",
            "监听",
            "不通",
            "无法连接",
            "9100",
            "19100",
            "bind",
            "lsof",
            "ss -lntp",
            "netstat",
        ],
        "tools": [
            "diagnose_mixed_log_file",
            "diagnose_log_file",
            "run_shell_command",
        ],
        "checks": [
            "ss -lntp 查看监听端口",
            "lsof -i 查看端口占用",
            "确认服务是否启动",
            "确认绑定地址是 127.0.0.1 还是 0.0.0.0",
        ],
    },
    "slurm": {
        "keywords": [
            "slurm",
            "slurmstepd",
            "squeue",
            "sbatch",
            "scontrol",
            "sinfo",
            "sacct",
            "pending",
            "jobstate=pending",
            "reason=resources",
            "nodelist(reason)",
            "node down",
            "drain",
            "not responding",
            "partition",
            "gres",
            "submitted batch job",
            "作业",
            "排队",
            "调度",
            "节点",
            "分区",
            "一直 pd",
            "任务不运行",
            "提交任务",
        ],
        "tools": [
            "diagnose_mixed_log_file",
            "diagnose_slurm_file",
            "diagnose_slurm_text",
            "check_slurm_queue",
            "check_slurm_nodes",
            "check_slurm_job",
        ],
        "checks": [
            "squeue 查看作业状态",
            "scontrol show job 查看 Pending reason",
            "sinfo 查看分区和节点状态",
            "scontrol show node 查看节点 DOWN/DRAIN 原因",
        ],
    },
    "python_env": {
        "keywords": [
            "modulenotfounderror",
            "importerror",
            "no module named",
            "pip path",
            "python interpreter",
            "do not belong to the same environment",
            "pip",
            "conda",
            "venv",
            "virtualenv",
            "python path",
            "which python",
            "site-packages",
            "依赖",
            "包没安装",
            "模块找不到",
            "环境变量",
            "解释器",
            "版本冲突",
        ],
        "tools": [
            "diagnose_mixed_log_file",
            "diagnose_python_error_text",
            "check_python_environment",
            "check_python_package",
            "diagnose_log_file",
        ],
        "checks": [
            "确认当前 python 路径",
            "确认 pip 是否属于同一个解释器",
            "检查虚拟环境是否激活",
            "检查缺失包是否安装到当前环境",
        ],
    },
}


def _normalize_text(text: str) -> str:
    return text.lower().strip()


def extract_log_path(question: str) -> str:
    """
    Extract a .log path from user question.
    """
    match = re.search(r"[\w./\\-]+\.log\b", question, flags=re.IGNORECASE)
    return match.group(0) if match else ""


def read_log_preview_from_question(question: str, max_chars: int = 12000) -> Tuple[str, str]:
    """
    If question contains a .log path, read a compact preview from that file.
    Returns: (log_path, preview_text)
    """
    log_path = extract_log_path(question)
    if not log_path:
        return "", ""

    path = Path(log_path).expanduser()
    if not path.exists():
        # 尝试按当前工作目录解析
        path = Path.cwd() / log_path

    if not path.exists() or not path.is_file():
        return log_path, ""

    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return log_path, ""

    if len(text) > max_chars:
        text = text[-max_chars:]

    return str(path), text


def _score_issue_types(text: str) -> Dict[str, List[str]]:
    normalized = _normalize_text(text)
    scores: Dict[str, List[str]] = {}

    for issue_type, rule in ROUTE_RULES.items():
        matched = []
        for keyword in rule["keywords"]:
            if keyword.lower() in normalized:
                matched.append(keyword)
        if matched:
            scores[issue_type] = matched

    return scores


def _detect_primary_failure(text: str, detected_types: List[str]) -> str:
    """
    Decide primary issue type by explicit primary_failure first,
    then by severe final-failure signals,
    then by issue priority.
    """
    lower = text.lower()

    # 1. 优先尊重日志中的 primary_failure
    primary_match = re.search(r"primary_failure\s*=\s*([^\n\r]+)", lower)
    if primary_match:
        value = primary_match.group(1)
        if "hip out of memory" in value or "cuda out of memory" in value or "oom" in value:
            return "gpu"
        if "no space" in value or "disk" in value:
            return "disk"
        if "module" in value or "import" in value or "python" in value:
            return "python_env"
        if "port" in value or "address already" in value:
            return "network_port"
        if "slurm" in value or "pending" in value:
            return "slurm"

    # 2. 最终失败强信号
    severe_gpu_signals = [
        "torch.outofmemoryerror",
        "hip out of memory",
        "cuda out of memory",
        "slurmstepd",
        "oom-kill",
        "accelerator memory constraints",
    ]
    if "gpu" in detected_types and any(s in lower for s in severe_gpu_signals):
        return "gpu"

    # 3. 如果只有 Slurm pending，没有后续训练错误，则主问题是 Slurm
    if "slurm" in detected_types and len(detected_types) == 1:
        return "slurm"

    # 4. 按严重程度排序
    if detected_types:
        return sorted(detected_types, key=lambda x: ISSUE_PRIORITY.get(x, 99))[0]

    return "log" if ".log" in lower else "unknown"


def _recommended_tools_for_types(issue_types: List[str]) -> List[str]:
    tools = []
    for issue_type in issue_types:
        for tool_name in ROUTE_RULES.get(issue_type, {}).get("tools", []):
            if tool_name not in tools:
                tools.append(tool_name)

    if not tools:
        tools = ["diagnose_log_file"]

    return tools


def _recommended_checks_for_types(issue_types: List[str]) -> List[str]:
    checks = []
    for issue_type in issue_types:
        for check in ROUTE_RULES.get(issue_type, {}).get("checks", []):
            if check not in checks:
                checks.append(check)

    if not checks:
        checks = ["优先读取并诊断日志文件"]

    return checks

def _primary_tools_for_route(
    primary_issue_type: str,
    all_detected_issue_types: list[str],
    has_log_file: bool,
) -> list[str]:
    """
    Decide the minimum tool set that should be called first.

    primary_tools should be small and stable.
    The goal is to avoid unnecessary tool calls.
    """
    # 复杂日志：优先只调用混合日志诊断工具
    if has_log_file and len(all_detected_issue_types) > 1:
        return ["diagnose_mixed_log_file"]

    # 单一日志：按主类型选择日志诊断工具
    if has_log_file:
        if primary_issue_type == "slurm":
            return ["diagnose_slurm_file"]
        if primary_issue_type in {"gpu", "disk", "python_env", "network_port"}:
            return ["diagnose_log_file"]
        return ["diagnose_log_file"]

    # 非日志问题：按类型选择最小工具
    if primary_issue_type == "disk":
        return ["check_disk_usage"]

    if primary_issue_type == "gpu":
        return ["check_gpu_status"]

    if primary_issue_type == "network_port":
        return ["run_shell_command"]

    if primary_issue_type == "slurm":
        return ["check_slurm_queue", "check_slurm_nodes"]

    if primary_issue_type == "python_env":
        return ["check_python_environment"]

    return ["diagnose_log_file"]


def _optional_tools_for_route(
    primary_issue_type: str,
    all_detected_issue_types: list[str],
    primary_tools: list[str],
) -> list[str]:
    """
    Decide optional tools that can be used only when primary tools are insufficient.

    These tools should not be called by default.
    """
    optional_tools = []

    for issue_type in all_detected_issue_types:
        if issue_type == "gpu":
            optional_tools.extend(["check_gpu_status", "run_shell_command"])
        elif issue_type == "disk":
            optional_tools.extend(["check_disk_usage", "run_shell_command"])
        elif issue_type == "network_port":
            optional_tools.extend(["run_shell_command"])
        elif issue_type == "slurm":
            optional_tools.extend([
                "diagnose_slurm_text",
                "check_slurm_queue",
                "check_slurm_nodes",
                "check_slurm_job",
            ])
        elif issue_type == "python_env":
            optional_tools.extend([
                "diagnose_python_error_text",
                "check_python_environment",
                "check_python_package",
            ])

    # 去重，同时去掉 primary_tools 中已有的工具
    deduped = []
    for tool_name in optional_tools:
        if tool_name not in deduped and tool_name not in primary_tools:
            deduped.append(tool_name)

    return deduped


def classify_issue_dict(question: str, content_preview: str = "") -> Dict[str, object]:
    """
    Classify troubleshooting question. If content_preview is provided,
    route by question + log content together.
    """
    log_path, auto_preview = read_log_preview_from_question(question)
    combined_text = "\n".join([question, content_preview or auto_preview])

    scores = _score_issue_types(combined_text)

    detected_types = sorted(
        scores.keys(),
        key=lambda x: ISSUE_PRIORITY.get(x, 99),
    )

    if detected_types:
        primary = _detect_primary_failure(combined_text, detected_types)
        secondary = [t for t in detected_types if t != primary]

        matched_keywords = []
        for t in detected_types:
            matched_keywords.extend(scores.get(t, []))

        confidence = min(0.98, 0.45 + 0.08 * len(set(matched_keywords)))
        ordered_types = [primary] + secondary

        primary_tools = _primary_tools_for_route(
            primary_issue_type=primary,
            all_detected_issue_types=ordered_types,
            has_log_file=bool(log_path),
        )

        optional_tools = _optional_tools_for_route(
            primary_issue_type=primary,
            all_detected_issue_types=ordered_types,
            primary_tools=primary_tools,
        )

        return {
            "issue_type": primary,  # 兼容旧字段
            "primary_issue_type": primary,
            "secondary_issue_types": secondary,
            "all_detected_issue_types": ordered_types,
            "confidence": round(confidence, 2),
            "matched_keywords": list(dict.fromkeys(matched_keywords)),
            "primary_tools": primary_tools,
            "optional_tools": optional_tools,
            "recommended_tools": primary_tools + optional_tools,  # 兼容旧字段
            "recommended_checks": _recommended_checks_for_types(ordered_types),
            "log_path": log_path,
            "routing_reason": (
                f"基于用户问题和日志内容识别到多类问题：{ordered_types}；"
                f"主问题判断为 {primary}。"
            ),
        }

    if log_path:
        return {
            "issue_type": "log",
            "primary_issue_type": "log",
            "secondary_issue_types": [],
            "all_detected_issue_types": ["log"],
            "confidence": 0.55,
            "matched_keywords": [".log"],
            "primary_tools": ["diagnose_log_file"],
            "optional_tools": ["read_log_file"],
            "recommended_tools": ["diagnose_log_file", "read_log_file"],
            "recommended_checks": ["优先读取并诊断日志文件"],
            "log_path": log_path,
            "routing_reason": "用户问题中包含 .log 文件路径，但日志预览中未明显命中特定故障类别。",
        }

    return {
        "issue_type": "unknown",
        "primary_issue_type": "unknown",
        "secondary_issue_types": [],
        "all_detected_issue_types": ["unknown"],
        "confidence": 0.2,
        "matched_keywords": [],
        "primary_tools": ["diagnose_log_file"],
        "optional_tools": [
            "run_shell_command",
            "check_disk_usage",
            "check_gpu_status",
        ],
        "recommended_tools": [
            "diagnose_log_file",
            "run_shell_command",
            "check_disk_usage",
            "check_gpu_status",
        ],
        "recommended_checks": [
            "先询问或识别错误日志",
            "根据报错关键词选择对应工具",
        ],
        "log_path": "",
        "routing_reason": "未命中明确关键词，需要 Agent 根据上下文继续判断。",
    }


def format_route_context(route: Dict[str, object]) -> str:
    primary_tools = route.get("primary_tools", [])
    optional_tools = route.get("optional_tools", [])
    recommended_tools = route.get("recommended_tools", [])
    checks = route.get("recommended_checks", [])
    keywords = route.get("matched_keywords", [])

    return (
        "[ROUTE_RESULT]\n"
        f"issue_type: {route.get('issue_type')}\n"
        f"primary_issue_type: {route.get('primary_issue_type')}\n"
        f"secondary_issue_types: {route.get('secondary_issue_types')}\n"
        f"all_detected_issue_types: {route.get('all_detected_issue_types')}\n"
        f"confidence: {route.get('confidence')}\n"
        f"log_path: {route.get('log_path')}\n"
        f"matched_keywords: {keywords}\n"
        f"routing_reason: {route.get('routing_reason')}\n\n"
        "[PRIMARY_TOOLS]\n"
        + "\n".join(f"- {tool}" for tool in primary_tools)
        + "\n\n[OPTIONAL_TOOLS]\n"
        + "\n".join(f"- {tool}" for tool in optional_tools)
        + "\n\n[RECOMMENDED_TOOLS_COMPAT]\n"
        + "\n".join(f"- {tool}" for tool in recommended_tools)
        + "\n\n[RECOMMENDED_CHECKS]\n"
        + "\n".join(f"- {check}" for check in checks)
    )


@tool
def route_issue_type(question: str) -> str:
    """
    Classify a troubleshooting question into primary issue type and secondary issue types.
    It supports content-aware routing when the question contains a .log file path.

    Args:
        question: User troubleshooting question or pasted error text.

    Returns:
        A structured routing result with primary issue type, secondary issue types,
        matched keywords, recommended tools and recommended checks.
    """
    route = classify_issue_dict(question)
    return format_route_context(route)