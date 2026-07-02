from __future__ import annotations

import hmac
import re
import secrets
import time
from dataclasses import dataclass
from http.cookies import SimpleCookie
from typing import Any


AUTH_COOKIE_NAME = "agentic_trace_session"
DEFAULT_SESSION_TTL_SECONDS = 8 * 60 * 60

ROLE_VIEWER = "viewer"
ROLE_OPERATOR = "operator"
ROLE_APPROVER = "approver"
ROLE_ADMIN = "admin"

PERMISSION_READ = "read"
PERMISSION_OPERATE = "operate"
PERMISSION_APPROVE = "approve"
PERMISSION_LIVE_APPLY = "live_apply"
PERMISSION_ROLLBACK = "rollback"
PERMISSION_RETRY_HIGH_RISK = "retry_high_risk"
PERMISSION_ADMIN = "admin"

ROLE_PERMISSIONS: dict[str, tuple[str, ...]] = {
    ROLE_VIEWER: (PERMISSION_READ,),
    ROLE_OPERATOR: (PERMISSION_READ, PERMISSION_OPERATE),
    ROLE_APPROVER: (
        PERMISSION_READ,
        PERMISSION_APPROVE,
        PERMISSION_LIVE_APPLY,
        PERMISSION_ROLLBACK,
        PERMISSION_RETRY_HIGH_RISK,
    ),
    ROLE_ADMIN: (
        PERMISSION_READ,
        PERMISSION_OPERATE,
        PERMISSION_APPROVE,
        PERMISSION_LIVE_APPLY,
        PERMISSION_ROLLBACK,
        PERMISSION_RETRY_HIGH_RISK,
        PERMISSION_ADMIN,
    ),
}

ROLE_STRENGTH = {
    ROLE_VIEWER: 10,
    ROLE_OPERATOR: 20,
    ROLE_APPROVER: 30,
    ROLE_ADMIN: 40,
}

HIGH_RISK_CONFIRMATIONS = {
    "approval_approve": "approval_approve",
    "live_apply": "live_apply",
    "rollback_latest": "rollback_latest",
    "recovery_history_rollback": "recovery_history_rollback",
    "job_retry": "job_retry",
}


@dataclass(frozen=True)
class AuthContext:
    authenticated: bool
    operator: str = ""
    role: str = ROLE_VIEWER
    permissions: tuple[str, ...] = ROLE_PERMISSIONS[ROLE_VIEWER]
    session_id: str = ""
    csrf_token: str = ""
    auth_required: bool = True


