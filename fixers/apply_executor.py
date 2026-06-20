from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fixers.safe_config_editor import ConfigEditResult, SafeConfigEditor
from safe_recovery.registry import (
    SAFE_RECOVERY_FIX_IDS,
    SafeRecoverySpec,
    get_safe_recovery_spec_by_fix_id,
)


@dataclass
class ApplyResult:
    success: bool
    fix_id: str
    message: str
    edit_results: list[ConfigEditResult]
    applied_record_path: str = ""

    def to_markdown(self) -> str:
        lines = [
            "## Apply 结果",
            f"- fix_id: `{self.fix_id}`",
            f"- success: `{self.success}`",
            f"- message: {self.message}",
        ]

        if self.applied_record_path:
            lines.append(f"- applied_record_path: `{self.applied_record_path}`")

        lines.append("")
        lines.append("### 配置修改明细")
        for item in self.edit_results:
            lines.append(item.to_markdown())
            lines.append("")

        return "\n".join(lines)


class SafeApplyExecutor:
    """
    Apply selected fix actions in a controlled and reversible way.

    Stage 4D first supports JSON config edits for demo projects.
    """

    def __init__(self, project_dir: str, session_dir: str) -> None:
        self.project_dir = Path(project_dir).expanduser().resolve()
        self.session_dir = Path(session_dir).expanduser().resolve()
        self.editor = SafeConfigEditor(project_dir=str(self.project_dir), session_dir=str(self.session_dir))
        self.applied_record_path = self.session_dir / "applied_fixes.json"

    def apply(self, fix_id: str) -> ApplyResult:
        fix_id = fix_id.strip()

        if not fix_id:
            return ApplyResult(
                success=False,
                fix_id=fix_id,
                message="fix_id 为空。",
                edit_results=[],
            )

        safe_spec = get_safe_recovery_spec_by_fix_id(fix_id)
        if safe_spec is not None:
            return self._apply_safe_registry_fix(safe_spec)

        if fix_id == "fix-gpu-2":
            results = [
                self.editor.update_json_field(
                    relative_config_path="config.json",
                    field_path="precision",
                    new_value="bf16",
                    fix_id=fix_id,
                ),
                self.editor.update_json_field(
                    relative_config_path="config.json",
                    field_path="gradient_checkpointing",
                    new_value=True,
                    fix_id=fix_id,
                ),
            ]
            return self._finalize(fix_id, results, "已尝试应用 GPU 显存优化：bf16 + gradient_checkpointing。")

        if fix_id == "fix-disk-1":
            # 企业 demo 中用 simulate_disk_full 表示缓存写入失败模拟开关
            results = [
                self.editor.update_json_field(
                    relative_config_path="config.json",
                    field_path="simulate_disk_full",
                    new_value=False,
                    fix_id=fix_id,
                )
            ]
            return self._finalize(fix_id, results, "已尝试应用缓存写入修复：关闭 simulate_disk_full。")

        if fix_id == "fix-python-1":
            # 企业 demo 中用 simulate_python_env_mismatch 表示 Python 环境告警模拟开关
            results = [
                self.editor.update_json_field(
                    relative_config_path="config.json",
                    field_path="simulate_python_env_mismatch",
                    new_value=False,
                    fix_id=fix_id,
                )
            ]
            return self._finalize(fix_id, results, "已尝试应用 Python 环境告警修复：关闭 simulate_python_env_mismatch。")

        return ApplyResult(
            success=False,
            fix_id=fix_id,
            message=(
                "当前 fix_id 暂不支持自动 apply。"
                "该修复可能需要用户手动处理，或尚未在 SafeApplyExecutor 中注册。"
            ),
            edit_results=[],
        )

    def rollback_latest(self) -> ApplyResult:
        records = self._load_records()

        if not records:
            return ApplyResult(
                success=False,
                fix_id="rollback",
                message="没有可回滚的 apply 记录。",
                edit_results=[],
            )

        latest = records[-1]
        edit_results = []

        for edit in latest.get("edits", []):
            backup_path = edit.get("backup_path", "")
            config_path = edit.get("config_path", "")

            if backup_path and config_path:
                edit_results.append(
                    self.editor.rollback(
                        backup_path=backup_path,
                        target_config_path=config_path,
                    )
                )

        success = bool(edit_results) and all(item.success for item in edit_results)

        if success:
            records.pop()
            self._save_records(records)

        return ApplyResult(
            success=success,
            fix_id=f"rollback:{latest.get('fix_id', '<unknown>')}",
            message="已回滚最近一次 apply。" if success else "回滚失败。",
            edit_results=edit_results,
            applied_record_path=str(self.applied_record_path),
        )

    @staticmethod
    def supported_safe_fix_ids() -> set[str]:
        return set(SAFE_RECOVERY_FIX_IDS)

    def _apply_safe_registry_fix(self, spec: SafeRecoverySpec) -> ApplyResult:
        candidates = [
            (candidate.field_path, candidate.new_value)
            for candidate in spec.candidates
        ]
        results = self._update_first_existing_json_field(
            fix_id=spec.fix_id,
            relative_config_path=spec.relative_config_path,
            candidates=candidates,
        )
        return self._finalize(
            spec.fix_id,
            results,
            spec.local_success_message,
        )

    def _update_first_existing_json_field(
        self,
        *,
        fix_id: str,
        candidates: list[tuple[str, Any]],
        relative_config_path: str = "config.json",
    ) -> list[ConfigEditResult]:
        results: list[ConfigEditResult] = []

        for field_path, new_value in candidates:
            result = self.editor.update_json_field(
                relative_config_path=relative_config_path,
                field_path=field_path,
                new_value=new_value,
                fix_id=fix_id,
            )
            results.append(result)

            if result.success:
                return [result]

        return results

    def _finalize(self, fix_id: str, results: list[ConfigEditResult], message: str) -> ApplyResult:
        success = bool(results) and all(item.success for item in results)

        if success:
            self._append_record(fix_id, results)

        return ApplyResult(
            success=success,
            fix_id=fix_id,
            message=message if success else f"{message} 但部分修改失败。",
            edit_results=results,
            applied_record_path=str(self.applied_record_path),
        )

    def _append_record(self, fix_id: str, results: list[ConfigEditResult]) -> None:
        records = self._load_records()

        records.append(
            {
                "fix_id": fix_id,
                "edits": [
                    {
                        "config_path": item.config_path,
                        "backup_path": item.backup_path,
                        "diff_path": item.diff_path,
                        "field_path": item.field_path,
                        "old_value": item.old_value,
                        "new_value": item.new_value,
                    }
                    for item in results
                    if item.success
                ],
            }
        )

        self._save_records(records)

    def _load_records(self) -> list[dict[str, Any]]:
        if not self.applied_record_path.exists():
            return []

        try:
            return json.loads(self.applied_record_path.read_text(encoding="utf-8"))
        except Exception:
            return []

    def _save_records(self, records: list[dict[str, Any]]) -> None:
        self.applied_record_path.write_text(
            json.dumps(records, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
