from __future__ import annotations

import argparse
import json
import mimetypes
import os
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from monitors.report_index_store import ReportIndexStore
from monitors.trace_store import APPROVAL_STATUS_APPROVED
from web_ui.job_worker import JobWorkerDaemon
from web_ui.operation_runner import OperationRunner
from web_ui.recovery_history import RecoveryHistoryService
from web_ui.runtime_control import (
    JOB_STATUS_FAILED,
    JOB_STATUS_SUCCEEDED,
    JobStore,
    RuntimeControlService,
)
from web_ui.security import (
    AuthContext,
    AuthManager,
    auth_payload,
    confirmation_missing,
    has_permission,
    PERMISSION_APPROVE,
    PERMISSION_LIVE_APPLY,
    PERMISSION_OPERATE,
    PERMISSION_READ,
    PERMISSION_RETRY_HIGH_RISK,
    PERMISSION_ROLLBACK,
)
from web_ui.trace_data import TraceUiDataService


STATIC_DIR = Path(__file__).resolve().parent / "static"
HIGH_RISK_JOB_ACTIONS = {"live_apply", "rollback_latest", "approved_recovery_job"}
OPERATION_PERMISSIONS = {
    "start_monitor": PERMISSION_OPERATE,
    "stop_monitor": PERMISSION_OPERATE,
    "refresh_logs": PERMISSION_OPERATE,
    "generate_report": PERMISSION_OPERATE,
    "dry_run_recovery": PERMISSION_OPERATE,
    "live_apply": PERMISSION_LIVE_APPLY,
    "rollback_latest": PERMISSION_ROLLBACK,
}


def _json_bytes(data: dict[str, Any] | list[Any]) -> bytes:
    return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")


def _read_json_body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0") or 0)
    if length <= 0:
        return {}
    raw = handler.rfile.read(length)
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8"))


def _int_query(query: dict[str, list[str]], key: str, default: int) -> int:
    values = query.get(key) or []
    if not values:
        return default
    try:
        return max(0, int(values[0]))
    except (TypeError, ValueError):
        return default


