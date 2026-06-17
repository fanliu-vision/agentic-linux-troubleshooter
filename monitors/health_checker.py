from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from monitors.project_registry import ProjectConfig


@dataclass
class HealthCheckResult:
    status: str
    message: str

    @property
    def ok(self) -> bool:
        return self.status == "ok"


class ProjectHealthChecker:
    """
    Stage 6E 周期性健康检查。

    V1 只做安全只读检查：
    - local: 检查项目目录和日志文件是否存在；
    - remote: 通过 ssh 执行 test -d / test -f；
    - 不执行 rm / kill / sudo / scancel 等危险命令。
    """

    def __init__(self, project: ProjectConfig) -> None:
        self.project = project

    def check(self) -> HealthCheckResult:
        if self.project.is_remote:
            return self._check_remote()

        return self._check_local()

    def _check_local(self) -> HealthCheckResult:
        project_dir = Path(self.project.effective_project_dir)

        if not project_dir.exists():
            return HealthCheckResult(
                status="warning",
                message=f"local project_dir does not exist: {project_dir}",
            )

        missing_logs: list[str] = []

        for log_file in self.project.log_files:
            path = Path(log_file)
            if not path.is_absolute():
                path = project_dir / path

            if not path.exists():
                missing_logs.append(str(path))

        if missing_logs:
            return HealthCheckResult(
                status="warning",
                message="missing local log files: " + ", ".join(missing_logs),
            )

        return HealthCheckResult(
            status="ok",
            message="local project_dir and log_files are available.",
        )

    def _check_remote(self) -> HealthCheckResult:
        ssh = self.project.ssh

        if not ssh.host or not ssh.user:
            return HealthCheckResult(
                status="warning",
                message="remote ssh user/host is not configured.",
            )

        paths_to_check: list[tuple[str, str]] = [
            ("project_dir", self.project.remote_project_dir),
        ]

        for log_file in self.project.log_files:
            paths_to_check.append(("log_file", log_file))

        remote_lines = []

        for kind, path in paths_to_check:
            if kind == "project_dir":
                remote_lines.append(
                    f'if test -d "{path}"; then echo "OK project_dir {path}"; '
                    f'else echo "MISSING project_dir {path}"; fi'
                )
            else:
                remote_lines.append(
                    f'if test -f "{path}"; then echo "OK log_file {path}"; '
                    f'else echo "MISSING log_file {path}"; fi'
                )

        command = " ; ".join(remote_lines)

        ssh_target = f"{ssh.user}@{ssh.host}"
        ssh_cmd = [
            "ssh",
            "-p",
            str(ssh.port),
            ssh_target,
            command,
        ]

        try:
            completed = subprocess.run(
                ssh_cmd,
                text=True,
                capture_output=True,
                timeout=10,
                check=False,
            )

            stdout = completed.stdout.strip()
            stderr = completed.stderr.strip()

            if completed.returncode != 0:
                return HealthCheckResult(
                    status="warning",
                    message=(
                        "remote health check command failed: "
                        f"return_code={completed.returncode}; stderr={stderr}"
                    ),
                )

            missing_lines = [
                line for line in stdout.splitlines()
                if line.startswith("MISSING ")
            ]

            if missing_lines:
                return HealthCheckResult(
                    status="warning",
                    message="; ".join(missing_lines),
                )

            return HealthCheckResult(
                status="ok",
                message=stdout or "remote project_dir and log_files are available.",
            )

        except FileNotFoundError:
            return HealthCheckResult(
                status="warning",
                message="ssh command not found, cannot run remote health check.",
            )
        except subprocess.TimeoutExpired:
            return HealthCheckResult(
                status="warning",
                message="remote health check timeout.",
            )
        except Exception as exc:
            return HealthCheckResult(
                status="warning",
                message=f"remote health check error: {type(exc).__name__}: {exc}",
            )