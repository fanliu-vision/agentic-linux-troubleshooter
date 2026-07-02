from __future__ import annotations

import threading
import os
from pathlib import Path
from typing import Iterator

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import Page, expect

from tests.web_ui_test_helpers import seed_trace_and_pending_approval, write_project_config
from web_ui.server import build_server


ROOT = Path(__file__).resolve().parents[2]
BASELINE_DIR = ROOT / "tests" / "browser" / "baselines"
VIEWPORTS = [
    {"width": 1440, "height": 900},
    {"width": 1280, "height": 800},
    {"width": 390, "height": 844},
]


@pytest.fixture()
def trace_ui_url(tmp_path: Path) -> Iterator[str]:
    project_id = "browser_trace_ui"
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    config_path = tmp_path / "projects.yaml"
    state_dir = tmp_path / "state"
    output_root = tmp_path / "outputs"
    write_project_config(
        config_path,
        project_id=project_id,
        project_dir=str(project_dir),
    )
    seed_trace_and_pending_approval(
        state_dir=state_dir,
        project_id=project_id,
        fingerprint="browser-network-port-fp",
    )

    server = build_server(
        host="127.0.0.1",
        port=0,
        config_path=str(config_path),
        state_dir=str(state_dir),
        output_root=str(output_root),
        quiet=True,
        auth_token="admin-token",
        auth_role_tokens={
            "viewer": "viewer-token",
            "operator": "operator-token",
            "approver": "approver-token",
        },
        auth_enabled=True,
        start_worker=False,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def login(page: Page, url: str, *, operator: str, token: str) -> None:
    page.goto(url)
    expect(page.locator("#authPanel")).to_be_visible()
    page.fill("#operatorInput", operator)
    page.fill("#tokenInput", token)
    page.click("#loginButton")
    expect(page.locator("#appShell")).to_be_visible()


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args: dict) -> dict:
    args = list(browser_type_launch_args.get("args") or [])
    args.extend([
        "--disable-setuid-sandbox",
        "--disable-seccomp-filter-sandbox",
        "--no-zygote",
    ])
    return {
        **browser_type_launch_args,
        "args": args,
        "chromium_sandbox": False,
    }


@pytest.mark.browser
@pytest.mark.e2e
def test_trace_ui_login_role_permissions_and_responsive_screens(
    page: Page,
    trace_ui_url: str,
    tmp_path: Path,
) -> None:
    login(page, trace_ui_url, operator="viewer@example.com", token="viewer-token")

    expect(page.locator("#operatorBadge")).to_contain_text("viewer")
    expect(page.locator("#opGenerateReport")).to_be_disabled()
    expect(page.locator("#opLiveApply")).to_be_disabled()
    expect(page.locator("#detailTabEvidence")).to_be_visible()

    for viewport in VIEWPORTS:
        page.set_viewport_size(viewport)
        expect(page.locator("#appShell")).to_be_visible()
        assert page.evaluate("document.body.innerText.length") > 0
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 2")
        capture_or_compare_screenshot(
            page,
            tmp_path,
            f"trace_ui_viewer_{viewport['width']}x{viewport['height']}.png",
        )


@pytest.mark.browser
@pytest.mark.e2e
def test_trace_ui_operator_can_queue_safe_ui_job(
    page: Page,
    trace_ui_url: str,
) -> None:
    login(page, trace_ui_url, operator="operator@example.com", token="operator-token")

    expect(page.locator("#opGenerateReport")).to_be_enabled()
    expect(page.locator("#opLiveApply")).to_be_disabled()
    page.click("#opGenerateReport")
    expect(page.locator("#operationStatus")).to_contain_text("生成报告")


def capture_or_compare_screenshot(page: Page, tmp_path: Path, name: str) -> None:
    screenshot = tmp_path / name
    page.screenshot(path=str(screenshot), full_page=True)
    assert screenshot.exists()

    baseline = BASELINE_DIR / name
    if os.environ.get("AGENTIC_UPDATE_BROWSER_BASELINES") == "1":
        baseline.parent.mkdir(parents=True, exist_ok=True)
        baseline.write_bytes(screenshot.read_bytes())
        return

    if baseline.exists():
        assert screenshot.read_bytes() == baseline.read_bytes()
