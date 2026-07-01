from __future__ import annotations

import argparse
from pathlib import Path

from agents.agent_protocol import AgentDepth
from agents.multi_agent_orchestrator_v3 import MultiAgentOrchestratorV3
from agents.report_agent import LLMReportAgent, ReportAgent
from routers import classify_issue_dict, format_route_context


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Legacy / experimental Stage-3 V3 Dynamic Multi-Agent Linux Troubleshooting CLI "
            "(not the monitor/daemon entry)."
        )
    )
    parser.add_argument(
        "question",
        nargs="*",
        help="User troubleshooting question, for example: 帮我分析 examples/logs/regression/08_complex_mixed_failure.log",
    )
    parser.add_argument(
        "--agent-depth",
        choices=["minimal", "balanced", "full"],
        default="balanced",
        help="Multi-agent execution depth. Default: balanced.",
    )
    parser.add_argument(
        "--report-mode",
        choices=["rule", "llm", "auto"],
        default="auto",
        help="Report generation mode. Default: auto.",
    )
    parser.add_argument(
        "--save",
        default="outputs/reports/last_multi_agent_v3_report.md",
        help="Path to save rule/fallback report.",
    )
    parser.add_argument(
        "--llm-report-save",
        default="outputs/reports/last_multi_agent_v3_llm_report.md",
        help="Path to save LLM report.",
    )
    parser.add_argument(
        "--no-optional",
        action="store_true",
        help="Do not run optional agents even in balanced mode.",
    )
    return parser.parse_args()


def build_report(args: argparse.Namespace, workflow_result: dict) -> tuple[str, str, Path]:
    if args.report_mode == "rule":
        report_agent = ReportAgent()
        report = report_agent.build_report(
            route=workflow_result["route"],
            results=workflow_result["results"],
        )
        return report, "Rule ReportAgent", Path(args.save)

    if args.report_mode == "llm":
        llm_report_agent = LLMReportAgent()
        report = llm_report_agent.build_report(
            route=workflow_result["route"],
            results=workflow_result["results"],
        )
        return report, "LLMReportAgent", Path(args.llm_report_save)

    # auto
    try:
        llm_report_agent = LLMReportAgent()
        report = llm_report_agent.build_report(
            route=workflow_result["route"],
            results=workflow_result["results"],
        )
        return report, "LLMReportAgent", Path(args.llm_report_save)
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
        return report, "Rule ReportAgent fallback", Path(args.save)


def main() -> None:
    args = parse_args()

    user_question = " ".join(args.question).strip()
    if not user_question:
        print("请提供问题，例如：")
        print(
            'python main_multi_agent_v3.py '
            '"帮我分析 examples/logs/regression/08_complex_mixed_failure.log" '
            '--agent-depth balanced'
        )
        raise SystemExit(1)

    print("\n" + "=" * 100)
    print("Legacy / experimental entry: Stage-3 V3 Dynamic Multi-Agent Linux Troubleshooting Assistant")
    print("Historical CLI only; use main_monitor.py for monitor/daemon workflows.")
    print("=" * 100)

    print("\n" + "=" * 100)
    print("用户问题")
    print("=" * 100)
    print(user_question)

    route = classify_issue_dict(user_question)

    print("\n" + "=" * 100)
    print("内容感知路由结果")
    print("=" * 100)
    print(format_route_context(route))

    run_optional = None if not args.no_optional else False

    orchestrator = MultiAgentOrchestratorV3(
        route=route,
        agent_depth=args.agent_depth,  # type: ignore[arg-type]
        run_optional=run_optional,
    )
    workflow_result = orchestrator.run()

    plan = workflow_result["execution_plan"]

    print("\n" + "=" * 100)
    print("V3 ExecutionPlan")
    print("=" * 100)
    print(plan.to_markdown())

    print("\n" + "=" * 100)
    print("实际执行的 Agent 结果")
    print("=" * 100)
    for result in workflow_result["results"]:
        print(
            f"- {result.agent_name}: "
            f"status={result.status}, issue_type={result.issue_type}, "
            f"is_primary={result.is_primary}"
        )
        print(f"  summary: {result.summary}")

    report, report_source, save_path = build_report(args, workflow_result)

    save_path.parent.mkdir(parents=True, exist_ok=True)
    save_path.write_text(report, encoding="utf-8")

    print("\n" + "=" * 100)
    print(f"最终 V3 多 Agent 报告（{report_source}）")
    print("=" * 100)
    print(report)

    print("\n" + "=" * 100)
    print(f"Agent depth：{args.agent_depth}")
    print(f"报告生成方式：{report_source}")
    print(f"报告已保存到：{save_path}")
    print("=" * 100)


if __name__ == "__main__":
    main()
