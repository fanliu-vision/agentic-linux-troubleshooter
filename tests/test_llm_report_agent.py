from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

from routers import classify_issue_dict
from agents.domain_agents import MultiAgentOrchestrator
from agents.report_agent import LLMReportAgent


def main() -> None:
    load_dotenv()

    if not os.getenv("DEEPSEEK_API_KEY"):
        print("未检测到 DEEPSEEK_API_KEY，跳过 LLMReportAgent 测试。")
        return

    question = "帮我分析 examples/logs/regression/08_complex_mixed_failure.log"

    route = classify_issue_dict(question)
    orchestrator = MultiAgentOrchestrator(route)
    workflow_result = orchestrator.run()

    report_agent = LLMReportAgent()
    report = report_agent.build_report(
        route=workflow_result["route"],
        results=workflow_result["results"],
    )

    print("=" * 100)
    print("LLM REPORT")
    print("=" * 100)
    print(report)

    assert "多 Agent Linux 排障报告" in report
    assert "主故障" in report
    assert "HIP" in report or "GPU" in report or "DCU" in report


if __name__ == "__main__":
    main()