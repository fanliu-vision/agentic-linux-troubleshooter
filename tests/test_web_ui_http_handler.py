from __future__ import annotations

import json
import tempfile
from pathlib import Path

from monitors.report_index_store import REPORT_TYPE_AUTO_RECOVERY, ReportIndexStore
from monitors.recovery_history_store import RecoveryHistoryStore
from monitors.trace_store import (
    APPROVAL_STATUS_APPROVED,
    APPROVAL_STATUS_EXPIRED,
    APPROVAL_STATUS_REJECTED,
)
from tests.web_ui_test_helpers import (
    call_handler,
    login_headers,
    make_server_context,
    seed_trace_and_pending_approval,
    write_project_config,
)
from web_ui.operation_runner import (
    OP_GENERATE_REPORT,
    OP_LIVE_APPLY,
    OP_ROLLBACK_LATEST,
    OP_START_MONITOR,
)
from web_ui.runtime_control import (
    JOB_STATUS_CANCELED,
    JOB_STATUS_FAILED,
    JOB_STATUS_QUEUED,
    JobStore,
)


def _setup_server(tmp: str, *, project_id: str = "http_ui_project"):
    root = Path(tmp)
    project_dir = root / "project"
    project_dir.mkdir()
    config_path = root / "projects.yaml"
    state_dir = root / "state"
    output_root = root / "outputs"
    write_project_config(
        config_path,
        project_id=project_id,
        project_dir=str(project_dir),
    )
    server = make_server_context(
        config_path=config_path,
        state_dir=state_dir,
        output_root=output_root,
    )
    return server, root, project_id


def test_http_auth_login_unauthorized_and_csrf_guard() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        server, _, project_id = _setup_server(tmp)

        unauthenticated = call_handler(server, "GET", "/api/projects")
        assert unauthenticated.status == 401
        assert unauthenticated.json()["error"] == "auth_required"

        login = call_handler(
            server,
            "POST",
            "/api/auth/login",
            body={"operator": "alice@example.com", "token": "secret-token"},
        )
        assert login.status == 200
        assert "HttpOnly" in login.headers["Set-Cookie"]
        assert login.json()["auth"]["operator"] == "alice@example.com"

        cookie = login.headers["Set-Cookie"].split(";", 1)[0]
        projects = call_handler(
            server,
            "GET",
            "/api/projects",
            headers={"Cookie": cookie},
        )
        assert projects.status == 200
        assert projects.json()["projects"][0]["project_id"] == project_id

        csrf_blocked = call_handler(
            server,
            "POST",
            f"/api/projects/{project_id}/operations/{OP_GENERATE_REPORT}",
            headers={"Cookie": cookie},
            body={},
        )
        assert csrf_blocked.status == 403
        assert csrf_blocked.json()["error"] == "csrf_required"


def test_http_high_risk_operation_requires_412_confirmation() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        server, _, project_id = _setup_server(tmp)
        headers = login_headers(server)

        blocked = call_handler(
            server,
            "POST",
            f"/api/projects/{project_id}/operations/{OP_LIVE_APPLY}",
            headers=headers,
            body={},
        )
        assert blocked.status == 412
        assert blocked.json() == {
            "error": "confirmation_required",
            "action": OP_LIVE_APPLY,
        }

        confirmed = call_handler(
            server,
            "POST",
            f"/api/projects/{project_id}/operations/{OP_LIVE_APPLY}",
            headers=headers,
            body={"confirm": True, "confirmation_action": OP_LIVE_APPLY},
        )
        assert confirmed.status == 200
        assert confirmed.json()["job"]["status"] == JOB_STATUS_QUEUED
        assert confirmed.json()["job"]["action"] == OP_LIVE_APPLY


