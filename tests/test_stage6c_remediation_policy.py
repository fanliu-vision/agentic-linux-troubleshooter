from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from detectors import ErrorEvent
from monitors.project_registry import PolicyConfig, ProjectConfig
from policies import RemediationPolicy


def make_project(
    auto_recover: bool = True,
    allow_auto_apply: list[str] | None = None,
    escalation_required: list[str] | None = None,
) -> ProjectConfig:
    return ProjectConfig(
        project_id="test",
        name="test",
        mode="local",
        project_dir=".",
        run_command="python app.py",
        policy=PolicyConfig(
            auto_recover=auto_recover,
            allow_auto_apply=allow_auto_apply or [],
            escalation_required=escalation_required or [],
        ),
    )


def make_event(event_type: str, issue_type: str, severity: str = "medium") -> ErrorEvent:
    return ErrorEvent(
        event_type=event_type,
        issue_type=issue_type,
        severity=severity,
        summary=f"test {event_type}",
        source="test",
        raw_excerpt="test",
        signature=f"test-{event_type}",
    )


def test_network_port_auto_recover_allowed() -> None:
    project = make_project(
        auto_recover=True,
        allow_auto_apply=["fix-network-1"],
        escalation_required=["disk", "slurm"],
    )
    event = make_event("network_port", "network_port")

    decision = RemediationPolicy().decide(event, project)

    assert decision.action == "auto_recover"
    assert decision.fix_id == "fix-network-1"


def test_gpu_oom_auto_recover_allowed_when_fix_is_allowed() -> None:
    project = make_project(
        auto_recover=True,
        allow_auto_apply=["fix-gpu-1"],
        escalation_required=["disk", "slurm"],
    )
    event = make_event("gpu_oom", "gpu", severity="high")

    decision = RemediationPolicy().decide(event, project)

    assert decision.action == "auto_recover"
    assert decision.fix_id == "fix-gpu-1"


def test_python_env_requires_explicit_allow_fix_id() -> None:
    project = make_project(
        auto_recover=True,
        allow_auto_apply=[],
        escalation_required=["disk", "slurm"],
    )
    event = make_event("python_env", "python_env")

    decision = RemediationPolicy().decide(event, project)

    assert decision.action == "manual_escalation"
    assert decision.fix_id == "fix-python-1"


def test_python_env_can_auto_recover_when_explicitly_allowed() -> None:
    project = make_project(
        auto_recover=True,
        allow_auto_apply=["fix-python-1"],
        escalation_required=["disk", "slurm"],
    )
    event = make_event("python_env", "python_env")

    decision = RemediationPolicy().decide(event, project)

    assert decision.action == "auto_recover"
    assert decision.fix_id == "fix-python-1"


def test_disk_full_must_escalate() -> None:
    project = make_project(
        auto_recover=True,
        allow_auto_apply=["fix-network-1", "fix-gpu-1"],
        escalation_required=["disk", "slurm"],
    )
    event = make_event("disk_full", "disk", severity="high")

    decision = RemediationPolicy().decide(event, project)

    assert decision.action == "manual_escalation"


def test_slurm_must_escalate() -> None:
    project = make_project(
        auto_recover=True,
        allow_auto_apply=["fix-network-1", "fix-gpu-1"],
        escalation_required=["disk", "slurm"],
    )
    event = make_event("slurm", "slurm", severity="high")

    decision = RemediationPolicy().decide(event, project)

    assert decision.action == "manual_escalation"


def test_auto_recover_disabled_escalates() -> None:
    project = make_project(
        auto_recover=False,
        allow_auto_apply=["fix-network-1"],
    )
    event = make_event("network_port", "network_port")

    decision = RemediationPolicy().decide(event, project)

    assert decision.action == "manual_escalation"


def main() -> None:
    test_network_port_auto_recover_allowed()
    test_gpu_oom_auto_recover_allowed_when_fix_is_allowed()
    test_python_env_requires_explicit_allow_fix_id()
    test_python_env_can_auto_recover_when_explicitly_allowed()
    test_disk_full_must_escalate()
    test_slurm_must_escalate()
    test_auto_recover_disabled_escalates()

    print("=" * 100)
    print("STAGE 6C REMEDIATION POLICY TEST PASSED")
    print("=" * 100)


if __name__ == "__main__":
    main()