from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fixers.remote_safe_config_editor import (
    RemoteConfigEditResult,
    RemoteSafeConfigEditor,
)
from tools.remote_ssh_executor import RemoteSSHProfile


@dataclass
class RemoteApplyResult:
    success: bool
    fix_id: str
    message: str
    edit_results: list[RemoteConfigEditResult]
    record_path: str = ""

    def to_markdown(self) -> str:
        lines = [
            "## 远程 Apply 结果",
            f"- fix_id: `{self.fix_id}`",
            f"- success: `{self.success}`",
            f"- message: {self.message}",
        ]

        if self.record_path:
            lines.append(f"- record_path: `{self.record_path}`")

        lines.append("")
        lines.append("### 远程配置修改明细")

        if not self.edit_results:
            lines.append("- <empty>")

        for item in self.edit_results:
            lines.append(item.to_markdown())
            lines.append("")

        return "\n".join(lines)


class RemoteSafeApplyExecutor:
    """
    Controlled remote apply executor.

    It modifies only registered JSON config fields under remote_project_dir.
    """

    def __init__(
        self,
        profile: RemoteSSHProfile,
        session_dir: str,
        timeout: int = 30,
    ) -> None:
        self.profile = profile
        self.session_dir = Path(session_dir)
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.editor = RemoteSafeConfigEditor(profile=profile, timeout=timeout)
        self.record_path = self.session_dir / "remote_applied_fixes.json"

    def apply(self, fix_id: str, remote_project_dir: str) -> RemoteApplyResult:
        fix_id = fix_id.strip()
        remote_project_dir = remote_project_dir.strip()

        if not fix_id:
            return RemoteApplyResult(
                success=False,
                fix_id=fix_id,
                message="fix_id 为空。",
                edit_results=[],
            )

        if not remote_project_dir:
            return RemoteApplyResult(
                success=False,
                fix_id=fix_id,
                message="remote_project_dir 为空。",
                edit_results=[],
            )

        if fix_id == "fix-network-1":
            results = [
                self.editor.update_json_field(
                    remote_project_dir=remote_project_dir,
                    relative_config_path="config.json",
                    field_path="metrics_port",
                    new_value=9101,
                    fix_id=fix_id,
                )
            ]
            return self._finalize(
                fix_id,
                remote_project_dir,
                results,
                "已远程应用端口冲突修复：metrics_port 改为 9101。",
            )

        if fix_id == "fix-gpu-1":
            candidate_fields = [
                "batch_size",
                "train_batch_size",
                "per_device_train_batch_size",
                "samples_per_gpu",
                "training.batch_size",
                "model.batch_size",
            ]

            results: list[RemoteConfigEditResult] = []

            for field_path in candidate_fields:
                result = self.editor.update_json_field(
                    remote_project_dir=remote_project_dir,
                    relative_config_path="config.json",
                    field_path=field_path,
                    new_value=4,
                    fix_id=fix_id,
                )
                results.append(result)

                if result.success:
                    return self._finalize(
                        fix_id,
                        remote_project_dir,
                        [result],
                        f"已远程应用 GPU OOM 修复：{field_path} 改为 4。",
                    )

            return RemoteApplyResult(
                success=False,
                fix_id=fix_id,
                message=(
                    "远程 GPU OOM 修复失败：未找到可修改的 batch size 字段。"
                    "已检查 batch_size、train_batch_size、per_device_train_batch_size、"
                    "samples_per_gpu、training.batch_size、model.batch_size。"
                ),
                edit_results=results,
                record_path=str(self.record_path),
            )

        if fix_id == "fix-gpu-2":
            results = [
                self.editor.update_json_field(
                    remote_project_dir=remote_project_dir,
                    relative_config_path="config.json",
                    field_path="precision",
                    new_value="bf16",
                    fix_id=fix_id,
                ),
                self.editor.update_json_field(
                    remote_project_dir=remote_project_dir,
                    relative_config_path="config.json",
                    field_path="gradient_checkpointing",
                    new_value=True,
                    fix_id=fix_id,
                ),
            ]
            return self._finalize(
                fix_id,
                remote_project_dir,
                results,
                "已远程应用 GPU 显存优化修复：bf16 + gradient_checkpointing。",
            )

        if fix_id == "fix-disk-1":
            results = [
                self.editor.update_json_field(
                    remote_project_dir=remote_project_dir,
                    relative_config_path="config.json",
                    field_path="simulate_disk_full",
                    new_value=False,
                    fix_id=fix_id,
                )
            ]
            return self._finalize(
                fix_id,
                remote_project_dir,
                results,
                "已远程应用磁盘缓存修复：simulate_disk_full 改为 false。",
            )

        if fix_id == "fix-python-1":
            results = [
                self.editor.update_json_field(
                    remote_project_dir=remote_project_dir,
                    relative_config_path="config.json",
                    field_path="simulate_python_env_mismatch",
                    new_value=False,
                    fix_id=fix_id,
                )
            ]
            return self._finalize(
                fix_id,
                remote_project_dir,
                results,
                "已远程应用 Python 环境告警修复：simulate_python_env_mismatch 改为 false。",
            )

        return RemoteApplyResult(
            success=False,
            fix_id=fix_id,
            message="当前 fix_id 暂不支持远程 apply。",
            edit_results=[],
            record_path=str(self.record_path),
        )

    def rollback_latest(self) -> RemoteApplyResult:
        records = self._load_records()

        if not records:
            return RemoteApplyResult(
                success=False,
                fix_id="remote-rollback",
                message="没有可回滚的远程 apply 记录。",
                edit_results=[],
                record_path=str(self.record_path),
            )

        latest = records[-1]
        remote_project_dir = latest.get("remote_project_dir", "")
        edit_results: list[RemoteConfigEditResult] = []

        for edit in latest.get("edits", []):
            backup_path = edit.get("backup_path", "")
            config_path = edit.get("config_path", "")

            if backup_path and config_path:
                edit_results.append(
                    self.editor.rollback(
                        remote_project_dir=remote_project_dir,
                        backup_path=backup_path,
                        target_config_path=config_path,
                    )
                )

        success = bool(edit_results) and all(item.success for item in edit_results)

        if success:
            records.pop()
            self._save_records(records)

        return RemoteApplyResult(
            success=success,
            fix_id=f"remote-rollback:{latest.get('fix_id', '<unknown>')}",
            message="已回滚最近一次远程 apply。" if success else "远程 rollback 失败。",
            edit_results=edit_results,
            record_path=str(self.record_path),
        )

    def read_latest_diff(self) -> tuple[bool, str, str]:
        records = self._load_records()

        if not records:
            return False, "", "没有远程 apply 记录。"

        latest = records[-1]
        edits = latest.get("edits", [])
        if not edits:
            return False, "", "最近一次远程 apply 没有 edit 记录。"

        diff_path = edits[-1].get("diff_path", "")
        if not diff_path:
            return False, "", "最近一次远程 apply 没有 diff_path。"

        ok, text = self.editor.read_remote_file(diff_path)
        return ok, diff_path, text

    def _finalize(
        self,
        fix_id: str,
        remote_project_dir: str,
        results: list[RemoteConfigEditResult],
        message: str,
    ) -> RemoteApplyResult:
        success = bool(results) and all(item.success for item in results)

        if success:
            self._append_record(fix_id, remote_project_dir, results)

        return RemoteApplyResult(
            success=success,
            fix_id=fix_id,
            message=message if success else f"{message} 但部分远程修改失败。",
            edit_results=results,
            record_path=str(self.record_path),
        )

    def _append_record(
        self,
        fix_id: str,
        remote_project_dir: str,
        results: list[RemoteConfigEditResult],
    ) -> None:
        records = self._load_records()

        records.append(
            {
                "fix_id": fix_id,
                "remote": self.profile.target,
                "remote_project_dir": remote_project_dir,
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
        if not self.record_path.exists():
            return []

        try:
            return json.loads(self.record_path.read_text(encoding="utf-8"))
        except Exception:
            return []

    def _save_records(self, records: list[dict[str, Any]]) -> None:
        self.record_path.write_text(
            json.dumps(records, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )