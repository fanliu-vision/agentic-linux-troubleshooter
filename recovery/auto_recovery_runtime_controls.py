from __future__ import annotations

import json
import socket
import time
from pathlib import Path
from typing import Any, Callable

from monitors.project_registry import ProjectConfig
from safe_recovery.semantics import (
    SEMANTIC_PORT_AVAILABLE,
    deferred_transition,
    evaluate_safe_transition,
)
from safe_recovery.registry import (
    SAFE_RECOVERY_SPECS_BY_FIX_ID as SAFE_FIX_SAFETY_SPECS,
    SafeRecoveryFieldCandidate as FixFieldCandidate,
    SafeRecoverySpec as FixSafetySpec,
)


def build_runtime_precheck_result(
    *,
    project: ProjectConfig,
    event_type: str,
    selected_fix_id: str,
) -> dict[str, Any]:
    reasons: list[str] = []
    spec = SAFE_FIX_SAFETY_SPECS.get(selected_fix_id)

    if spec is None:
        return {
            "passed": False,
            "reasons": ["fix_safety_spec_missing"],
            "target_match": False,
            "low_risk_action": False,
            "rollback_plan": _rollback_plan(project=project, spec=None),
            "planned_edits": [],
        }

    if spec.event_type != event_type:
        reasons.append("fix_event_type_mismatch")

    project_root = project.effective_project_dir
    if not project_root:
        reasons.append("project_dir_missing")

    read_result = _read_config(project=project, spec=spec)
    planned_edits = read_result["planned_edits"]

    if read_result["status"] in {"config_missing", "invalid_json", "unsafe_path"}:
        reasons.append(read_result["status"])

    if read_result["status"] == "read_ok" and not planned_edits:
        reasons.append("target_config_field_missing")

    unsafe_planned_edits = [
        item for item in planned_edits if item.get("semantic_status") == "unsafe"
    ]
    actionable_planned_edits = [
        item for item in planned_edits if item.get("actionable") is True
    ]
    no_op_planned_edits = [
        item for item in planned_edits if item.get("no_op") is True
    ]

    if unsafe_planned_edits and not actionable_planned_edits:
        reasons.append("unsafe_semantic_transition")

    rollback_plan = _rollback_plan(project=project, spec=spec)
    if not rollback_plan["available"]:
        reasons.append("rollback_plan_unavailable")

    no_op = bool(no_op_planned_edits and not actionable_planned_edits and not unsafe_planned_edits)

    return {
        "passed": not reasons,
        "reasons": reasons,
        "target_match": not reasons,
        "target_event_type": event_type,
        "target_fix_id": selected_fix_id,
        "project_id": project.project_id,
        "project_mode": project.mode,
        "target_config_path": read_result["config_path"],
        "config_read_status": read_result["status"],
        "planned_edits": planned_edits,
        "actionable_planned_edits": actionable_planned_edits,
        "no_op_planned_edits": no_op_planned_edits,
        "unsafe_planned_edits": unsafe_planned_edits,
        "actionable_edit_count": len(actionable_planned_edits),
        "no_op": no_op,
        "semantic_status": _semantic_status(
            planned_edits=planned_edits,
            actionable_planned_edits=actionable_planned_edits,
            no_op_planned_edits=no_op_planned_edits,
            unsafe_planned_edits=unsafe_planned_edits,
        ),
        "checked_candidate_fields": [
            item.field_path for item in spec.candidates
        ],
        "low_risk_action": True,
        "low_risk_reason": spec.low_risk_reason,
        "affects_other_services": False,
        "rollback_plan": rollback_plan,
    }


