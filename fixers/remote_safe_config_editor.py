from __future__ import annotations

import base64
import json
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tools.remote_ssh_executor import RemoteSSHProfile


@dataclass
class RemoteConfigEditResult:
    success: bool
    message: str
    profile: RemoteSSHProfile
    remote_project_dir: str
    config_path: str = ""
    backup_path: str = ""
    diff_path: str = ""
    field_path: str = ""
    old_value: Any = None
    new_value: Any = None
    stdout: str = ""
    stderr: str = ""
    return_code: int | None = None

    def to_markdown(self) -> str:
        lines = [
            "## 远程配置修改结果",
            f"- success: `{self.success}`",
            f"- message: {self.message}",
            f"- remote: `{self.profile.target}`",
            f"- remote_project_dir: `{self.remote_project_dir}`",
            f"- config_path: `{self.config_path}`",
            f"- field_path: `{self.field_path}`",
            f"- old_value: `{self.old_value}`",
            f"- new_value: `{self.new_value}`",
            f"- return_code: `{self.return_code}`",
        ]

        if self.backup_path:
            lines.append(f"- backup_path: `{self.backup_path}`")

        if self.diff_path:
            lines.append(f"- diff_path: `{self.diff_path}`")

        if self.stderr:
            lines.append("")
            lines.append("### STDERR")
            lines.append("```text")
            lines.append(self.stderr)
            lines.append("```")

        return "\n".join(lines)


