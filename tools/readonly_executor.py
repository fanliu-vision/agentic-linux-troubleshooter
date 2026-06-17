from __future__ import annotations

import re
import shlex
import subprocess
from dataclasses import dataclass


@dataclass
class CommandResult:
    command: str
    allowed: bool
    return_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    reason: str = ""

    def to_evidence_text(self) -> str:
        return (
            "[COMMAND_RESULT]\n"
            f"command: {self.command}\n"
            f"allowed: {self.allowed}\n"
            f"return_code: {self.return_code}\n"
            f"reason: {self.reason}\n\n"
            "[STDOUT]\n"
            f"{self.stdout if self.stdout else '<empty>'}\n\n"
            "[STDERR]\n"
            f"{self.stderr if self.stderr else '<empty>'}"
        )


class ReadonlyCommandExecutor:
    """
    Execute only allowlisted read-only troubleshooting commands.

    This executor is designed for Stage 4B.
    It should never execute destructive commands such as rm, kill, scancel, sudo, chmod, chown.
    """

    DANGEROUS_PATTERNS = [
        r"\brm\b",
        r"\bkill\b",
        r"\bpkill\b",
        r"\bkillall\b",
        r"\bscancel\b",
        r"\bsudo\b",
        r"\bchmod\b",
        r"\bchown\b",
        r"\bmv\b",
        r"\bcp\b",
        r"\bdd\b",
        r"\bmkfs\b",
        r"\breboot\b",
        r"\bshutdown\b",
        r"\bsystemctl\s+restart\b",
        r"\bpip\s+install\b",
        r"\bconda\s+install\b",
        r">\s*",
        r">>\s*",
        r"\|\s*sh\b",
        r"\|\s*bash\b",
        r"`",
        r"\$\(",
    ]

    SAFE_COMMAND_PATTERNS = [
        r"^df\s+-h(\s+[\w./$-]+)?$",
        r"^df\s+-ih(\s+[\w./$-]+)?$",
        r"^du\s+-sh\s+[\w./$*-]+$",
        r"^ss\s+-lntp(\s*\|\s*grep\s+[\w.:-]+)?$",
        r"^lsof\s+-i\s*:\d+$",
        r"^ps\s+-eo\s+[\w,% .-]+(\s*\|\s*grep\s+[\w./:-]+)?$",
        r"^which\s+python$",
        r"^which\s+pip$",
        r"^python\s+-c\s+\".*\"$",
        r"^python\s+-m\s+pip\s+--version$",
        r"^python\s+-m\s+pip\s+show\s+[\w.-]+$",
        r"^echo\s+\$[A-Za-z_][A-Za-z0-9_]*$",
        r"^nvidia-smi$",
        r"^hy-smi$",
        r"^squeue(\s+-j\s+[0-9_]+|\s+-u\s+[\w.-]+)?$",
        r"^scontrol\s+show\s+job\s+[0-9_]+$",
        r"^sinfo\s+-N\s+-l$",
        r"^scontrol\s+show\s+node\s+[\w.-]+$",
        r"^tail\s+-n\s+\d+\s+[\w./-]+$",
        r"^head\s+-n\s+\d+\s+[\w./-]+$",
        r"^ls\s+-lh\s+[\w./-]+$",
    ]

    def __init__(self, timeout: int = 20, max_output_chars: int = 12000) -> None:
        self.timeout = timeout
        self.max_output_chars = max_output_chars

    def is_allowed(self, command: str) -> tuple[bool, str]:
        command = command.strip()

        if not command:
            return False, "命令为空。"

        for pattern in self.DANGEROUS_PATTERNS:
            if re.search(pattern, command):
                return False, f"命令包含危险模式，已拒绝：{pattern}"

        for pattern in self.SAFE_COMMAND_PATTERNS:
            if re.fullmatch(pattern, command):
                return True, "命令通过只读白名单检查。"

        return False, "命令不在只读白名单中。"

    def run(self, command: str) -> CommandResult:
        command = command.strip()
        allowed, reason = self.is_allowed(command)

        if not allowed:
            return CommandResult(
                command=command,
                allowed=False,
                reason=reason,
            )

        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )

            stdout = self._truncate(result.stdout.strip())
            stderr = self._truncate(result.stderr.strip())

            return CommandResult(
                command=command,
                allowed=True,
                return_code=result.returncode,
                stdout=stdout,
                stderr=stderr,
                reason=reason,
            )

        except subprocess.TimeoutExpired:
            return CommandResult(
                command=command,
                allowed=True,
                return_code=124,
                stdout="",
                stderr="COMMAND_TIMEOUT",
                reason=f"命令超过 {self.timeout}s 超时时间。",
            )
        except Exception as exc:
            return CommandResult(
                command=command,
                allowed=True,
                return_code=1,
                stdout="",
                stderr=f"{type(exc).__name__}: {exc}",
                reason="命令执行异常。",
            )

    def _truncate(self, text: str) -> str:
        if len(text) > self.max_output_chars:
            return text[: self.max_output_chars] + "\n[OUTPUT_TRUNCATED]"
        return text