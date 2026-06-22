from __future__ import annotations

import difflib
import json
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class ConfigEditResult:
    success: bool
    message: str
    config_path: str
    backup_path: str = ""
    diff_path: str = ""
    old_value: Any = None
    new_value: Any = None
    field_path: str = ""
    no_op: bool = False
    semantic_status: str = ""
    semantic_reason: str = ""

    def to_markdown(self) -> str:
        lines = [
            "## 配置修改结果",
            f"- success: `{self.success}`",
            f"- message: {self.message}",
            f"- config_path: `{self.config_path}`",
            f"- field_path: `{self.field_path}`",
            f"- old_value: `{self.old_value}`",
            f"- new_value: `{self.new_value}`",
        ]

        if self.semantic_status:
            lines.append(f"- semantic_status: `{self.semantic_status}`")
        if self.semantic_reason:
            lines.append(f"- semantic_reason: `{self.semantic_reason}`")
        if self.no_op:
            lines.append("- no_op: `True`")

        if self.backup_path:
            lines.append(f"- backup_path: `{self.backup_path}`")

        if self.diff_path:
            lines.append(f"- diff_path: `{self.diff_path}`")

        return "\n".join(lines)


class SafeConfigEditor:
    """
    Safe JSON config editor.

    It only supports explicit field updates in JSON files.
    Every edit creates:
    - backup file
    - unified diff file
    """

    def __init__(self, project_dir: str, session_dir: str) -> None:
        self.project_dir = Path(project_dir).expanduser().resolve()
        self.session_dir = Path(session_dir).expanduser().resolve()

        self.backup_dir = self.session_dir / "backups"
        self.patch_dir = self.session_dir / "patches"
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self.patch_dir.mkdir(parents=True, exist_ok=True)

    def update_json_field(
        self,
        relative_config_path: str,
        field_path: str,
        new_value: Any,
        fix_id: str,
    ) -> ConfigEditResult:
        config_path = (self.project_dir / relative_config_path).resolve()

        if not self._is_inside_project(config_path):
            return ConfigEditResult(
                success=False,
                message="配置文件路径不在 project_dir 内，拒绝修改。",
                config_path=str(config_path),
                field_path=field_path,
                new_value=new_value,
            )

        if not config_path.exists():
            return ConfigEditResult(
                success=False,
                message="配置文件不存在。",
                config_path=str(config_path),
                field_path=field_path,
                new_value=new_value,
            )

        if config_path.suffix.lower() != ".json":
            return ConfigEditResult(
                success=False,
                message="当前 SafeConfigEditor 仅支持 .json 配置文件。",
                config_path=str(config_path),
                field_path=field_path,
                new_value=new_value,
            )

        try:
            original_text = config_path.read_text(encoding="utf-8")
            data = json.loads(original_text)
        except Exception as exc:
            return ConfigEditResult(
                success=False,
                message=f"读取或解析 JSON 失败：{type(exc).__name__}: {exc}",
                config_path=str(config_path),
                field_path=field_path,
                new_value=new_value,
            )

        try:
            old_value = self._get_nested_value(data, field_path)
        except KeyError:
            return ConfigEditResult(
                success=False,
                message=f"字段不存在：{field_path}",
                config_path=str(config_path),
                field_path=field_path,
                new_value=new_value,
            )

        if old_value == new_value:
            return ConfigEditResult(
                success=True,
                message="字段当前值已经等于目标值，无需修改。",
                config_path=str(config_path),
                field_path=field_path,
                old_value=old_value,
                new_value=new_value,
                no_op=True,
                semantic_status="no_op",
                semantic_reason="already_target_value",
            )

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = config_path.name.replace("/", "_")
        backup_path = self.backup_dir / f"{fix_id}_{safe_name}_{timestamp}.bak"
        diff_path = self.patch_dir / f"{fix_id}_{safe_name}_{timestamp}.diff"

        shutil.copy2(config_path, backup_path)

        self._set_nested_value(data, field_path, new_value)

        updated_text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"

        diff = difflib.unified_diff(
            original_text.splitlines(keepends=True),
            updated_text.splitlines(keepends=True),
            fromfile=str(config_path),
            tofile=f"{config_path} (updated by {fix_id})",
        )

        diff_text = "".join(diff)
        diff_path.write_text(diff_text, encoding="utf-8")
        config_path.write_text(updated_text, encoding="utf-8")

        return ConfigEditResult(
            success=True,
            message="配置字段已安全修改，并已生成备份和 diff。",
            config_path=str(config_path),
            backup_path=str(backup_path),
            diff_path=str(diff_path),
            old_value=old_value,
            new_value=new_value,
            field_path=field_path,
        )

    def rollback(self, backup_path: str, target_config_path: str) -> ConfigEditResult:
        backup = Path(backup_path).expanduser().resolve()
        target = Path(target_config_path).expanduser().resolve()

        if not backup.exists():
            return ConfigEditResult(
                success=False,
                message="备份文件不存在，无法回滚。",
                config_path=str(target),
                backup_path=str(backup),
            )

        if not self._is_inside_project(target):
            return ConfigEditResult(
                success=False,
                message="目标配置文件不在 project_dir 内，拒绝回滚。",
                config_path=str(target),
                backup_path=str(backup),
            )

        old_text = target.read_text(encoding="utf-8") if target.exists() else ""
        backup_text = backup.read_text(encoding="utf-8")

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        diff_path = self.patch_dir / f"rollback_{target.name}_{timestamp}.diff"

        diff = difflib.unified_diff(
            old_text.splitlines(keepends=True),
            backup_text.splitlines(keepends=True),
            fromfile=str(target),
            tofile=f"{target} (rollback)",
        )
        diff_path.write_text("".join(diff), encoding="utf-8")

        shutil.copy2(backup, target)

        return ConfigEditResult(
            success=True,
            message="已根据备份文件完成回滚。",
            config_path=str(target),
            backup_path=str(backup),
            diff_path=str(diff_path),
        )

    def _is_inside_project(self, path: Path) -> bool:
        try:
            path.relative_to(self.project_dir)
            return True
        except ValueError:
            return False

    @staticmethod
    def _get_nested_value(data: dict, field_path: str) -> Any:
        current = data
        parts = field_path.split(".")

        for part in parts:
            if not isinstance(current, dict) or part not in current:
                raise KeyError(field_path)
            current = current[part]

        return current

    @staticmethod
    def _set_nested_value(data: dict, field_path: str, new_value: Any) -> None:
        current = data
        parts = field_path.split(".")

        for part in parts[:-1]:
            if not isinstance(current, dict) or part not in current:
                raise KeyError(field_path)
            current = current[part]

        last = parts[-1]
        if not isinstance(current, dict) or last not in current:
            raise KeyError(field_path)

        current[last] = new_value