class RemoteSafeConfigEditor:
    """
    Remote JSON config editor over SSH.

    It modifies only explicit JSON fields under remote_project_dir.
    Every successful edit creates:
    - .agent_backups/*.bak
    - .agent_patches/*.diff
    """

    def __init__(
        self,
        profile: RemoteSSHProfile,
        timeout: int = 30,
        max_output_chars: int = 20000,
    ) -> None:
        self.profile = profile
        self.timeout = timeout
        self.max_output_chars = max_output_chars

    def update_json_field(
        self,
        remote_project_dir: str,
        relative_config_path: str,
        field_path: str,
        new_value: Any,
        fix_id: str,
    ) -> RemoteConfigEditResult:
        payload = {
            "op": "update_json_field",
            "remote_project_dir": remote_project_dir,
            "relative_config_path": relative_config_path,
            "field_path": field_path,
            "new_value": new_value,
            "fix_id": fix_id,
        }

        return self._run_remote_editor(payload)

    def rollback(
        self,
        remote_project_dir: str,
        backup_path: str,
        target_config_path: str,
    ) -> RemoteConfigEditResult:
        payload = {
            "op": "rollback",
            "remote_project_dir": remote_project_dir,
            "backup_path": backup_path,
            "target_config_path": target_config_path,
            "fix_id": "rollback",
        }

        return self._run_remote_editor(payload)

    def read_remote_file(self, remote_path: str) -> tuple[bool, str]:
        if not self._safe_remote_path(remote_path):
            return False, "远程路径包含不安全字符。"

        command = f"cat {shlex.quote(remote_path)}"

        result = subprocess.run(
            [
                "ssh",
                "-p",
                str(self.profile.port),
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=8",
                self.profile.target,
                command,
            ],
            capture_output=True,
            text=True,
            timeout=self.timeout,
            check=False,
        )

        if result.returncode != 0:
            return False, result.stderr.strip()

        return True, self._truncate(result.stdout)

    def _run_remote_editor(self, payload: dict[str, Any]) -> RemoteConfigEditResult:
        payload_b64 = base64.b64encode(
            json.dumps(payload, ensure_ascii=False).encode("utf-8")
        ).decode("ascii")

        script = self._remote_editor_script()

        command = f"python3 -c {shlex.quote(script)} {shlex.quote(payload_b64)}"

        completed = subprocess.run(
            [
                "ssh",
                "-p",
                str(self.profile.port),
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=8",
                self.profile.target,
                command,
            ],
            capture_output=True,
            text=True,
            timeout=self.timeout,
            check=False,
        )

        stdout = self._truncate(completed.stdout.strip())
        stderr = self._truncate(completed.stderr.strip())

        try:
            data = json.loads(stdout) if stdout else {}
        except Exception:
            data = {}

        return RemoteConfigEditResult(
            success=bool(data.get("success", False)),
            message=str(data.get("message", "远程配置编辑失败。")),
            profile=self.profile,
            remote_project_dir=str(payload.get("remote_project_dir", "")),
            config_path=str(data.get("config_path", data.get("target_config_path", ""))),
            backup_path=str(data.get("backup_path", "")),
            diff_path=str(data.get("diff_path", "")),
            field_path=str(data.get("field_path", payload.get("field_path", ""))),
            old_value=data.get("old_value"),
            new_value=data.get("new_value", payload.get("new_value")),
            stdout=stdout,
            stderr=stderr,
            return_code=completed.returncode,
        )

    @staticmethod
    def _remote_editor_script() -> str:
        return r'''
import base64
import difflib
import json
import shutil
import sys
import time
from pathlib import Path


def result(**kwargs):
    print(json.dumps(kwargs, ensure_ascii=False))


def ensure_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def get_nested(data, field_path):
    cur = data
    for part in field_path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            raise KeyError(field_path)
        cur = cur[part]
    return cur


def set_nested(data, field_path, value):
    cur = data
    parts = field_path.split(".")
    for part in parts[:-1]:
        if not isinstance(cur, dict) or part not in cur:
            raise KeyError(field_path)
        cur = cur[part]
    last = parts[-1]
    if not isinstance(cur, dict) or last not in cur:
        raise KeyError(field_path)
    cur[last] = value


def main():
    payload = json.loads(base64.b64decode(sys.argv[1]).decode("utf-8"))
    op = payload.get("op")
    project_dir = Path(payload.get("remote_project_dir", "")).expanduser().resolve()

    if not project_dir.exists() or not project_dir.is_dir():
        result(success=False, message=f"远程项目目录不存在：{project_dir}")
        return

    if op == "update_json_field":
        rel = payload.get("relative_config_path", "")
        field_path = payload.get("field_path", "")
        new_value = payload.get("new_value")
        fix_id = payload.get("fix_id", "fix")

        config_path = (project_dir / rel).resolve()

        if not ensure_inside(config_path, project_dir):
            result(success=False, message="配置文件不在远程项目目录内。", config_path=str(config_path))
            return

        if not config_path.exists():
            result(success=False, message="配置文件不存在。", config_path=str(config_path))
            return

        if config_path.suffix.lower() != ".json":
            result(success=False, message="当前远程安全编辑仅支持 JSON。", config_path=str(config_path))
            return

        original_text = config_path.read_text(encoding="utf-8")
        data = json.loads(original_text)

        try:
            old_value = get_nested(data, field_path)
        except KeyError:
            result(success=False, message=f"字段不存在：{field_path}", config_path=str(config_path))
            return

        if old_value == new_value:
            result(
                success=True,
                message="字段当前值已经等于目标值，无需修改。",
                config_path=str(config_path),
                field_path=field_path,
                old_value=old_value,
                new_value=new_value,
            )
            return

        backup_dir = project_dir / ".agent_backups"
        patch_dir = project_dir / ".agent_patches"
        backup_dir.mkdir(parents=True, exist_ok=True)
        patch_dir.mkdir(parents=True, exist_ok=True)

        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_path = backup_dir / f"{fix_id}_{config_path.name}_{ts}.bak"
        diff_path = patch_dir / f"{fix_id}_{config_path.name}_{ts}.diff"

        shutil.copy2(config_path, backup_path)

        set_nested(data, field_path, new_value)
        updated_text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"

        diff_text = "".join(
            difflib.unified_diff(
                original_text.splitlines(keepends=True),
                updated_text.splitlines(keepends=True),
                fromfile=str(config_path),
                tofile=f"{config_path} updated by {fix_id}",
            )
        )

        diff_path.write_text(diff_text, encoding="utf-8")
        config_path.write_text(updated_text, encoding="utf-8")

        result(
            success=True,
            message="远程配置字段已修改，并已生成备份和 diff。",
            config_path=str(config_path),
            backup_path=str(backup_path),
            diff_path=str(diff_path),
            field_path=field_path,
            old_value=old_value,
            new_value=new_value,
        )
        return

    if op == "rollback":
        backup_path = Path(payload.get("backup_path", "")).expanduser().resolve()
        target_config_path = Path(payload.get("target_config_path", "")).expanduser().resolve()

        if not ensure_inside(target_config_path, project_dir):
            result(success=False, message="目标配置文件不在远程项目目录内。", target_config_path=str(target_config_path))
            return

        if not backup_path.exists():
            result(success=False, message="备份文件不存在。", backup_path=str(backup_path))
            return

        patch_dir = project_dir / ".agent_patches"
        patch_dir.mkdir(parents=True, exist_ok=True)

        old_text = target_config_path.read_text(encoding="utf-8") if target_config_path.exists() else ""
        backup_text = backup_path.read_text(encoding="utf-8")

        ts = time.strftime("%Y%m%d_%H%M%S")
        diff_path = patch_dir / f"rollback_{target_config_path.name}_{ts}.diff"

        diff_text = "".join(
            difflib.unified_diff(
                old_text.splitlines(keepends=True),
                backup_text.splitlines(keepends=True),
                fromfile=str(target_config_path),
                tofile=f"{target_config_path} rollback",
            )
        )

        diff_path.write_text(diff_text, encoding="utf-8")
        shutil.copy2(backup_path, target_config_path)

        result(
            success=True,
            message="远程配置已回滚。",
            target_config_path=str(target_config_path),
            backup_path=str(backup_path),
            diff_path=str(diff_path),
        )
        return

    result(success=False, message=f"未知操作：{op}")


main()
'''

    @staticmethod
    def _safe_remote_path(path: str) -> bool:
        if not path or len(path) > 400:
            return False

        forbidden = [";", "&&", "||", "`", "$(", ">", "<", "|"]
        return not any(x in path for x in forbidden)

    def _truncate(self, text: str) -> str:
        if len(text) > self.max_output_chars:
            return text[: self.max_output_chars] + "\n[REMOTE_OUTPUT_TRUNCATED]"
        return text