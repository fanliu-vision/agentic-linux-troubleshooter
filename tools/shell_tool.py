import shlex
import subprocess
from typing import Optional

from smolagents import tool


DANGEROUS_TOKENS = {
    "rm",
    "sudo",
    "su",
    "kill",
    "killall",
    "pkill",
    "reboot",
    "shutdown",
    "mkfs",
    "dd",
    "chmod",
    "chown",
    "mount",
    "umount",
    "systemctl",
    "service",
    "iptables",
    "firewall-cmd",
    "conda",
    "pip",
    "apt",
    "yum",
    "dnf",
}

DEFAULT_ALLOWED_PREFIXES = {
    "df",
    "du",
    "ls",
    "free",
    "ps",
    "top",
    "whoami",
    "pwd",
    "hostname",
    "uptime",
    "nvidia-smi",
    "hy-smi",
    "squeue",
    "scontrol",
    "ss",
    "netstat",
    "lsof",
    "cat",
    "tail",
    "head",
    "grep",
}


def _truncate_text(text: str, max_chars: int = 12000) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n\n[OUTPUT_TRUNCATED: 输出过长，已截断]"


def _validate_command(command: str, allowed_prefixes: Optional[set[str]] = None) -> tuple[bool, str]:
    command = command.strip()

    if not command:
        return False, "命令为空。"

    if any(symbol in command for symbol in [";", "&&", "||", "`", "$(", ">", ">>", "<"]):
        return False, "命令中包含管道式/重定向式/复合执行符号，当前阶段禁止执行。"

    try:
        parts = shlex.split(command)
    except ValueError as exc:
        return False, f"命令解析失败：{exc}"

    if not parts:
        return False, "命令解析后为空。"

    first = parts[0]
    allowed_prefixes = allowed_prefixes or DEFAULT_ALLOWED_PREFIXES

    if first in DANGEROUS_TOKENS:
        return False, f"检测到危险命令：{first}。当前工具只允许只读排查命令。"

    if first not in allowed_prefixes:
        return False, f"命令 {first} 不在白名单中。当前阶段只允许执行只读诊断命令。"

    return True, "OK"


@tool
def run_shell_command(command: str, timeout: int = 15, max_output_chars: int = 12000) -> str:
    """
    Execute a safe read-only Linux diagnostic shell command and return stdout/stderr.

    Args:
        command: A read-only diagnostic command, such as 'df -h', 'ps aux', 'free -h', 'ss -lntp', 'nvidia-smi', or 'hy-smi'.
        timeout: Maximum execution time in seconds.
        max_output_chars: Maximum number of characters returned to the agent.

    Returns:
        A structured text result containing command, return code, stdout and stderr.
    """
    ok, reason = _validate_command(command)
    if not ok:
        return (
            "[COMMAND_BLOCKED]\n"
            f"command: {command}\n"
            f"reason: {reason}\n"
            "suggestion: 请改用只读诊断命令，例如 df -h、du -sh、ps aux、ss -lntp、nvidia-smi、hy-smi。"
        )

    try:
        result = subprocess.run(
            shlex.split(command),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return (
            "[COMMAND_TIMEOUT]\n"
            f"command: {command}\n"
            f"timeout: {timeout}s\n"
            "reason: 命令执行超时，可能是系统负载高或命令输出过多。"
        )
    except FileNotFoundError:
        return (
            "[COMMAND_NOT_FOUND]\n"
            f"command: {command}\n"
            "reason: 当前系统中没有找到该命令。"
        )
    except Exception as exc:
        return (
            "[COMMAND_ERROR]\n"
            f"command: {command}\n"
            f"error: {type(exc).__name__}: {exc}"
        )

    stdout = _truncate_text(result.stdout.strip(), max_output_chars)
    stderr = _truncate_text(result.stderr.strip(), max_output_chars)

    return (
        "[COMMAND_RESULT]\n"
        f"command: {command}\n"
        f"return_code: {result.returncode}\n\n"
        "[STDOUT]\n"
        f"{stdout if stdout else '<empty>'}\n\n"
        "[STDERR]\n"
        f"{stderr if stderr else '<empty>'}"
    )