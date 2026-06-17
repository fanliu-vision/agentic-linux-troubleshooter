from __future__ import annotations

import argparse
import sys
from pathlib import Path

from routers import classify_issue_dict, format_route_context
from agents.domain_agents import MultiAgentOrchestrator
from agents.report_agent import ReportAgent, LLMReportAgent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Stage-3 V1 Multi-Agent Linux Troubleshooting Assistant"
    )
    parser.add_argument(
        "question",
        nargs="*",
        help="User troubleshooting question, for example: 帮我分析 examples/logs/regression/08_complex_mixed_failure.log",
    )
    parser.add_argument(
        "--save",
        default="outputs/reports/last_multi_agent_report.md",
        help="Path to save the generated report.",
    )
    parser.add_argument(
        "--report-mode",
        choices=["rule", "llm", "auto"],
        default="auto",
        help="Report generation mode: rule, llm, or auto. Default: auto.",
    )
    parser.add_argument(
        "--llm-report-save",
        default="outputs/reports/last_multi_agent_llm_report.md",
        help="Path to save the LLM-generated report.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    user_question = " ".join(args.question).strip()
    if not user_question:
        print("请提供问题，例如：")
        print('python main_multi_agent.py "帮我分析 examples/logs/regression/08_complex_mixed_failure.log"')
        raise SystemExit(1)

    print("\n" + "=" * 100)
    print("Stage-3 V1 Multi-Agent Linux Troubleshooting Assistant")
    print("=" * 100)

    print("\n" + "=" * 100)
    print("用户问题")
    print("=" * 100)
    print(user_question)

    route = classify_issue_dict(user_question)
    route_context = format_route_context(route)

    print("\n" + "=" * 100)
    print("ManagerAgent 路由判断结果")
    print("=" * 100)
    print(route_context)

    orchestrator = MultiAgentOrchestrator(route)
    workflow_result = orchestrator.run()

    print("\n" + "=" * 100)
    print("子 Agent 执行摘要")
    print("=" * 100)
    for result in workflow_result["results"]:
        print(f"- {result.agent_name}: status={result.status}, issue_type={result.issue_type}, is_primary={result.is_primary}")
        print(f"  summary: {result.summary}")

    report = ""
    report_source = ""

    if args.report_mode == "rule":
        report_agent = ReportAgent()
        report = report_agent.build_report(
            route=workflow_result["route"],
            results=workflow_result["results"],
        )
        save_path = Path(args.save)
        report_source = "Rule ReportAgent"

    elif args.report_mode == "llm":
        llm_report_agent = LLMReportAgent()
        report = llm_report_agent.build_report(
            route=workflow_result["route"],
            results=workflow_result["results"],
        )
        save_path = Path(args.llm_report_save)
        report_source = "LLMReportAgent"

    else:
        # auto mode: try LLM first, then fallback to deterministic rule report
        try:
            llm_report_agent = LLMReportAgent()
            report = llm_report_agent.build_report(
                route=workflow_result["route"],
                results=workflow_result["results"],
            )
            save_path = Path(args.llm_report_save)
            report_source = "LLMReportAgent"
        except Exception as exc:
            print("\n" + "=" * 100)
            print("LLMReportAgent 生成失败，自动回退到规则 ReportAgent")
            print("=" * 100)
            print(f"错误类型：{type(exc).__name__}")
            print(f"错误信息：{exc}")

            report_agent = ReportAgent()
            report = report_agent.build_report(
                route=workflow_result["route"],
                results=workflow_result["results"],
            )
            save_path = Path(args.save)
            report_source = "Rule ReportAgent fallback"

    save_path.parent.mkdir(parents=True, exist_ok=True)
    save_path.write_text(report, encoding="utf-8")

    print("\n" + "=" * 100)
    print(f"最终多 Agent 报告（{report_source}）")
    print("=" * 100)
    print(report)

    print("\n" + "=" * 100)
    print(f"报告生成方式：{report_source}")
    print(f"报告已保存到：{save_path}")
    print("=" * 100)


if __name__ == "__main__":
    main()