from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from detectors import ErrorEvent
from monitors.project_registry import PolicyConfig, ProjectConfig
from policies import RemediationPolicy


def make_project() -> ProjectConfig:
    return ProjectConfig(
        project_id="enterprise_policy",
        name="Enterprise Policy",
        mode="local",
        project_dir=".",
        run_command="python app.py",
        policy=PolicyConfig(
            auto_recover=True,
            allow_auto_apply=[
                "fix-network-1",
                "fix-gpu-1",
                "fix-python-1",
            ],
            escalation_required=[],
        ),
    )


def make_event(event_type: str, issue_type: str) -> ErrorEvent:
    return ErrorEvent(
        event_type=event_type,
        issue_type=issue_type,
        severity="high",
        summary=f"test {event_type}",
        source="test",
        raw_excerpt="test",
        signature=f"test-{event_type}",
    )


@pytest.mark.parametrize(
    ("event_type", "issue_type"),
    [
        ("process_crash", "process"),
        ("container_k8s", "container_k8s"),
        ("host_resource", "host_resource"),
        ("network_connectivity", "network_connectivity"),
        ("dependency_service", "dependency_service"),
        ("config_error", "config"),
        ("auth_cert", "auth_cert"),
    ],
)
def test_enterprise_domains_manual_escalation(event_type: str, issue_type: str) -> None:
    decision = RemediationPolicy().decide(
        make_event(event_type, issue_type),
        make_project(),
    )

    assert decision.action == "manual_escalation"
    assert decision.fix_id == ""
    assert not decision.is_auto_recover


def test_network_port_and_gpu_auto_recovery_remain_unchanged() -> None:
    policy = RemediationPolicy()
    project = make_project()

    network_decision = policy.decide(
        make_event("network_port", "network_port"),
        project,
    )
    gpu_decision = policy.decide(make_event("gpu_oom", "gpu"), project)

    assert network_decision.action == "auto_recover"
    assert network_decision.fix_id == "fix-network-1"
    assert network_decision.is_auto_recover
    assert gpu_decision.action == "auto_recover"
    assert gpu_decision.fix_id == "fix-gpu-1"
    assert gpu_decision.is_auto_recover


@pytest.mark.parametrize(
    ("event_type", "issue_type"),
    [
        ("disk_full", "disk"),
        ("slurm", "slurm"),
        ("process_kill", "process"),
        ("permission_denied", "permission"),
    ],
)
def test_existing_escalation_domains_remain_unchanged(
    event_type: str,
    issue_type: str,
) -> None:
    decision = RemediationPolicy().decide(
        make_event(event_type, issue_type),
        make_project(),
    )

    assert decision.action == "manual_escalation"
    assert not decision.is_auto_recover