class TraceUiRequestHandler(BaseHTTPRequestHandler):
    server_version = "AgenticTraceUI/0.1"

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = unquote(parsed.path)

        if path.startswith("/api/"):
            if self._is_auth_path(path):
                self._handle_api_get(path)
                return
            if not self._require_authenticated():
                return
            self._handle_api_get(path)
            return

        self._serve_static(path)

    def do_HEAD(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = unquote(parsed.path)

        if path.startswith("/api/"):
            if self._is_auth_path(path):
                self._handle_api_get(path)
                return
            if not self._require_authenticated():
                return
            self._handle_api_get(path)
            return

        self._serve_static(path)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = unquote(parsed.path)

        if path.startswith("/api/"):
            if self._is_auth_path(path):
                self._handle_api_post(path)
                return
            if not self._require_authenticated():
                return
            if not self._require_csrf():
                return
            self._handle_api_post(path)
            return

        self._send_json(
            {"error": "not_found"},
            status=HTTPStatus.NOT_FOUND,
        )

    def log_message(self, format: str, *args: Any) -> None:
        if getattr(self.server, "quiet", False):
            return
        super().log_message(format, *args)

    def _auth_manager(self) -> AuthManager:
        return getattr(self.server, "auth_manager")

    def _auth_context(self) -> AuthContext:
        cached = getattr(self, "_cached_auth_context", None)
        if cached is not None:
            return cached
        context = self._auth_manager().authenticate(self.headers.get("Cookie", ""))
        self._cached_auth_context = context
        return context

    def _operator(self) -> str:
        context = self._auth_context()
        return context.operator or "web-ui"

    def _role(self) -> str:
        return self._auth_context().role

    @staticmethod
    def _is_auth_path(path: str) -> bool:
        return path in {"/api/auth/status", "/api/auth/login", "/api/auth/logout"}

    def _require_authenticated(self) -> bool:
        context = self._auth_context()
        if context.authenticated:
            return True
        self._send_json(
            {"error": "auth_required", "auth": auth_payload(context)},
            status=HTTPStatus.UNAUTHORIZED,
        )
        return False

    def _require_permission(
        self,
        permission: str,
        *,
        action: str = "",
        project_id: str = "",
    ) -> bool:
        context = self._auth_context()
        if has_permission(context, permission):
            return True
        self._send_json(
            {
                "error": "permission_denied",
                "required_permission": permission,
                "action": action,
                "project_id": project_id,
                "auth": auth_payload(context),
            },
            status=HTTPStatus.FORBIDDEN,
        )
        return False

    def _require_csrf(self) -> bool:
        context = self._auth_context()
        token = self.headers.get("X-CSRF-Token", "")
        if self._auth_manager().validate_csrf(context, token):
            return True
        self._send_json(
            {"error": "csrf_required"},
            status=HTTPStatus.FORBIDDEN,
        )
        return False

    def _request_audit(
        self,
        *,
        action: str,
        project_id: str = "",
        body: dict[str, Any] | None = None,
        request_id: str = "",
        fingerprint: str = "",
        fix_id: str = "",
        result: str = "accepted",
    ) -> dict[str, Any]:
        body = dict(body or {})
        client_address = getattr(self, "client_address", ("", 0))
        remote_addr = ""
        if isinstance(client_address, tuple) and client_address:
            remote_addr = str(client_address[0])
        return {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "operator": self._operator(),
            "role": self._role(),
            "action": action,
            "project_id": project_id,
            "fingerprint": fingerprint or str(body.get("fingerprint", "")),
            "request_id": request_id or str(body.get("request_id", "")),
            "fix_id": fix_id or str(body.get("fix_id", "")),
            "confirmation_action": str(body.get("confirmation_action", "")),
            "remote_addr": remote_addr,
            "user_agent": self.headers.get("User-Agent", ""),
            "result": result,
        }

    def _service(self, project_id: str) -> TraceUiDataService:
        return TraceUiDataService(
            project_id=project_id,
            state_dir=getattr(self.server, "state_dir", "state"),
            config_path=getattr(self.server, "config_path", "configs/projects.yaml"),
        )

    def _runtime_service(self, project_id: str) -> RuntimeControlService:
        return RuntimeControlService(
            project_id=project_id,
            state_dir=getattr(self.server, "state_dir", "state"),
            config_path=getattr(self.server, "config_path", "configs/projects.yaml"),
        )

    def _operation_runner(self, project_id: str) -> OperationRunner:
        return OperationRunner(
            project_id=project_id,
            state_dir=getattr(self.server, "state_dir", "state"),
            config_path=getattr(self.server, "config_path", "configs/projects.yaml"),
            output_root=getattr(self.server, "output_root", "outputs/monitors"),
        )

    def _report_store(self, project_id: str) -> ReportIndexStore:
        return ReportIndexStore(
            project_id=project_id,
            state_dir=getattr(self.server, "state_dir", "state"),
        )

    def _job_store(self, project_id: str) -> JobStore:
        return JobStore(
            project_id=project_id,
            state_dir=getattr(self.server, "state_dir", "state"),
        )

    def _recovery_history_service(self, project_id: str) -> RecoveryHistoryService:
        return RecoveryHistoryService(
            project_id=project_id,
            state_dir=getattr(self.server, "state_dir", "state"),
            config_path=getattr(self.server, "config_path", "configs/projects.yaml"),
            output_root=getattr(self.server, "output_root", "outputs/monitors"),
        )

    def _handle_api_get(self, path: str) -> None:
        parts = [item for item in path.split("/") if item]
        query = parse_qs(urlparse(self.path).query)
        offset = _int_query(query, "offset", 0)

        try:
            if parts == ["api", "auth", "status"]:
                self._send_json({"auth": auth_payload(self._auth_context())})
                return

            if parts == ["api", "projects"]:
                if not self._require_permission(PERMISSION_READ, action="projects"):
                    return
                service = TraceUiDataService(
                    project_id="",
                    state_dir=getattr(self.server, "state_dir", "state"),
                    config_path=getattr(self.server, "config_path", "configs/projects.yaml"),
                )
                self._send_json({"projects": service.projects()})
                return

            if len(parts) == 4 and parts[:2] == ["api", "projects"] and parts[3] == "overview":
                project_id = parts[2]
                if not self._require_permission(PERMISSION_READ, action="overview", project_id=project_id):
                    return
                self._send_json(self._service(project_id).overview())
                return

            if len(parts) == 4 and parts[:2] == ["api", "projects"] and parts[3] == "events":
                project_id = parts[2]
                if not self._require_permission(PERMISSION_READ, action="events", project_id=project_id):
                    return
                self._send_json({"events": self._service(project_id).events()})
                return

            if len(parts) == 5 and parts[:2] == ["api", "projects"] and parts[3] == "events":
                project_id = parts[2]
                if not self._require_permission(PERMISSION_READ, action="event_detail", project_id=project_id):
                    return
                fingerprint = parts[4]
                self._send_json(self._service(project_id).event_detail(fingerprint))
                return

            if len(parts) == 4 and parts[:2] == ["api", "projects"] and parts[3] == "runtime":
                project_id = parts[2]
                if not self._require_permission(PERMISSION_READ, action="runtime", project_id=project_id):
                    return
                self._send_json(self._runtime_service(project_id).runtime())
                return

            if len(parts) == 4 and parts[:2] == ["api", "projects"] and parts[3] == "jobs":
                project_id = parts[2]
                if not self._require_permission(PERMISSION_READ, action="jobs", project_id=project_id):
                    return
                self._send_json(
                    self._runtime_service(project_id).jobs(
                        limit=_int_query(query, "limit", 20),
                        offset=offset,
                    )
                )
                return

            if (
                len(parts) == 6
                and parts[:2] == ["api", "projects"]
                and parts[3] == "jobs"
                and parts[5] == "log"
            ):
                project_id = parts[2]
                if not self._require_permission(PERMISSION_READ, action="job_log", project_id=project_id):
                    return
                job_id = parts[4]
                self._send_json(self._job_store(project_id).job_log(job_id))
                return

            if len(parts) == 4 and parts[:2] == ["api", "projects"] and parts[3] == "worker":
                project_id = parts[2]
                if not self._require_permission(PERMISSION_READ, action="worker", project_id=project_id):
                    return
                daemon = getattr(self.server, "job_daemon", None)
                self._send_json(
                    {
                        "project_id": project_id,
                        "worker": daemon.status() if daemon else {"running": False},
                    }
                )
                return

            if len(parts) == 4 and parts[:2] == ["api", "projects"] and parts[3] == "operations":
                project_id = parts[2]
                if not self._require_permission(PERMISSION_READ, action="operations", project_id=project_id):
                    return
                self._send_json({"operations": OperationRunner.operations()})
                return

            if len(parts) == 4 and parts[:2] == ["api", "projects"] and parts[3] == "reports":
                project_id = parts[2]
                if not self._require_permission(PERMISSION_READ, action="reports", project_id=project_id):
                    return
                store = self._report_store(project_id)
                self._send_json(
                    {
                        "project_id": project_id,
                        "report_index_path": str(store.report_index_path),
                        "reports": store.reports(
                            limit=_int_query(query, "limit", 100),
                            offset=offset,
                        ),
                    }
                )
                return

            if len(parts) == 5 and parts[:2] == ["api", "projects"] and parts[3] == "reports":
                project_id = parts[2]
                if not self._require_permission(PERMISSION_READ, action="report_detail", project_id=project_id):
                    return
                report_id = unquote(parts[4])
                self._send_json(self._report_store(project_id).detail(report_id))
                return

            if (
                len(parts) == 4
                and parts[:2] == ["api", "projects"]
                and parts[3] == "recovery-history"
            ):
                project_id = parts[2]
                if not self._require_permission(PERMISSION_READ, action="recovery_history", project_id=project_id):
                    return
                self._send_json(
                    self._recovery_history_service(project_id).history(
                        limit=_int_query(query, "limit", 100),
                        offset=offset,
                    )
                )
                return

            self._send_json(
                {"error": "not_found"},
                status=HTTPStatus.NOT_FOUND,
            )
        except KeyError as exc:
            self._send_json(
                {"error": str(exc)},
                status=HTTPStatus.NOT_FOUND,
            )
        except Exception as exc:
            self._send_json(
                {"error": type(exc).__name__, "message": str(exc)},
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )

    def _handle_api_post(self, path: str) -> None:
        parts = [item for item in path.split("/") if item]
        try:
            body = _read_json_body(self)
            operator = self._operator()
            comment = str(body.get("comment") or "")
            connection_mode = str(body.get("connection_mode") or "")

            if parts == ["api", "auth", "login"]:
                context = self._auth_manager().login(
                    token=str(body.get("token", "")),
                    operator=str(body.get("operator", "")),
                )
                if not context.authenticated:
                    self._send_json(
                        {"error": "invalid_token", "auth": auth_payload(context)},
                        status=HTTPStatus.UNAUTHORIZED,
                    )
                    return
                self._cached_auth_context = context
                self._send_json(
                    {"auth": auth_payload(context)},
                    headers={"Set-Cookie": self._auth_manager().session_cookie(context)},
                )
                return

            if parts == ["api", "auth", "logout"]:
                context = self._auth_context()
                self._auth_manager().logout(context.session_id)
                self._send_json(
                    {"auth": auth_payload(AuthContext(authenticated=False))},
                    headers={"Set-Cookie": AuthManager.clear_cookie()},
                )
                return

            if (
                len(parts) == 4
                and parts[:2] == ["api", "projects"]
                and parts[3] in {"connect", "health-check"}
            ):
                project_id = parts[2]
                runtime = self._runtime_service(project_id)
                action = parts[3]
                if not self._require_permission(
                    PERMISSION_OPERATE,
                    action=action,
                    project_id=project_id,
                ):
                    return
                audit = self._request_audit(
                    action=action,
                    project_id=project_id,
                    body=body,
                )
                if parts[3] == "connect":
                    self._send_json(
                        runtime.enqueue_connection(
                            action="connect",
                            connection_mode=connection_mode,
                            operator=operator,
                            role=self._role(),
                            request_audit=audit,
                        )
                    )
                else:
                    self._send_json(
                        runtime.enqueue_connection(
                            action="health_check",
                            connection_mode=connection_mode,
                            operator=operator,
                            role=self._role(),
                            request_audit=audit,
                        )
                    )
                return

            if (
                len(parts) == 5
                and parts[:2] == ["api", "projects"]
                and parts[3] == "operations"
            ):
                project_id = parts[2]
                action = parts[4]
                permission = OPERATION_PERMISSIONS.get(action, PERMISSION_OPERATE)
                if not self._require_permission(
                    permission,
                    action=action,
                    project_id=project_id,
                ):
                    return
                if confirmation_missing(action, body):
                    self._send_json(
                        {
                            "error": "confirmation_required",
                            "action": action,
                        },
                        status=HTTPStatus.PRECONDITION_FAILED,
                    )
                    return
                self._send_json(
                    self._operation_runner(project_id).enqueue(
                        action,
                        operator=operator,
                        role=self._role(),
                        request_audit=self._request_audit(
                            action=action,
                            project_id=project_id,
                            body=body,
                        ),
                    )
                )
                return

            if (
                len(parts) == 6
                and parts[:2] == ["api", "projects"]
                and parts[3] == "jobs"
                and parts[5] in {"cancel", "retry"}
            ):
                project_id = parts[2]
                job_id = parts[4]
                store = self._job_store(project_id)
                previous = store.get(job_id)
                previous_action = str(previous.get("action", ""))
                high_risk = previous_action in HIGH_RISK_JOB_ACTIONS
                permission = (
                    PERMISSION_RETRY_HIGH_RISK
                    if high_risk
                    else PERMISSION_OPERATE
                )
                if not self._require_permission(
                    permission,
                    action=f"job_{parts[5]}",
                    project_id=project_id,
                ):
                    return
                audit = self._request_audit(
                    action=f"job_{parts[5]}",
                    project_id=project_id,
                    body=body,
                    result="accepted",
                )
                if parts[5] == "cancel":
                    job = store.request_cancel(
                        job_id,
                        operator=operator,
                        role=self._role(),
                        request_audit=audit,
                    )
                else:
                    if (
                        previous_action in HIGH_RISK_JOB_ACTIONS
                        and confirmation_missing("job_retry", body)
                    ):
                        self._send_json(
                            {
                                "error": "confirmation_required",
                                "action": "job_retry",
                            },
                            status=HTTPStatus.PRECONDITION_FAILED,
                        )
                        return
                    job = store.retry(
                        job_id,
                        operator=operator,
                        role=self._role(),
                        request_audit=audit,
                    )
                self._send_json(
                    {
                        "job": job,
                        "jobs": store.jobs(limit=20),
                    }
                )
                return

            if (
                len(parts) == 6
                and parts[:2] == ["api", "projects"]
                and parts[3] == "recovery-history"
                and parts[5] == "rollback"
            ):
                project_id = parts[2]
                target_identity = parts[4]
                if not self._require_permission(
                    PERMISSION_ROLLBACK,
                    action="recovery_history_rollback",
                    project_id=project_id,
                ):
                    return
                if confirmation_missing("recovery_history_rollback", body):
                    self._send_json(
                        {
                            "error": "confirmation_required",
                            "action": "recovery_history_rollback",
                        },
                        status=HTTPStatus.PRECONDITION_FAILED,
                    )
                    return
                self._send_json(
                    self._operation_runner(project_id).enqueue_rollback_history(
                        target_identity,
                        operator=operator,
                        role=self._role(),
                        request_audit=self._request_audit(
                            action="recovery_history_rollback",
                            project_id=project_id,
                            body=body,
                        ),
                    )
                )
                return

            if (
                len(parts) == 6
                and parts[:2] == ["api", "projects"]
                and parts[3] == "approvals"
            ):
                project_id = parts[2]
                request_id = parts[4]
                action = parts[5]
                if not self._require_permission(
                    PERMISSION_APPROVE,
                    action=f"approval_{action}",
                    project_id=project_id,
                ):
                    return
                confirmation_action = f"approval_{action}"
                if confirmation_missing(confirmation_action, body):
                    self._send_json(
                        {
                            "error": "confirmation_required",
                            "action": confirmation_action,
                        },
                        status=HTTPStatus.PRECONDITION_FAILED,
                    )
                    return
                service = self._service(project_id)
                runtime = self._runtime_service(project_id)
                audit = self._request_audit(
                    action=f"approval_{action}",
                    project_id=project_id,
                    body=body,
                    request_id=request_id,
                )
                job = runtime.record_ui_action_job(
                    action=f"approval_{action}",
                    operator=operator,
                    role=self._role(),
                    payload={
                        "request_id": request_id,
                        "comment_present": bool(comment),
                        "request_audit": audit,
                    },
                    request_audit=audit,
                )
                runtime.mark_ui_action_running(
                    job["job_id"],
                    summary=f"approval {action} running",
                )

                try:
                    if action == "approve":
                        record = service.approve(
                            request_id,
                            operator=operator,
                            role=self._role(),
                            request_audit=audit,
                        )
                    elif action == "reject":
                        record = service.reject(
                            request_id,
                            operator=operator,
                            comment=comment,
                            role=self._role(),
                            request_audit=audit,
                        )
                    elif action == "expire":
                        record = service.expire(
                            request_id,
                            operator=operator,
                            comment=comment,
                            role=self._role(),
                            request_audit=audit,
                        )
                    else:
                        runtime.complete_ui_action_job(
                            job["job_id"],
                            status=JOB_STATUS_FAILED,
                            summary="unsupported approval action",
                            result={"action": action},
                        )
                        self._send_json(
                            {"error": "unsupported_approval_action"},
                            status=HTTPStatus.BAD_REQUEST,
                        )
                        return
                except Exception as exc:
                    runtime.complete_ui_action_job(
                        job["job_id"],
                        status=JOB_STATUS_FAILED,
                        summary=f"{type(exc).__name__}: {exc}",
                        result={"request_id": request_id, "action": action},
                    )
                    raise

                approved_recovery: dict[str, Any] = {}
                if (
                    action == "approve"
                    and record.get("status") == APPROVAL_STATUS_APPROVED
                ):
                    approved_recovery = self._operation_runner(
                        project_id
                    ).enqueue_approved_recovery(
                        request_id,
                        operator=operator,
                        role=self._role(),
                        request_audit=audit,
                    )

                fingerprint = str(record.get("fingerprint", ""))
                detail = (
                    service.event_detail(fingerprint)
                    if fingerprint
                    else {"approval": {"latest": record}}
                )
                job_result: dict[str, Any] = {"approval": record}
                if approved_recovery:
                    job_result["approved_recovery"] = approved_recovery.get(
                        "job",
                        approved_recovery,
                    )
                completed_job = runtime.complete_ui_action_job(
                    job["job_id"],
                    status=JOB_STATUS_SUCCEEDED,
                    summary=f"approval {action} completed",
                    result=job_result,
                )
                response = {"approval": record, "event": detail, "job": completed_job}
                if approved_recovery:
                    response["approved_recovery"] = approved_recovery
                self._send_json(response)
                return

            self._send_json(
                {"error": "not_found"},
                status=HTTPStatus.NOT_FOUND,
            )
        except KeyError as exc:
            self._send_json(
                {"error": str(exc)},
                status=HTTPStatus.NOT_FOUND,
            )
        except Exception as exc:
            self._send_json(
                {"error": type(exc).__name__, "message": str(exc)},
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )

    def _serve_static(self, path: str) -> None:
        if path in {"", "/"}:
            path = "/index.html"

        relative = Path(path.lstrip("/"))
        candidate = (STATIC_DIR / relative).resolve()
        static_root = STATIC_DIR.resolve()

        if static_root not in candidate.parents and candidate != static_root:
            self._send_json(
                {"error": "unsafe_static_path"},
                status=HTTPStatus.BAD_REQUEST,
            )
            return

        if not candidate.exists() or not candidate.is_file():
            self._send_json(
                {"error": "not_found"},
                status=HTTPStatus.NOT_FOUND,
            )
            return

        content_type = mimetypes.guess_type(str(candidate))[0] or "application/octet-stream"
        data = candidate.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _send_json(
        self,
        data: dict[str, Any] | list[Any],
        *,
        status: HTTPStatus = HTTPStatus.OK,
        headers: dict[str, str] | None = None,
    ) -> None:
        raw = _json_bytes(data)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        for key, value in dict(headers or {}).items():
            if value:
                self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(raw)


def build_server(
    *,
    host: str,
    port: int,
    config_path: str,
    state_dir: str,
    output_root: str = "outputs/monitors",
    quiet: bool = False,
    auth_token: str = "",
    auth_role_tokens: dict[str, str] | None = None,
    auth_enabled: bool = True,
    session_ttl_seconds: int = 8 * 60 * 60,
    start_worker: bool = False,
    worker_poll_interval_seconds: float = 1.5,
) -> ThreadingHTTPServer:
    auth_manager = AuthManager(
        token=auth_token,
        role_tokens=auth_role_tokens or {},
        enabled=auth_enabled,
        session_ttl_seconds=session_ttl_seconds,
    )
    server = ThreadingHTTPServer((host, port), TraceUiRequestHandler)
    server.config_path = config_path  # type: ignore[attr-defined]
    server.state_dir = state_dir  # type: ignore[attr-defined]
    server.output_root = output_root  # type: ignore[attr-defined]
    server.quiet = quiet  # type: ignore[attr-defined]
    server.auth_manager = auth_manager  # type: ignore[attr-defined]
    server.job_daemon = None  # type: ignore[attr-defined]
    if start_worker:
        server.job_daemon = JobWorkerDaemon(  # type: ignore[attr-defined]
            state_dir=state_dir,
            config_path=config_path,
            output_root=output_root,
            poll_interval_seconds=worker_poll_interval_seconds,
        ).start()
    return server


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Agentic operations trace UI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--config", default="configs/projects.yaml")
    parser.add_argument("--state-dir", default="state")
    parser.add_argument("--output-root", default="outputs/monitors")
    parser.add_argument("--auth-token-env", default="AGENTIC_TRACE_UI_TOKEN")
    parser.add_argument("--viewer-token-env", default="AGENTIC_TRACE_UI_VIEWER_TOKEN")
    parser.add_argument("--operator-token-env", default="AGENTIC_TRACE_UI_OPERATOR_TOKEN")
    parser.add_argument("--approver-token-env", default="AGENTIC_TRACE_UI_APPROVER_TOKEN")
    parser.add_argument("--admin-token-env", default="AGENTIC_TRACE_UI_ADMIN_TOKEN")
    parser.add_argument("--disable-auth", action="store_true")
    parser.add_argument("--disable-worker", action="store_true")
    parser.add_argument("--worker-poll-interval-seconds", type=float, default=1.5)
    parser.add_argument("--session-ttl-seconds", type=int, default=8 * 60 * 60)
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    auth_token = os.environ.get(args.auth_token_env, "")
    auth_role_tokens = {
        "viewer": os.environ.get(args.viewer_token_env, ""),
        "operator": os.environ.get(args.operator_token_env, ""),
        "approver": os.environ.get(args.approver_token_env, ""),
        "admin": os.environ.get(args.admin_token_env, ""),
    }
    if not args.disable_auth and not auth_token and not any(auth_role_tokens.values()):
        raise SystemExit(
            f"Trace UI auth token is required. Set {args.auth_token_env} "
            f"or one of {args.viewer_token_env}/{args.operator_token_env}/"
            f"{args.approver_token_env}/{args.admin_token_env} "
            "or start with --disable-auth for local development only."
        )
    server = build_server(
        host=args.host,
        port=args.port,
        config_path=args.config,
        state_dir=args.state_dir,
        output_root=args.output_root,
        quiet=args.quiet,
        auth_token=auth_token,
        auth_role_tokens=auth_role_tokens,
        auth_enabled=not args.disable_auth,
        session_ttl_seconds=args.session_ttl_seconds,
        start_worker=not args.disable_worker,
        worker_poll_interval_seconds=args.worker_poll_interval_seconds,
    )
    print(f"Trace UI listening on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        daemon = getattr(server, "job_daemon", None)
        if daemon:
            daemon.stop()
        server.server_close()


if __name__ == "__main__":
    main()
