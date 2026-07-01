from __future__ import annotations

import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import PurePosixPath


@dataclass
class RemoteSSHProfile:
    host: str
    user: str
    port: int = 22
    name: str = "default"

    @property
    def target(self) -> str:
        return f"{self.user}@{self.host}"

    def to_text(self) -> str:
        return (
            f"name={self.name}, "
            f"user={self.user}, "
            f"host={self.host}, "
            f"port={self.port}"
        )


@dataclass
class RemoteCommandResult:
    command: str
    profile: RemoteSSHProfile
    allowed: bool
    return_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    reason: str = ""

    def to_evidence_text(self) -> str:
        return (
            "[REMOTE_COMMAND_RESULT]\n"
            f"profile: {self.profile.to_text()}\n"
            f"command: {self.command}\n"
            f"allowed: {self.allowed}\n"
            f"return_code: {self.return_code}\n"
            f"reason: {self.reason}\n\n"
            "[STDOUT]\n"
            f"{self.stdout if self.stdout else '<empty>'}\n\n"
            "[STDERR]\n"
            f"{self.stderr if self.stderr else '<empty>'}"
        )


class RemoteReadonlySSHExecutor:
    """
    Read-only SSH command executor.

    It uses local OpenSSH client:
        ssh -p <port> user@host <command>

    It only allows read-only troubleshooting commands.
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
        r";",
        r"&&",
        r"\|\|",
    ]

    SAFE_COMMAND_PATTERNS = [
        r"^pwd$",
        r"^hostname$",
        r"^whoami$",
        r"^date$",
        r"^uptime$",
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
        r"^tail\s+-n\s+\d+\s+[\w./~:-]+$",
        r"^head\s+-n\s+\d+\s+[\w./~:-]+$",
        r"^ls\s+-lah\s+[\w./~:-]+$",
        r"^find\s+[\w./~:-]+\s+-maxdepth\s+\d+\s+-type\s+f.*$",
    ]

    def __init__(self, timeout: int = 25, max_output_chars: int = 20000) -> None:
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
                return True, "远程命令通过只读白名单检查。"

        return False, "远程命令不在只读白名单中。"

    def run(
        self,
        profile: RemoteSSHProfile,
        command: str,
        preserve_tail_on_truncate: bool = False,
    ) -> RemoteCommandResult:
        command = command.strip()
        allowed, reason = self.is_allowed(command)

        if not allowed:
            return RemoteCommandResult(
                command=command,
                profile=profile,
                allowed=False,
                reason=reason,
            )

        ssh_cmd = [
            "ssh",
            "-p",
            str(profile.port),
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=8",
            profile.target,
            command,
        ]

        try:
            completed = subprocess.run(
                ssh_cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )

            return RemoteCommandResult(
                command=command,
                profile=profile,
                allowed=True,
                return_code=completed.returncode,
                stdout=self._truncate(
                    completed.stdout.strip(),
                    preserve_tail_on_truncate=preserve_tail_on_truncate,
                ),
                stderr=self._truncate(completed.stderr.strip()),
                reason=reason,
            )

        except subprocess.TimeoutExpired:
            return RemoteCommandResult(
                command=command,
                profile=profile,
                allowed=True,
                return_code=124,
                stdout="",
                stderr="REMOTE_COMMAND_TIMEOUT",
                reason=f"远程命令超过 {self.timeout}s 超时时间。",
            )
        except Exception as exc:
            return RemoteCommandResult(
                command=command,
                profile=profile,
                allowed=True,
                return_code=1,
                stdout="",
                stderr=f"{type(exc).__name__}: {exc}",
                reason="远程命令执行异常。",
            )

    def read_remote_log_tail(
        self,
        profile: RemoteSSHProfile,
        remote_path: str,
        lines: int = 400,
    ) -> RemoteCommandResult:
        if not self._safe_remote_path(remote_path):
            return RemoteCommandResult(
                command=f"tail -n {lines} {remote_path}",
                profile=profile,
                allowed=False,
                reason="远程日志路径包含不安全字符。",
            )

        lines = max(20, min(lines, 2000))
        command = f"tail -n {lines} {remote_path}"
        return self.run(profile, command, preserve_tail_on_truncate=True)

    def stat_remote_log(
        self,
        profile: RemoteSSHProfile,
        remote_path: str,
    ) -> RemoteCommandResult:
        if not self._safe_remote_path(remote_path):
            return RemoteCommandResult(
                command=f"stat -Lc '%i %s %Y' {remote_path}",
                profile=profile,
                allowed=False,
                reason="远程日志路径包含不安全字符。",
            )

        quoted = shlex.quote(remote_path)
        command = f"stat -Lc '%i %s %Y' {quoted}"
        return self._run_fixed_readonly(
            profile=profile,
            command=command,
            display_command=f"stat -Lc '%i %s %Y' {remote_path}",
            reason="固定远程日志 stat 命令，只读执行。",
        )

    def read_remote_log_range(
        self,
        profile: RemoteSSHProfile,
        remote_path: str,
        offset: int,
        max_bytes: int,
    ) -> RemoteCommandResult:
        if not self._safe_remote_path(remote_path):
            return RemoteCommandResult(
                command=f"tail -c +{offset + 1} {remote_path} | head -c {max_bytes}",
                profile=profile,
                allowed=False,
                reason="远程日志路径包含不安全字符。",
            )

        if offset < 0:
            return RemoteCommandResult(
                command=f"remote-log-range {remote_path}",
                profile=profile,
                allowed=False,
                reason="远程日志 offset 不能为负数。",
            )

        if max_bytes <= 0:
            return RemoteCommandResult(
                command=f"remote-log-range {remote_path}",
                profile=profile,
                allowed=False,
                reason="远程日志 max_bytes 必须大于 0。",
            )

        quoted = shlex.quote(remote_path)
        start_byte = offset + 1
        command = f"tail -c +{start_byte} {quoted} | head -c {max_bytes}"

        return self._run_fixed_readonly_raw(
            profile=profile,
            command=command,
            display_command=f"tail -c +{start_byte} {remote_path} | head -c {max_bytes}",
            reason="固定远程日志 byte range 读取命令，只读执行。",
        )

    def collect_remote_project_context(
        self,
        profile: RemoteSSHProfile,
        remote_project_dir: str,
    ) -> RemoteCommandResult:
        """
        Collect remote project context using fixed read-only commands.

        It lists common project files and config candidates.
        """
        if not self._safe_remote_path(remote_project_dir):
            return RemoteCommandResult(
                command=f"remote-context {remote_project_dir}",
                profile=profile,
                allowed=False,
                reason="远程项目路径包含不安全字符。",
            )

        quoted = shlex.quote(remote_project_dir)

        command = (
            f"find {quoted} -maxdepth 3 -type f "
            r"\( -name 'config.json' -o -name '*.yaml' -o -name '*.yml' "
            r"-o -name '*.toml' -o -name 'requirements.txt' "
            r"-o -name 'pyproject.toml' -o -name '*.log' -o -name '*.err' -o -name '*.out' \)"
        )

        # 这个 command 会包含括号和引号，不适合通用 allowlist；
        # 因此通过专用 SSH 执行方法，不暴露给 /remote-run 任意调用。
        return self._run_fixed_readonly(profile, command, display_command=f"remote-context {remote_project_dir}")

    def run_remote_project(
        self,
        profile: RemoteSSHProfile,
        remote_project_dir: str,
        run_command: str,
    ) -> RemoteCommandResult:
        """
        Run a user-provided project command on a remote server.

        This is for Stage 5B remote rerun.
        It is not a general remote shell executor.

        Safety policy:
        - remote_project_dir must be a safe path;
        - run_command must pass a restricted rerun safety check;
        - no rm / sudo / kill / scancel / chmod / chown / redirection / pipe;
        - command is executed under `cd <remote_project_dir> && <run_command>`.
        """
        remote_project_dir = remote_project_dir.strip()
        run_command = run_command.strip()

        if not self._safe_remote_path(remote_project_dir):
            return RemoteCommandResult(
                command=f"remote-rerun {remote_project_dir} {run_command}",
                profile=profile,
                allowed=False,
                reason="远程项目路径包含不安全字符。",
            )

        allowed, reason = self.is_safe_rerun_command(run_command)
        if not allowed:
            return RemoteCommandResult(
                command=f"remote-rerun {remote_project_dir} {run_command}",
                profile=profile,
                allowed=False,
                reason=reason,
            )

        quoted_dir = shlex.quote(remote_project_dir)
        command = f"cd {quoted_dir} && {run_command}"

        return self._run_fixed_readonly(
            profile=profile,
            command=command,
            display_command=f"remote-rerun cd {remote_project_dir} && {run_command}",
        )

    def is_safe_rerun_command(self, command: str) -> tuple[bool, str]:
        """
        Safety check for remote project rerun commands.

        This is less strict than readonly command allowlist because rerun may execute
        a project scripts, but it still blocks dangerous shell operations.
        """
        command = command.strip()

        if not command:
            return False, "远程 rerun 命令为空。"

        dangerous = [
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
            r"\bsystemctl\b",
            r">\s*",
            r">>\s*",
            r"\|\s*sh\b",
            r"\|\s*bash\b",
            r"`",
            r"\$\(",
            r";",
            r"&&",
            r"\|\|",
            r"\|",
        ]

        for pattern in dangerous:
            if re.search(pattern, command):
                return False, f"远程 rerun 命令包含危险模式，已拒绝：{pattern}"

        safe_patterns = [
            r"^python\s+[\w./-]+(\s+--[\w-]+\s+[\w./-]+)*$",
            r"^python3\s+[\w./-]+(\s+--[\w-]+\s+[\w./-]+)*$",
            r"^bash\s+[\w./-]+$",
            r"^sh\s+[\w./-]+$",
        ]

        for pattern in safe_patterns:
            if re.fullmatch(pattern, command):
                return True, "远程 rerun 命令通过受控运行检查。"

        return False, (
            "远程 rerun 命令不在允许模式中。"
            "当前仅允许 python/python3/bash/sh 运行项目脚本，且不能包含管道、重定向、&&、sudo、rm 等危险操作。"
        )

    def _run_fixed_readonly(
        self,
        profile: RemoteSSHProfile,
        command: str,
        display_command: str,
        reason: str = "固定远程项目上下文扫描命令，只读执行。",
    ) -> RemoteCommandResult:
        ssh_cmd = [
            "ssh",
            "-p",
            str(profile.port),
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=8",
            profile.target,
            command,
        ]

        try:
            completed = subprocess.run(
                ssh_cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )

            return RemoteCommandResult(
                command=display_command,
                profile=profile,
                allowed=True,
                return_code=completed.returncode,
                stdout=self._truncate(completed.stdout.strip()),
                stderr=self._truncate(completed.stderr.strip()),
                reason=reason,
            )

        except subprocess.TimeoutExpired:
            return RemoteCommandResult(
                command=display_command,
                profile=profile,
                allowed=True,
                return_code=124,
                stdout="",
                stderr="REMOTE_CONTEXT_TIMEOUT",
                reason=f"远程上下文扫描超过 {self.timeout}s 超时时间。",
            )

    def _run_fixed_readonly_raw(
        self,
        profile: RemoteSSHProfile,
        command: str,
        display_command: str,
        reason: str,
    ) -> RemoteCommandResult:
        ssh_cmd = [
            "ssh",
            "-p",
            str(profile.port),
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=8",
            profile.target,
            command,
        ]

        try:
            completed = subprocess.run(
                ssh_cmd,
                capture_output=True,
                text=False,
                timeout=self.timeout,
                check=False,
            )

            return RemoteCommandResult(
                command=display_command,
                profile=profile,
                allowed=True,
                return_code=completed.returncode,
                stdout=self._decode_process_output(completed.stdout),
                stderr=self._truncate(self._decode_process_output(completed.stderr).strip()),
                reason=reason,
            )

        except subprocess.TimeoutExpired:
            return RemoteCommandResult(
                command=display_command,
                profile=profile,
                allowed=True,
                return_code=124,
                stdout="",
                stderr="REMOTE_LOG_RANGE_TIMEOUT",
                reason=f"远程日志 byte range 读取超过 {self.timeout}s 超时时间。",
            )

    def _safe_remote_path(self, path: str) -> bool:
        if not path or len(path) > 300:
            return False

        forbidden = [";", "&&", "||", "`", "$(", ">", "<", "|"]
        if any(x in path for x in forbidden):
            return False

        return True

    @staticmethod
    def _decode_process_output(value: bytes | str | None) -> str:
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="ignore")
        return value

    def _truncate(self, text: str, preserve_tail_on_truncate: bool = False) -> str:
        if len(text) > self.max_output_chars:
            if preserve_tail_on_truncate:
                omitted = len(text) - self.max_output_chars
                return (
                    f"[REMOTE_OUTPUT_TRUNCATED_KEEP_TAIL omitted {omitted} chars]\n"
                    f"{text[-self.max_output_chars:]}"
                )
            return text[: self.max_output_chars] + "\n[REMOTE_OUTPUT_TRUNCATED]"
        return text