def test_http_role_permissions_and_job_audit_metadata() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        server, _, project_id = _setup_server(tmp)
        server.auth_manager = server.auth_manager.__class__(
            token="legacy-admin-token",
            role_tokens={
                "viewer": "viewer-token",
                "operator": "operator-token",
                "approver": "approver-token",
                "admin": "admin-token",
            },
        )

        viewer_headers = login_headers(server, operator="viewer", token="viewer-token")
        projects = call_handler(
            server,
            "GET",
            "/api/projects",
            headers={"Cookie": viewer_headers["Cookie"]},
        )
        assert projects.status == 200
        assert projects.json()["projects"][0]["project_id"] == project_id

        viewer_blocked = call_handler(
            server,
            "POST",
            f"/api/projects/{project_id}/operations/{OP_GENERATE_REPORT}",
            headers=viewer_headers,
            body={},
        )
        assert viewer_blocked.status == 403
        assert viewer_blocked.json()["error"] == "permission_denied"
        assert viewer_blocked.json()["required_permission"] == "operate"

        operator_headers = login_headers(server, operator="ops@example.com", token="operator-token")
        generated = call_handler(
            server,
            "POST",
            f"/api/projects/{project_id}/operations/{OP_GENERATE_REPORT}",
            headers={**operator_headers, "User-Agent": "rbac-test"},
            body={},
        )
        assert generated.status == 200
        job = generated.json()["job"]
        assert job["role"] == "operator"
        assert job["request_audit"]["operator"] == "ops@example.com"
        assert job["request_audit"]["role"] == "operator"
        assert job["request_audit"]["action"] == OP_GENERATE_REPORT
        assert job["request_audit"]["project_id"] == project_id
        assert job["request_audit"]["user_agent"] == "rbac-test"
        assert "operator-token" not in json.dumps(job)

        operator_live_apply = call_handler(
            server,
            "POST",
            f"/api/projects/{project_id}/operations/{OP_LIVE_APPLY}",
            headers=operator_headers,
            body={"confirm": True, "confirmation_action": OP_LIVE_APPLY},
        )
        assert operator_live_apply.status == 403
        assert operator_live_apply.json()["required_permission"] == "live_apply"

        approver_headers = login_headers(server, operator="approver", token="approver-token")
        approver_start = call_handler(
            server,
            "POST",
            f"/api/projects/{project_id}/operations/{OP_START_MONITOR}",
            headers=approver_headers,
            body={},
        )
        assert approver_start.status == 403
        assert approver_start.json()["required_permission"] == "operate"

        approver_live_apply = call_handler(
            server,
            "POST",
            f"/api/projects/{project_id}/operations/{OP_LIVE_APPLY}",
            headers=approver_headers,
            body={"confirm": True, "confirmation_action": OP_LIVE_APPLY},
        )
        assert approver_live_apply.status == 200
        assert approver_live_apply.json()["job"]["role"] == "approver"


def test_http_approval_approve_reject_and_expire_paths() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        server, root, project_id = _setup_server(tmp)
        headers = login_headers(server)

        _, approve_request = seed_trace_and_pending_approval(
            state_dir=root / "state",
            project_id=project_id,
            fingerprint="approve-fp",
        )
        approved = call_handler(
            server,
            "POST",
            f"/api/projects/{project_id}/approvals/{approve_request['request_id']}/approve",
            headers=headers,
            body={"confirm": True, "confirmation_action": "approval_approve"},
        )
        assert approved.status == 200
        body = approved.json()
        assert body["approval"]["status"] == APPROVAL_STATUS_APPROVED
        assert body["approved_recovery"]["job"]["action"] == "approved_recovery_job"
        assert body["approved_recovery"]["job"]["status"] == JOB_STATUS_QUEUED

        _, reject_request = seed_trace_and_pending_approval(
            state_dir=root / "state",
            project_id=project_id,
            fingerprint="reject-fp",
        )
        rejected = call_handler(
            server,
            "POST",
            f"/api/projects/{project_id}/approvals/{reject_request['request_id']}/reject",
            headers=headers,
            body={"comment": "not safe enough now"},
        )
        assert rejected.status == 200
        assert rejected.json()["approval"]["status"] == APPROVAL_STATUS_REJECTED
        assert rejected.json()["event"]["summary"]["status"] == "approval_rejected"

        _, expire_request = seed_trace_and_pending_approval(
            state_dir=root / "state",
            project_id=project_id,
            fingerprint="expire-fp",
        )
        expired = call_handler(
            server,
            "POST",
            f"/api/projects/{project_id}/approvals/{expire_request['request_id']}/expire",
            headers=headers,
            body={"comment": "stale request"},
        )
        assert expired.status == 200
        assert expired.json()["approval"]["status"] == APPROVAL_STATUS_EXPIRED
        assert expired.json()["event"]["summary"]["status"] == "approval_expired"


