from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional


@dataclass
class ProjectRunResult:
    command: str
    project_dir: str
    return_code: int
    stdout: str
    stderr: str
    log_path: str
    timed_out: bool = False

    @property
    def success(self) -> bool:
        return self.return_code == 0 and not self.timed_out

    def to_evidence_text(self) -> str:
        return (
            "[PROJECT_RERUN_RESULT]\n"
            f"command: {self.command}\n"
            f"project_dir: {self.project_dir}\n"
            f"return_code: {self.return_code}\n"
            f"success: {self.success}\n"
            f"timed_out: {self.timed_out}\n"
            f"log_path: {self.log_path}\n\n"
            "[STDOUT]\n"
            f"{self.stdout if self.stdout else '<empty>'}\n\n"
            "[STDERR]\n"
            f"{self.stderr if self.stderr else '<empty>'}"
        )


class ProjectRunner:
    """
    Run a user-provided project command under a project directory.

    This is used by Stage 4C rerun loop.
    It should be used only after user confirmation.
    """

    DANGEROUS_PATTERNS = [
        "rm ",
        "rm -rf",
        "sudo ",
        "kill ",
        "kill -9",
        "pkill",
        "scancel",
        "chmod ",
        "chown ",
        "mkfs",
        "dd ",
        "reboot",
        "shutdown",
        "systemctl restart",
    ]

    def __init__(
        self,
        project_dir: str,
        run_command: str,
        output_dir: str,
        timeout: int = 120,
        max_output_chars: int = 30000,
        extra_env: Optional[Dict[str, str]] = None,
    ) -> None:
        self.project_dir = Path(project_dir).expanduser().resolve()
        self.run_command = run_command.strip()
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self.max_output_chars = max_output_chars
        self.extra_env = extra_env or {}

    def validate(self) -> tuple[bool, str]:
        if not self.project_dir.exists():
            return False, f"项目目录不存在：{self.project_dir}"

        if not self.project_dir.is_dir():
            return False, f"project_dir 不是目录：{self.project_dir}"

        if not self.run_command:
            return False, "运行命令为空。"

        lower = self.run_command.lower()
        for pattern in self.DANGEROUS_PATTERNS:
            if pattern in lower:
                return False, f"运行命令包含危险片段，已拒绝：{pattern}"

        return True, "项目运行命令通过基础安全检查。"

    def run(self) -> ProjectRunResult:
        ok, reason = self.validate()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_path = self.output_dir / f"rerun_{timestamp}.log"

        if not ok:
            result = ProjectRunResult(
                command=self.run_command,
                project_dir=str(self.project_dir),
                return_code=126,
                stdout="",
                stderr=reason,
                log_path=str(log_path),
            )
            log_path.write_text(result.to_evidence_text(), encoding="utf-8")
            return result

        env = os.environ.copy()
        env.update(self.extra_env)

        try:
            completed = subprocess.run(
                self.run_command,
                shell=True,
                cwd=str(self.project_dir),
                env=env,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )

            stdout = self._truncate(completed.stdout.strip())
            stderr = self._truncate(completed.stderr.strip())

            result = ProjectRunResult(
                command=self.run_command,
                project_dir=str(self.project_dir),
                return_code=completed.returncode,
                stdout=stdout,
                stderr=stderr,
                log_path=str(log_path),
                timed_out=False,
            )

        except subprocess.TimeoutExpired as exc:
            stdout = self._truncate((exc.stdout or "").strip() if isinstance(exc.stdout, str) else "")
            stderr = self._truncate((exc.stderr or "").strip() if isinstance(exc.stderr, str) else "")

            result = ProjectRunResult(
                command=self.run_command,
                project_dir=str(self.project_dir),
                return_code=124,
                stdout=stdout,
                stderr=stderr + "\nCOMMAND_TIMEOUT",
                log_path=str(log_path),
                timed_out=True,
            )

        log_path.write_text(result.to_evidence_text(), encoding="utf-8")
        return result

    def _truncate(self, text: str) -> str:
        if len(text) > self.max_output_chars:
            return text[: self.max_output_chars] + "\n[OUTPUT_TRUNCATED]"
        return text