class RuntimeAutoRecoveryCooldownTracker:
    def __init__(
        self,
        *,
        fingerprint_seconds: int = 3600,
        event_type_seconds: int = 1800,
        project_seconds: int = 600,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.fingerprint_seconds = max(0, int(fingerprint_seconds))
        self.event_type_seconds = max(0, int(event_type_seconds))
        self.project_seconds = max(0, int(project_seconds))
        self.clock = clock or time.time
        self._fingerprint_last: dict[str, float] = {}
        self._event_type_last: dict[str, float] = {}
        self._project_last: dict[str, float] = {}

    @classmethod
    def from_project(cls, project: ProjectConfig) -> RuntimeAutoRecoveryCooldownTracker:
        policy = project.policy
        return cls(
            fingerprint_seconds=getattr(
                policy,
                "auto_recovery_fingerprint_cooldown_seconds",
                3600,
            ),
            event_type_seconds=getattr(
                policy,
                "auto_recovery_event_type_cooldown_seconds",
                1800,
            ),
            project_seconds=getattr(
                policy,
                "auto_recovery_project_cooldown_seconds",
                600,
            ),
        )

    def check(
        self,
        *,
        event_type: str,
        fingerprint: str,
        project_id: str,
    ) -> dict[str, Any]:
        now = self.clock()
        scopes = {
            "fingerprint": self._scope_result(
                registry=self._fingerprint_last,
                key=fingerprint,
                cooldown_seconds=self.fingerprint_seconds,
                now=now,
            ),
            "event_type": self._scope_result(
                registry=self._event_type_last,
                key=event_type,
                cooldown_seconds=self.event_type_seconds,
                now=now,
            ),
            "project": self._scope_result(
                registry=self._project_last,
                key=project_id,
                cooldown_seconds=self.project_seconds,
                now=now,
            ),
        }
        allowed = all(item["allowed"] for item in scopes.values())
        blocked = [scope for scope, item in scopes.items() if not item["allowed"]]
        return {
            "allowed": allowed,
            "reserved": False,
            "reason": "" if allowed else f"cooldown_active:{','.join(blocked)}",
            "scopes": scopes,
        }

    def reserve(
        self,
        *,
        event_type: str,
        fingerprint: str,
        project_id: str,
    ) -> dict[str, Any]:
        result = self.check(
            event_type=event_type,
            fingerprint=fingerprint,
            project_id=project_id,
        )
        if not result["allowed"]:
            return result

        now = self.clock()
        self._fingerprint_last[fingerprint] = now
        self._event_type_last[event_type] = now
        self._project_last[project_id] = now
        result["reserved"] = True
        result["reason"] = "reserved_for_auto_recovery_execution"
        return result

    @staticmethod
    def _scope_result(
        *,
        registry: dict[str, float],
        key: str,
        cooldown_seconds: int,
        now: float,
    ) -> dict[str, Any]:
        if not key:
            return {
                "allowed": False,
                "key": key,
                "cooldown_seconds": cooldown_seconds,
                "remaining_seconds": cooldown_seconds,
                "reason": "cooldown_key_missing",
            }

        last = registry.get(key)
        if cooldown_seconds <= 0 or last is None:
            return {
                "allowed": True,
                "key": key,
                "cooldown_seconds": cooldown_seconds,
                "remaining_seconds": 0,
                "reason": "cooldown_not_active",
            }

        elapsed = max(0.0, now - last)
        remaining = max(0, int(cooldown_seconds - elapsed))
        return {
            "allowed": elapsed >= cooldown_seconds,
            "key": key,
            "cooldown_seconds": cooldown_seconds,
            "remaining_seconds": remaining,
            "reason": ""
            if elapsed >= cooldown_seconds
            else "cooldown_window_active",
        }


def _read_config(
    *,
    project: ProjectConfig,
    spec: FixSafetySpec,
) -> dict[str, Any]:
    root = Path(project.effective_project_dir).expanduser()
    config_path = (root / spec.relative_config_path).resolve()

    if project.is_remote and not config_path.exists():
        return {
            "status": "remote_deferred_no_write",
            "config_path": str(config_path),
            "planned_edits": [
                {
                    "field_path": item.field_path,
                    "old_value_available": False,
                    "old_value": None,
                    "new_value": item.new_value,
                    "already_target_value": False,
                    **deferred_transition(item.semantic_rule),
                }
                for item in spec.candidates
            ],
        }

    try:
        config_path.relative_to(root.resolve())
    except ValueError:
        return {"status": "unsafe_path", "config_path": str(config_path), "planned_edits": []}

    if not config_path.exists():
        return {"status": "config_missing", "config_path": str(config_path), "planned_edits": []}

    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return {"status": "invalid_json", "config_path": str(config_path), "planned_edits": []}

    planned_edits = []
    for item in spec.candidates:
        ok, old_value = _try_get_nested_value(data, item.field_path)
        if ok:
            port_available = None
            port_host = None
            if item.semantic_rule == SEMANTIC_PORT_AVAILABLE and old_value != item.new_value:
                port_host = _target_port_host(data)
                port_available = _is_tcp_port_available(port_host, item.new_value)

            transition = evaluate_safe_transition(
                semantic_rule=item.semantic_rule,
                old_value=old_value,
                new_value=item.new_value,
                port_available=port_available,
            )
            port_fields = {}
            if item.semantic_rule == SEMANTIC_PORT_AVAILABLE:
                port_fields = {
                    "target_port_host": port_host,
                    "target_port_available": port_available,
                }

            planned_edits.append(
                {
                    "field_path": item.field_path,
                    "old_value_available": True,
                    "old_value": old_value,
                    "new_value": item.new_value,
                    "already_target_value": old_value == item.new_value,
                    **transition,
                    **port_fields,
                }
            )

    return {
        "status": "read_ok",
        "config_path": str(config_path),
        "planned_edits": planned_edits,
    }


def _rollback_plan(
    *,
    project: ProjectConfig,
    spec: FixSafetySpec | None,
) -> dict[str, Any]:
    record_name = "remote_applied_fixes.json" if project.is_remote else "applied_fixes.json"
    return {
        "available": bool(project.policy.rollback_on_failure and spec is not None),
        "record_name": record_name,
        "backup_created_before_write": True,
        "rollback_method": "remote_rollback_latest_apply"
        if project.is_remote
        else "rollback_latest_apply",
    }


def _try_get_nested_value(data: dict[str, Any], field_path: str) -> tuple[bool, Any]:
    current: Any = data
    for part in field_path.split("."):
        if not isinstance(current, dict) or part not in current:
            return False, None
        current = current[part]
    return True, current


def _semantic_status(
    *,
    planned_edits: list[dict[str, Any]],
    actionable_planned_edits: list[dict[str, Any]],
    no_op_planned_edits: list[dict[str, Any]],
    unsafe_planned_edits: list[dict[str, Any]],
) -> str:
    if unsafe_planned_edits:
        if not actionable_planned_edits:
            return "unsafe"
    if actionable_planned_edits:
        return "actionable"
    if no_op_planned_edits:
        return "no_op"
    if planned_edits:
        return "unknown"
    return "no_target"


def _target_port_host(data: dict[str, Any]) -> str:
    ok, host = _try_get_nested_value(data, "metrics_host")
    if ok and isinstance(host, str) and host.strip():
        return host.strip()
    return "127.0.0.1"


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
