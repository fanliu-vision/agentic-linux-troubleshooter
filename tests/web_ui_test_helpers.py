from __future__ import annotations

import io
import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from monitors.trace_store import (
    TRACE_STAGE_DETECTED,
    TRACE_STAGE_POLICY_DECIDED,
    TRACE_STAGE_PRECHECK_COMPLETED,
    ApprovalStore,
    TraceStore,
)
from web_ui.security import AuthManager
from web_ui.server import TraceUiRequestHandler


@dataclass
class HandlerResponse:
    status: int
    headers: dict[str, str]
    body: bytes

    def json(self) -> dict[str, Any]:
        return json.loads(self.body.decode("utf-8"))


class CapturingTraceUiHandler(TraceUiRequestHandler):
    def __init__(
        self,
        *,
        server: Any,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        raw = b""
        if body is not None:
            raw = json.dumps(body).encode("utf-8")
        request_headers = dict(headers or {})
        if raw and "Content-Length" not in request_headers:
            request_headers["Content-Length"] = str(len(raw))
        if raw and "Content-Type" not in request_headers:
            request_headers["Content-Type"] = "application/json"

        self.server = server
        self.command = method.upper()
        self.path = path
        self.request_version = "HTTP/1.1"
        self.headers = request_headers
        self.rfile = io.BytesIO(raw)
        self.wfile = io.BytesIO()
        self.response_status = 0
        self.response_headers: dict[str, str] = {}

    def send_response(self, code: int, message: str | None = None) -> None:
        self.response_status = int(code)

    def send_header(self, keyword: str, value: str) -> None:
        self.response_headers[keyword] = value

    def end_headers(self) -> None:
        return


def call_handler(
    server: Any,
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> HandlerResponse:
    handler = CapturingTraceUiHandler(
        server=server,
        method=method,
        path=path,
        body=body,
        headers=headers,
    )
    getattr(handler, f"do_{method.upper()}")()
    return HandlerResponse(
        status=handler.response_status,
        headers=dict(handler.response_headers),
        body=handler.wfile.getvalue(),
    )


def make_server_context(
    *,
    config_path: Path,
    state_dir: Path,
    output_root: Path,
    auth_token: str = "secret-token",
    auth_role_tokens: dict[str, str] | None = None,
    auth_enabled: bool = True,
) -> SimpleNamespace:
    return SimpleNamespace(
        config_path=str(config_path),
        state_dir=str(state_dir),
        output_root=str(output_root),
        quiet=True,
        auth_manager=AuthManager(
            token=auth_token,
            role_tokens=auth_role_tokens or {},
            enabled=auth_enabled,
        ),
        job_daemon=None,
    )


def login_headers(
    server: Any,
    *,
    operator: str = "tester",
    token: str = "secret-token",
) -> dict[str, str]:
    response = call_handler(
        server,
        "POST",
        "/api/auth/login",
        body={"operator": operator, "token": token},
    )
    assert response.status == 200
    cookie = response.headers["Set-Cookie"].split(";", 1)[0]
    csrf = response.json()["auth"]["csrf_token"]
    return {
        "Cookie": cookie,
        "X-CSRF-Token": csrf,
    }


def write_project_config(
    path: Path,
    *,
    project_id: str,
    project_dir: str,
    require_human_approval_for_live_apply: bool = True,
) -> None:
    approval = "true" if require_human_approval_for_live_apply else "false"
    path.write_text(
        f"""
projects:
  - project_id: {project_id}
    name: Trace UI HTTP Test Project
    mode: local
    owner: tests
    project_dir: {project_dir}
    run_command: python app.py --config config.json
    log_files:
      []
    policy:
      auto_recover: true
      auto_recovery_policy_enabled: true
      auto_recovery_dry_run: false
      require_human_approval_for_live_apply: {approval}
      rollback_on_failure: true
      allow_auto_apply:
        - fix-network-1
      escalation_required: []
""",
        encoding="utf-8",
    )


def make_precheck() -> dict[str, Any]:
    planned_edit = {
        "field_path": "server.port",
        "current_value": 8000,
        "old_value": 8000,
        "new_value": 8001,
        "semantic_status": "safe",
        "actionable": True,
    }
    return {
        "passed": True,
        "reasons": [],
        "target_fix_id": "fix-network-1",
        "planned_edits": [planned_edit],
        "actionable_planned_edits": [planned_edit],
        "unsafe_planned_edits": [],
        "actionable_edit_count": 1,
        "rollback_plan": {
            "available": True,
            "record_name": "applied_fixes.json",
            "backup_created_before_write": True,
            "rollback_method": "rollback_latest_apply",
        },
    }


def seed_trace_and_pending_approval(
    *,
    state_dir: Path,
    project_id: str,
    fingerprint: str = "seeded-network-port-fp",
) -> tuple[SimpleNamespace, dict[str, Any]]:
    event = SimpleNamespace(
        event_type="network_port",
        issue_type="network_port",
        severity="medium",
        summary="metrics port is already in use",
        source="service.log",
        fingerprint=fingerprint,
        signature=fingerprint,
        matched_keywords=["address already in use", "server.port"],
        raw_excerpt="OSError: address already in use for server.port",
    )
    precheck = make_precheck()
    gate = SimpleNamespace(
        event_type=event.event_type,
        fingerprint=event.fingerprint,
        strategy_layer="safe_auto_recover",
        candidate_fix_id="fix-network-1",
        selected_fix_id="fix-network-1",
        dry_run=False,
        would_execute=True,
        allowed_to_execute=False,
        auto_recover_allowed=True,
        operator_required=True,
        rollback_available=True,
        downgrade_reason="human_approval_required",
        precheck_result=precheck,
        audit_record={
            "event_type": event.event_type,
            "fingerprint": event.fingerprint,
            "selected_fix_id": "fix-network-1",
            "auto_recover_allowed": True,
            "rollback_available": True,
            "forbidden_action": False,
            "precheck_result": precheck,
        },
    )

    trace_store = TraceStore(project_id=project_id, state_dir=str(state_dir))
    approval_store = ApprovalStore(
        project_id=project_id,
        state_dir=str(state_dir),
        trace_store=trace_store,
    )
    trace_store.append(
        TRACE_STAGE_DETECTED,
        event=event,
        payload={
            "signature": event.signature,
            "matched_keywords": event.matched_keywords,
            "raw_excerpt_present": True,
        },
    )
    trace_store.append(
        TRACE_STAGE_POLICY_DECIDED,
        event=event,
        payload={
            "final_decision": {
                "action": "safe_auto_recover",
                "fix_id": "fix-network-1",
            },
            "gate": {
                "allowed_to_execute": False,
                "dry_run": False,
                "downgrade_reason": "human_approval_required",
            },
        },
    )
    trace_store.append(
        TRACE_STAGE_PRECHECK_COMPLETED,
        event=event,
        payload={"precheck_result": precheck},
    )
    request = approval_store.create_request_from_gate(
        event=event,
        gate=gate,
        audit_record={
            "gate": {
                "allowed_to_execute": False,
                "downgrade_reason": "human_approval_required",
            },
            "precheck_result": precheck,
        },
        reason="human_approval_required",
    )
    return event, request
