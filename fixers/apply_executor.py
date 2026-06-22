from __future__ import annotations

import json
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fixers.safe_config_editor import ConfigEditResult, SafeConfigEditor
from safe_recovery.semantics import (
    SEMANTIC_PORT_AVAILABLE,
    evaluate_safe_transition,
)
from safe_recovery.registry import (
    SAFE_RECOVERY_FIX_IDS,
    SafeRecoveryFieldCandidate,
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
        results = self._update_first_safe_registry_field(spec)
        return self._finalize(
            spec.fix_id,
            results,
            spec.local_success_message,
        )

    def _update_first_safe_registry_field(
        self,
        spec: SafeRecoverySpec,
    ) -> list[ConfigEditResult]:
        config_path = (self.project_dir / spec.relative_config_path).resolve()

        if not self.editor._is_inside_project(config_path):
            return [
                ConfigEditResult(
                    success=False,
                    message="配置文件路径不在 project_dir 内，拒绝修改。",
                    config_path=str(config_path),
                )
            ]

        if not config_path.exists():
            return [
                ConfigEditResult(
                    success=False,
                    message="配置文件不存在。",
                    config_path=str(config_path),
                )
            ]

        if config_path.suffix.lower() != ".json":
            return [
                ConfigEditResult(
                    success=False,
                    message="当前 SafeApplyExecutor 仅支持 .json 配置文件。",
                    config_path=str(config_path),
                )
            ]

        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return [
                ConfigEditResult(
                    success=False,
                    message=f"读取或解析 JSON 失败：{type(exc).__name__}: {exc}",
                    config_path=str(config_path),
                )
            ]

        results: list[ConfigEditResult] = []
        no_op_results: list[ConfigEditResult] = []

        for candidate in spec.candidates:
            ok, old_value = self._try_get_nested_value(data, candidate.field_path)
            if not ok:
                results.append(
                    ConfigEditResult(
                        success=False,
                        message=f"字段不存在：{candidate.field_path}",
                        config_path=str(config_path),
                        field_path=candidate.field_path,
                        new_value=candidate.new_value,
                    )
                )
                continue

            transition = self._semantic_transition(
                data=data,
                candidate=candidate,
                old_value=old_value,
            )

            if transition["no_op"]:
                no_op_results.append(
                    ConfigEditResult(
                        success=True,
                        message="字段当前值已经处于安全目标值，无需修改。",
                        config_path=str(config_path),
                        field_path=candidate.field_path,
                        old_value=old_value,
                        new_value=candidate.new_value,
                        no_op=True,
                        semantic_status=str(transition["semantic_status"]),
                        semantic_reason=str(transition["semantic_reason"]),
                    )
                )
                continue

            if not transition["actionable"]:
                results.append(
                    ConfigEditResult(
                        success=False,
                        message=(
                            "字段变更不满足 safe_auto_recover 语义降级要求："
                            f"{transition['semantic_reason']}"
                        ),
                        config_path=str(config_path),
                        field_path=candidate.field_path,
                        old_value=old_value,
                        new_value=candidate.new_value,
                        semantic_status=str(transition["semantic_status"]),
                        semantic_reason=str(transition["semantic_reason"]),
                    )
                )
                continue

            result = self.editor.update_json_field(
                relative_config_path=spec.relative_config_path,
                field_path=candidate.field_path,
                new_value=candidate.new_value,
                fix_id=spec.fix_id,
            )
            result.semantic_status = str(transition["semantic_status"])
            result.semantic_reason = str(transition["semantic_reason"])
            return [result]

        if no_op_results:
            return [no_op_results[0]]

        return results

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

        if success and any(item.backup_path and item.diff_path for item in results):
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
                    if item.success and item.backup_path and item.diff_path
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

    def _semantic_transition(
        self,
        *,
        data: dict[str, Any],
        candidate: SafeRecoveryFieldCandidate,
        old_value: Any,
    ) -> dict[str, Any]:
        port_available = None
        if candidate.semantic_rule == SEMANTIC_PORT_AVAILABLE and old_value != candidate.new_value:
            port_available = self._is_tcp_port_available(
                host=self._target_port_host(data),
                port=candidate.new_value,
            )

        return evaluate_safe_transition(
            semantic_rule=candidate.semantic_rule,
            old_value=old_value,
            new_value=candidate.new_value,
            port_available=port_available,
        )

    @staticmethod
    def _try_get_nested_value(data: dict[str, Any], field_path: str) -> tuple[bool, Any]:
        current: Any = data
        for part in field_path.split("."):
            if not isinstance(current, dict) or part not in current:
                return False, None
            current = current[part]
        return True, current

    def _target_port_host(self, data: dict[str, Any]) -> str:
        ok, host = self._try_get_nested_value(data, "metrics_host")
        if ok and isinstance(host, str) and host.strip():
            return host.strip()
        return "127.0.0.1"

    @staticmethod
    def _is_tcp_port_available(host: str, port: Any) -> bool:
        if not isinstance(port, int) or isinstance(port, bool):
            return False
        if port <= 0 or port > 65535:
            return False

        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind((host, port))
            return True
        except OSError:
            return False