class AuthManager:
    def __init__(
        self,
        *,
        token: str = "",
        role_tokens: dict[str, str] | None = None,
        enabled: bool = True,
        session_ttl_seconds: int = DEFAULT_SESSION_TTL_SECONDS,
    ) -> None:
        self.enabled = bool(enabled)
        self.token = str(token or "")
        self.role_tokens = _normalize_role_tokens(role_tokens or {})
        self.session_ttl_seconds = int(session_ttl_seconds)
        self._sessions: dict[str, dict[str, Any]] = {}

        if self.enabled and not self.token and not self.role_tokens:
            raise ValueError("trace_ui_auth_token_required")

    def authenticate(self, cookie_header: str = "") -> AuthContext:
        if not self.enabled:
            return AuthContext(
                authenticated=True,
                operator="local-dev",
                role=ROLE_ADMIN,
                permissions=ROLE_PERMISSIONS[ROLE_ADMIN],
                auth_required=False,
            )

        session_id = _session_id_from_cookie(cookie_header)
        if not session_id:
            return AuthContext(authenticated=False, auth_required=True)

        session = self._sessions.get(session_id) or {}
        if not session:
            return AuthContext(authenticated=False, auth_required=True)

        if float(session.get("expires_at", 0)) <= time.time():
            self._sessions.pop(session_id, None)
            return AuthContext(authenticated=False, auth_required=True)

        return AuthContext(
            authenticated=True,
            operator=str(session.get("operator", "")),
            role=_normalize_role(str(session.get("role", ROLE_VIEWER))),
            permissions=permissions_for_role(str(session.get("role", ROLE_VIEWER))),
            session_id=session_id,
            csrf_token=str(session.get("csrf_token", "")),
            auth_required=True,
        )

    def login(self, *, token: str, operator: str) -> AuthContext:
        if not self.enabled:
            return AuthContext(
                authenticated=True,
                operator=_sanitize_operator(operator) or "local-dev",
                role=ROLE_ADMIN,
                permissions=ROLE_PERMISSIONS[ROLE_ADMIN],
                auth_required=False,
            )

        role = self._role_for_token(str(token or ""))
        if not role:
            return AuthContext(authenticated=False, auth_required=True)

        session_id = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        operator_name = _sanitize_operator(operator) or "operator"
        self._sessions[session_id] = {
            "operator": operator_name,
            "role": role,
            "csrf_token": csrf_token,
            "created_at": time.time(),
            "expires_at": time.time() + self.session_ttl_seconds,
        }
        return AuthContext(
            authenticated=True,
            operator=operator_name,
            role=role,
            permissions=permissions_for_role(role),
            session_id=session_id,
            csrf_token=csrf_token,
            auth_required=True,
        )

    def logout(self, session_id: str) -> None:
        if session_id:
            self._sessions.pop(session_id, None)

    def validate_csrf(self, context: AuthContext, token: str) -> bool:
        if not self.enabled:
            return True
        if not context.authenticated or not context.csrf_token:
            return False
        return hmac.compare_digest(context.csrf_token, str(token or ""))

    def session_cookie(self, context: AuthContext) -> str:
        if not self.enabled or not context.session_id:
            return ""
        return (
            f"{AUTH_COOKIE_NAME}={context.session_id}; "
            "Path=/; HttpOnly; SameSite=Strict; "
            f"Max-Age={max(1, self.session_ttl_seconds)}"
        )

    @staticmethod
    def clear_cookie() -> str:
        return (
            f"{AUTH_COOKIE_NAME}=; Path=/; HttpOnly; SameSite=Strict; "
            "Max-Age=0"
        )

    def _role_for_token(self, token: str) -> str:
        matches: list[str] = []
        if self.token and hmac.compare_digest(token, self.token):
            matches.append(ROLE_ADMIN)
        for role, role_token in self.role_tokens.items():
            if role_token and hmac.compare_digest(token, role_token):
                matches.append(role)
        if not matches:
            return ""
        return sorted(matches, key=lambda item: ROLE_STRENGTH[item], reverse=True)[0]


def auth_payload(context: AuthContext) -> dict[str, Any]:
    return {
        "authenticated": bool(context.authenticated),
        "operator": context.operator,
        "role": context.role,
        "permissions": list(context.permissions),
        "csrf_token": context.csrf_token,
        "auth_required": bool(context.auth_required),
    }


def permissions_for_role(role: str) -> tuple[str, ...]:
    return ROLE_PERMISSIONS.get(_normalize_role(role), ROLE_PERMISSIONS[ROLE_VIEWER])


def has_permission(context: AuthContext, permission: str) -> bool:
    return permission in set(context.permissions)


def confirmation_missing(action: str, body: dict[str, Any]) -> bool:
    expected = HIGH_RISK_CONFIRMATIONS.get(action)
    if not expected:
        return False
    return not (
        body.get("confirm") is True
        and str(body.get("confirmation_action", "")) == expected
    )


def _session_id_from_cookie(cookie_header: str) -> str:
    if not cookie_header:
        return ""
    cookie = SimpleCookie()
    try:
        cookie.load(cookie_header)
    except Exception:
        return ""
    morsel = cookie.get(AUTH_COOKIE_NAME)
    return str(morsel.value) if morsel is not None else ""


def _sanitize_operator(operator: str) -> str:
    text = str(operator or "").strip()
    text = re.sub(r"[^A-Za-z0-9_.@-]+", "_", text)
    return text[:80]


def _normalize_role(role: str) -> str:
    text = str(role or "").strip().lower()
    if text in ROLE_PERMISSIONS:
        return text
    return ROLE_VIEWER


def _normalize_role_tokens(raw: dict[str, str]) -> dict[str, str]:
    tokens: dict[str, str] = {}
    for role, token in raw.items():
        normalized = _normalize_role(role)
        value = str(token or "")
        if normalized in ROLE_PERMISSIONS and value:
            tokens[normalized] = value
    return tokens
