from __future__ import annotations

import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from monitors.project_registry import ProjectRegistry
from web_ui.runtime_control import _parse_run_command
from web_ui.security import (
    AuthManager,
    PERMISSION_APPROVE,
    PERMISSION_LIVE_APPLY,
    PERMISSION_OPERATE,
    PERMISSION_READ,
    ROLE_ADMIN,
    ROLE_APPROVER,
    ROLE_OPERATOR,
    ROLE_VIEWER,
    confirmation_missing,
)


def test_auth_manager_login_session_and_csrf() -> None:
    manager = AuthManager(token="secret-token", enabled=True)

    failed = manager.login(token="wrong", operator="alice")
    assert failed.authenticated is False

    context = manager.login(token="secret-token", operator="alice@example.com")
    assert context.authenticated is True
    assert context.operator == "alice@example.com"
    assert context.role == ROLE_ADMIN
    assert PERMISSION_OPERATE in context.permissions
    assert manager.validate_csrf(context, context.csrf_token) is True
    assert manager.validate_csrf(context, "wrong") is False

    cookie = manager.session_cookie(context)
    assert "HttpOnly" in cookie
    assert "SameSite=Strict" in cookie
    assert manager.authenticate(cookie).operator == "alice@example.com"


def test_auth_manager_role_tokens_and_permissions() -> None:
    manager = AuthManager(
        token="legacy-admin",
        enabled=True,
        role_tokens={
            ROLE_VIEWER: "viewer-token",
            ROLE_OPERATOR: "operator-token",
            ROLE_APPROVER: "approver-token",
            ROLE_ADMIN: "admin-token",
        },
    )

    viewer = manager.login(token="viewer-token", operator="viewer")
    assert viewer.authenticated is True
    assert viewer.role == ROLE_VIEWER
    assert viewer.permissions == (PERMISSION_READ,)

    operator = manager.login(token="operator-token", operator="operator")
    assert operator.role == ROLE_OPERATOR
    assert PERMISSION_OPERATE in operator.permissions
    assert PERMISSION_APPROVE not in operator.permissions

    approver = manager.login(token="approver-token", operator="approver")
    assert approver.role == ROLE_APPROVER
    assert PERMISSION_APPROVE in approver.permissions
    assert PERMISSION_LIVE_APPLY in approver.permissions
    assert PERMISSION_OPERATE not in approver.permissions

    legacy = manager.login(token="legacy-admin", operator="admin")
    assert legacy.role == ROLE_ADMIN


def test_high_risk_confirmation_requires_exact_action() -> None:
    assert confirmation_missing("live_apply", {}) is True
    assert confirmation_missing(
        "live_apply",
        {"confirm": True, "confirmation_action": "rollback_latest"},
    ) is True
    assert confirmation_missing(
        "live_apply",
        {"confirm": True, "confirmation_action": "live_apply"},
    ) is False
    assert confirmation_missing("generate_report", {}) is False


def test_auth_manager_requires_token_unless_disabled() -> None:
    try:
        AuthManager(token="", enabled=True)
    except ValueError as exc:
        assert "trace_ui_auth_token_required" in str(exc)
    else:
        raise AssertionError("auth manager should require token")

    disabled = AuthManager(token="", enabled=False)
    assert disabled.authenticate("").authenticated is True


def test_project_registry_rejects_inline_ssh_secret() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        config_path = Path(tmp) / "projects.yaml"
        config_path.write_text(
            """
projects:
  - project_id: bad_ssh
    name: Bad SSH
    mode: remote
    ssh:
      user: lf
      host: localhost
      port: 22
      password: super-secret
""",
            encoding="utf-8",
        )

        try:
            ProjectRegistry(str(config_path)).load_all()
        except ValueError as exc:
            assert "ssh_credentials_must_not_be_in_project_config" in str(exc)
        else:
            raise AssertionError("inline ssh password should be rejected")


def test_run_command_allowlist_rejects_shell_and_non_python() -> None:
    assert _parse_run_command("python3 run_service.py --config config.json")["ok"] is True
    assert _parse_run_command("python -m app.worker --config config.json")["ok"] is True
    assert _parse_run_command("bash run.sh")["ok"] is False
    assert _parse_run_command("python app.py && rm -rf /")["ok"] is False