def test_http_job_cancel_retry_and_high_risk_retry_confirmation() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        server, root, project_id = _setup_server(tmp)
        headers = login_headers(server)
        store = JobStore(project_id=project_id, state_dir=str(root / "state"))

        queued = store.create(action=OP_GENERATE_REPORT, operator="tester")
        canceled = call_handler(
            server,
            "POST",
            f"/api/projects/{project_id}/jobs/{queued['job_id']}/cancel",
            headers=headers,
            body={},
        )
        assert canceled.status == 200
        assert canceled.json()["job"]["status"] == JOB_STATUS_CANCELED

        failed = store.create(action=OP_GENERATE_REPORT, operator="tester")
        store.complete(
            failed["job_id"],
            status=JOB_STATUS_FAILED,
            runtime_status="connected",
            summary="failed",
            result={"failure_reason": "test"},
        )
        retried = call_handler(
            server,
            "POST",
            f"/api/projects/{project_id}/jobs/{failed['job_id']}/retry",
            headers=headers,
            body={},
        )
        assert retried.status == 200
        assert retried.json()["job"]["status"] == JOB_STATUS_QUEUED
        assert retried.json()["job"]["payload"]["retry_of"] == failed["job_id"]

        high_risk = store.create(action=OP_LIVE_APPLY, operator="tester")
        store.complete(
            high_risk["job_id"],
            status=JOB_STATUS_FAILED,
            runtime_status="connected",
            summary="failed",
            result={"failure_reason": "test"},
        )
        blocked = call_handler(
            server,
            "POST",
            f"/api/projects/{project_id}/jobs/{high_risk['job_id']}/retry",
            headers=headers,
            body={},
        )
        assert blocked.status == 412
        assert blocked.json()["action"] == "job_retry"

        paged = call_handler(
            server,
            "GET",
            f"/api/projects/{project_id}/jobs?limit=1&offset=1",
            headers={"Cookie": headers["Cookie"]},
        )
        assert paged.status == 200
        assert len(paged.json()["jobs"]) == 1


def test_http_report_detail_recovery_history_rollback_and_static_path_safety() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        server, root, project_id = _setup_server(tmp)
        headers = login_headers(server)

        report = ReportIndexStore(
            project_id=project_id,
            state_dir=str(root / "state"),
        ).register_text_report(
            content="# Recovery Report\n\nRecovered safely.",
            report_type=REPORT_TYPE_AUTO_RECOVERY,
            fingerprint="report-fp",
            event_type="network_port",
            job_id="job-1",
        )
        ReportIndexStore(
            project_id=project_id,
            state_dir=str(root / "state"),
        ).register_text_report(
            content="# Other Report\n\nSecond page.",
            report_type=REPORT_TYPE_AUTO_RECOVERY,
            fingerprint="report-fp-2",
            event_type="network_port",
            job_id="job-2",
        )
        detail = call_handler(
            server,
            "GET",
            f"/api/projects/{project_id}/reports/{report['report_id']}",
            headers={"Cookie": headers["Cookie"]},
        )
        assert detail.status == 200
        assert detail.json()["content_status"] == "ok"
        assert "Recovered safely" in detail.json()["content"]

        paged_reports = call_handler(
            server,
            "GET",
            f"/api/projects/{project_id}/reports?limit=1&offset=1",
            headers={"Cookie": headers["Cookie"]},
        )
        assert paged_reports.status == 200
        assert len(paged_reports.json()["reports"]) == 1

        RecoveryHistoryStore(project_id=project_id, state_dir=str(root / "state")).register_applied(
            fix_id="fix-network-1",
            edits=[
                {
                    "field_path": "server.port",
                    "old_value": 8000,
                    "new_value": 8001,
                    "config_path": "config.json",
                }
            ],
            record_path=str(root / "outputs" / project_id / "applied_fixes.json"),
            record_index=0,
            fingerprint="history-fp",
            event_type="network_port",
            job_id="job-history",
            mode="local",
        )
        paged_history = call_handler(
            server,
            "GET",
            f"/api/projects/{project_id}/recovery-history?limit=1&offset=0",
            headers={"Cookie": headers["Cookie"]},
        )
        assert paged_history.status == 200
        assert len(paged_history.json()["records"]) == 1

        rollback_blocked = call_handler(
            server,
            "POST",
            f"/api/projects/{project_id}/recovery-history/target-identity/rollback",
            headers=headers,
            body={},
        )
        assert rollback_blocked.status == 412
        assert rollback_blocked.json()["action"] == "recovery_history_rollback"

        rollback = call_handler(
            server,
            "POST",
            f"/api/projects/{project_id}/recovery-history/target-identity/rollback",
            headers=headers,
            body={
                "confirm": True,
                "confirmation_action": "recovery_history_rollback",
            },
        )
        assert rollback.status == 200
        assert rollback.json()["job"]["action"] == OP_ROLLBACK_LATEST
        assert rollback.json()["job"]["payload"]["target_identity"] == "target-identity"

        css = call_handler(server, "GET", "/styles.css")
        assert css.status == 200
        assert css.headers["Content-Type"] == "text/css"

        unsafe = call_handler(server, "GET", "/../server.py")
        assert unsafe.status == 400
        assert json.loads(unsafe.body.decode("utf-8"))["error"] == "unsafe_static_path"
