from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from agents.multi_agent_orchestrator_v3 import MultiAgentOrchestratorV3
from agents.report_agent import ReportAgent
from routers import classify_issue_dict


QUESTION = "帮我分析 examples/logs/regression/08_complex_mixed_failure.log"


def _agent_names(workflow_result: dict) -> list[str]:
    return [result.agent_name for result in workflow_result["results"]]

def _executed_agent_names(workflow_result: dict) -> list[str]:
    """
    Agents with status != skipped are considered actually executed.
    Skipped AgentResult objects are kept only for report explanation.
    """
    return [
        result.agent_name
        for result in workflow_result["results"]
        if result.status != "skipped"
    ]


def _skipped_agent_names(workflow_result: dict) -> list[str]:
    return [
        result.agent_name
        for result in workflow_result["results"]
        if result.status == "skipped"
    ]


def test_minimal() -> None:
    route = classify_issue_dict(QUESTION)

    orchestrator = MultiAgentOrchestratorV3(route=route, agent_depth="minimal")
    workflow_result = orchestrator.run()

    names = _agent_names(workflow_result)
    executed_names = _executed_agent_names(workflow_result)
    skipped_names = _skipped_agent_names(workflow_result)

    print("all results:", names)
    print("executed:", executed_names)
    print("skipped:", skipped_names)

    assert "DynamicManagerAgent" in executed_names
    assert "LogDiagnosisAgent" in executed_names
    assert "GPUAgent" in executed_names

    # minimal 模式下这些 Agent 可以出现在 results 中，但必须是 skipped
    assert "DiskAgent" not in executed_names
    assert "PythonEnvAgent" not in executed_names
    assert "NetworkAgent" not in executed_names
    assert "SlurmAgent" not in executed_names

    assert "DiskAgent" in skipped_names
    assert "PythonEnvAgent" in skipped_names
    assert "NetworkAgent" in skipped_names
    assert "SlurmAgent" in skipped_names


def test_balanced() -> None:
    route = classify_issue_dict(QUESTION)

    orchestrator = MultiAgentOrchestratorV3(route=route, agent_depth="balanced")
    workflow_result = orchestrator.run()

    names = _agent_names(workflow_result)
    executed_names = _executed_agent_names(workflow_result)
    skipped_names = _skipped_agent_names(workflow_result)

    print("all results:", names)
    print("executed:", executed_names)
    print("skipped:", skipped_names)

    assert "DynamicManagerAgent" in executed_names
    assert "LogDiagnosisAgent" in executed_names
    assert "GPUAgent" in executed_names
    assert "SlurmAgent" in executed_names
    assert "DiskAgent" in executed_names
    assert "PythonEnvAgent" in executed_names

    # balanced 模式下 NetworkAgent 被判定为弱相关，应该跳过
    assert "NetworkAgent" not in executed_names
    assert "NetworkAgent" in skipped_names


def test_full() -> None:
    route = classify_issue_dict(QUESTION)

    orchestrator = MultiAgentOrchestratorV3(route=route, agent_depth="full")
    workflow_result = orchestrator.run()

    names = _agent_names(workflow_result)
    executed_names = _executed_agent_names(workflow_result)
    skipped_names = _skipped_agent_names(workflow_result)

    print("all results:", names)
    print("executed:", executed_names)
    print("skipped:", skipped_names)

    assert "DynamicManagerAgent" in executed_names
    assert "LogDiagnosisAgent" in executed_names
    assert "GPUAgent" in executed_names
    assert "DiskAgent" in executed_names
    assert "PythonEnvAgent" in executed_names
    assert "NetworkAgent" in executed_names
    assert "SlurmAgent" in executed_names

    assert "GPUAgent" not in skipped_names
    assert "DiskAgent" not in skipped_names
    assert "PythonEnvAgent" not in skipped_names
    assert "NetworkAgent" not in skipped_names
    assert "SlurmAgent" not in skipped_names

    report = ReportAgent().build_report(
        route=workflow_result["route"],
        results=workflow_result["results"],
    )

    assert "多 Agent Linux 排障报告" in report


def main() -> None:
    test_minimal()
    test_balanced()
    test_full()
    print("=" * 100)
    print("V3 MULTI-AGENT TEST PASSED")
    print("=" * 100)


if __name__ == "__main__":
    main()