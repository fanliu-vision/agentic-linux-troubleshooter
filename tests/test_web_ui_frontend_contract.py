from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

from web_ui.security import HIGH_RISK_CONFIRMATIONS


ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = ROOT / "web_ui" / "static" / "index.html"
APP_JS = ROOT / "web_ui" / "static" / "app.js"


class IdCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for key, value in attrs:
            if key == "id" and value:
                self.ids.add(value)


def _html_ids() -> set[str]:
    parser = IdCollector()
    parser.feed(INDEX_HTML.read_text(encoding="utf-8"))
    return parser.ids


def _app_js() -> str:
    return APP_JS.read_text(encoding="utf-8")


def test_browser_dom_bindings_resolve_to_existing_elements() -> None:
    app = _app_js()
    ids = _html_ids()

    referenced_ids = set(re.findall(r'document\.getElementById\("([^"]+)"\)', app))

    assert referenced_ids
    assert referenced_ids <= ids


def test_browser_controls_wire_required_click_and_submit_handlers() -> None:
    app = _app_js()
    required_handlers = [
        "els.loginForm.addEventListener(\"submit\", login)",
        "els.logoutButton.addEventListener(\"click\", logout)",
        "els.refreshButton.addEventListener(\"click\"",
        "els.connectLocalButton.addEventListener(\"click\"",
        "els.connectRemoteButton.addEventListener(\"click\"",
        "els.healthCheckButton.addEventListener(\"click\"",
        "els.opStartMonitor.addEventListener(\"click\"",
        "els.opStopMonitor.addEventListener(\"click\"",
        "els.opRefreshLogs.addEventListener(\"click\"",
        "els.opGenerateReport.addEventListener(\"click\"",
        "els.opDryRunRecovery.addEventListener(\"click\"",
        "els.opLiveApply.addEventListener(\"click\"",
        "els.opRollbackLatest.addEventListener(\"click\"",
        "els.eventFilter.addEventListener(\"input\"",
    ]

    for snippet in required_handlers:
        assert snippet in app


def test_browser_api_client_uses_same_origin_credentials_and_csrf_header() -> None:
    app = _app_js()

    assert "credentials: \"same-origin\"" in app
    assert "X-CSRF-Token" in app
    assert "state.auth?.csrf_token" in app
    assert "response.status === 401" in app
    assert "renderAuth()" in app


def test_browser_high_risk_confirmation_actions_match_server_contract() -> None:
    app = _app_js()
    block = re.search(
        r"const HIGH_RISK_ACTIONS = \{(?P<body>.*?)\};",
        app,
        flags=re.S,
    )
    assert block is not None
    client_actions = set(re.findall(r"^\s{2}([A-Za-z0-9_]+): \{", block.group("body"), re.M))

    assert client_actions == set(HIGH_RISK_CONFIRMATIONS)
    assert "window.confirm" not in app
    assert "confirmWord" in block.group("body")
    assert "impact" in block.group("body")
    for action in client_actions:
        assert "confirmation_action: pending.action" in app
        assert action in app


def test_browser_workflow_defaults_focus_actionable_events() -> None:
    app = _app_js()

    assert "function eventWorkflowRank(event)" in app
    assert "function defaultEventFingerprint()" in app
    assert "event.pending_approval" in app
    assert "ATTENTION_EVENT_STATUSES.has(status)" in app
    assert "HIGH_ATTENTION_SEVERITIES.has(severity)" in app
    assert "state.selectedFingerprint = defaultEventFingerprint();" in app
    assert "待处理事件" in INDEX_HTML.read_text(encoding="utf-8")


def test_browser_job_log_report_and_history_previews_are_explicit() -> None:
    app = _app_js()

    assert "自动刷新中" in app
    assert "ACTIVE_JOB_STATUSES.has(job.status)" in app
    assert "function reportPreviewText(content, status)" in app
    assert "REPORT_PREVIEW_LIMIT" in app
    assert "预览已截断" in app
    assert "function editArtifact(edit, backup)" in app
    assert "diff / backup" in app


def test_browser_detail_tabs_match_declared_dom_panes() -> None:
    app = _app_js()
    ids = _html_ids()
    block = re.search(
        r"const DETAIL_TABS = \[(?P<body>.*?)\];",
        app,
        flags=re.S,
    )
    assert block is not None
    tab_entries = re.findall(
        r'\["([^"]+)",\s*"([^"]+)",\s*"([^"]+)"\]',
        block.group("body"),
    )

    assert {
        "evidence",
        "policy",
        "plan",
        "approval",
        "execution",
        "reports",
        "audit",
    } <= {entry[0] for entry in tab_entries}
    for _, button_id, pane_id in tab_entries:
        assert button_id in ids
        assert pane_id in ids
