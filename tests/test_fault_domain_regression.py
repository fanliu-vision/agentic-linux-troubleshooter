from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from detectors import ErrorEventDetector
from monitors.project_registry import PolicyConfig, ProjectConfig
from policies import RemediationPolicy


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "regression_logs"
EXPECTED_CASES_PATH = FIXTURE_DIR / "expected_cases.json"


def load_expected_cases() -> list[dict[str, Any]]:
    return json.loads(EXPECTED_CASES_PATH.read_text(encoding="utf-8"))


def make_policy_project() -> ProjectConfig:
    return ProjectConfig(
        project_id="fault_regression",
        name="Fault Regression",
        mode="local",
        project_dir=".",
        run_command="python app.py",
        policy=PolicyConfig(
            auto_recover=True,
            allow_auto_apply=[
                "fix-network-1",
                "fix-gpu-1",
                "fix-cache-1",
                "fix-optional-dep-1",
                "fix-worker-1",
                "fix-python-1",
            ],
            escalation_required=[],
        ),
    )


def assert_expected_policy_action(case: dict[str, Any], event: Any) -> None:
    expected_action = case.get("expected_action")
    if expected_action == "policy_escalation_only":
        decision = RemediationPolicy().decide(event, make_policy_project())
        assert decision.action == "manual_escalation"
        assert not decision.is_auto_recover
    elif expected_action == "policy_requires_explicit_allow":
        project = make_policy_project()
        project.policy.allow_auto_apply = []
        decision = RemediationPolicy().decide(event, project)
        assert decision.action == "manual_escalation"
        assert not decision.is_auto_recover
    elif expected_action == "policy_report_only":
        decision = RemediationPolicy().decide(event, make_policy_project())
        assert decision.action == "report_only"
        assert not decision.is_auto_recover
    elif expected_action == "policy_auto_recover":
        decision = RemediationPolicy().decide(event, make_policy_project())
        assert decision.action == "auto_recover"
        assert decision.is_auto_recover


@pytest.mark.parametrize(
    "case",
    load_expected_cases(),
    ids=lambda case: case["case_id"],
)
def test_fault_domain_regression_case(case: dict[str, Any]) -> None:
    log_path = FIXTURE_DIR / case["log_file"]
    text = log_path.read_text(encoding="utf-8")

    events = ErrorEventDetector().detect(text, source=f"fixture:{case['log_file']}")
    expected_event_type = case["expected_event_type"]

    if expected_event_type is None:
        assert events == []
        return

    matching_events = [
        event for event in events if event.event_type == expected_event_type
    ]

    assert matching_events, [event.event_type for event in events]

    event = matching_events[0]
    expected_issue_type = case.get("expected_issue_type")
    if expected_issue_type is not None:
        assert event.issue_type == expected_issue_type

    assert event.signature
    assert event.fingerprint
    assert_expected_policy_action(case, event)
