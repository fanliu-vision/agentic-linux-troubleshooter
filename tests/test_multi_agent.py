from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from routers import classify_issue_dict, format_route_context
from agents.domain_agents import MultiAgentOrchestrator
from agents.report_agent import ReportAgent


def test_complex_mixed_log() -> None:
    question = "帮我分析 examples/logs/regression/08_complex_mixed_failure.log"

    print("=" * 100)
    print("TEST: Multi-Agent Complex Mixed Log")
    print("=" * 100)

    route = classify_issue_dict(question)
    print("[ROUTE]")
    print(format_route_context(route))

    assert route["primary_issue_type"] == "gpu"
    assert "disk" in route["all_detected_issue_types"]
    assert "python_env" in route["all_detected_issue_types"]
    assert "network_port" in route["all_detected_issue_types"]
    assert "slurm" in route["all_detected_issue_types"]

    orchestrator = MultiAgentOrchestrator(route)
    workflow_result = orchestrator.run()

    results = workflow_result["results"]
    agent_names = [r.agent_name for r in results]

    print("\n[AGENT RESULTS]")
    for result in results:
        print(f"- {result.agent_name}: {result.status}, {result.issue_type}, primary={result.is_primary}")

    required_agents = [
        "ManagerAgent",
        "LogDiagnosisAgent",
        "GPUAgent",
        "DiskAgent",
        "PythonEnvAgent",
        "NetworkAgent",
        "SlurmAgent",
    ]

    for agent_name in required_agents:
        assert agent_name in agent_names, f"Missing agent: {agent_name}"

    gpu_result = next(r for r in results if r.agent_name == "GPUAgent")
    assert gpu_result.is_primary is True
    assert gpu_result.status == "ok"

    report_agent = ReportAgent()
    report = report_agent.build_report(route=route, results=results)

    print("\n[REPORT]")
    print(report)

    assert "多 Agent Linux 排障报告" in report
    assert "主故障类型" in report
    assert "GPUAgent" in report
    assert "DiskAgent" in report
    assert "PythonEnvAgent" in report
    assert "NetworkAgent" in report
    assert "SlurmAgent" in report


def main() -> None:
    test_complex_mixed_log()
    print("=" * 100)
    print("MULTI-AGENT TEST PASSED")
    print("=" * 100)


if __name__ == "__main__":
    main()