"""Official monitor and daemon entry point for the project."""

from __future__ import annotations

import argparse
import signal

from monitors import MonitorLoop, ProjectRegistry


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Official Stage 6 Linux Project Monitor & Auto-Recovery Agent entry"
    )
    parser.add_argument(
        "--config",
        default="configs/projects.yaml",
        help="Path to projects.yaml.",
    )
    parser.add_argument(
        "--project",
        required=True,
        help="project_id defined in projects.yaml.",
    )
    parser.add_argument(
        "--agent-depth",
        choices=["minimal", "balanced", "full"],
        default="balanced",
    )
    parser.add_argument(
        "--report-mode",
        choices=["rule", "llm", "auto"],
        default="auto",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one monitor cycle and exit.",
    )
    parser.add_argument(
        "--cycles",
        type=int,
        default=0,
        help="Run N monitor cycles and exit. If 0 and --once not set, run forever.",
    )
    parser.add_argument(
        "--forever",
        action="store_true",
        help="Stage 6E: run monitor forever until Ctrl+C.",
    )
    parser.add_argument(
        "--daemon",
        action="store_true",
        help="Stage 6E: alias of --forever for Python-level daemon mode.",
    )
    parser.add_argument(
        "--state-dir",
        default="state",
        help="Directory for Stage 6E state files.",
    )
    parser.add_argument(
        "--daemon-log",
        default="",
        help="Path of daemon.log. Default: state/<project_id>/daemon.log",
    )
    parser.add_argument(
        "--heartbeat-interval",
        type=int,
        default=60,
        help="Heartbeat interval seconds for daemon mode.",
    )
    parser.add_argument(
        "--health-check-interval",
        type=int,
        default=300,
        help="Health check interval seconds for daemon mode.",
    )
    parser.add_argument(
        "--max-idle-cycles",
        type=int,
        default=0,
        help="Stop daemon after N idle cycles. 0 means never stop because of idle.",
    )
    parser.add_argument(
        "--no-persistent-state",
        action="store_true",
        help="Disable persistent project_status.json and long-term fingerprint dedupe.",
    )
    parser.add_argument(
        "--output-root",
        default="outputs/monitors",
        help="Root directory for monitor reports.",
    )
    return parser.parse_args()


def _print_result(result) -> None:
    print("=" * 100)
    print("Monitor finished")
    print("=" * 100)
    print(f"project_id: {result.project_id}")
    print(f"events_detected: {result.events_detected}")
    print("reports_generated:")
    for item in result.reports_generated:
        print(f"- {item}")


def main() -> None:
    args = parse_args()

    registry = ProjectRegistry(args.config)
    project = registry.get(args.project)

    loop = MonitorLoop(
        project=project,
        agent_depth=args.agent_depth,
        report_mode=args.report_mode,
        output_root=args.output_root,
        state_dir=args.state_dir,
        daemon_log_path=args.daemon_log or None,
        heartbeat_interval=args.heartbeat_interval,
        health_check_interval=args.health_check_interval,
        enable_persistent_state=not args.no_persistent_state,
    )

    def _handle_stop_signal(signum, frame) -> None:
        loop.request_stop("stopped_by_signal")

    signal.signal(signal.SIGTERM, _handle_stop_signal)
    signal.signal(signal.SIGINT, _handle_stop_signal)

    max_idle_cycles = (
        args.max_idle_cycles if args.max_idle_cycles > 0 else None
    )

    if args.once:
        result = loop.run_for_cycles(1)
        _print_result(result)
        return

    if args.cycles > 0:
        result = loop.run_for_cycles(args.cycles)
        _print_result(result)
        return

    # Stage 6E:
    # --forever / --daemon 显式进入长期运行。
    # 如果 --cycles=0 且没有 --once，也默认长期运行，保持你原来的行为。
    if args.forever or args.daemon or args.cycles == 0:
        loop.run_forever(max_idle_cycles=max_idle_cycles)
        return


if __name__ == "__main__":
    main()
