#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


DEFAULT_SUMMARY_NAME = "r17_real_log_shadow_summary.json"


def main() -> int:
    args = parse_args()
    summary_path = resolve_summary_path(args.summary)
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    metrics = dict(summary.get("metrics") or {})

    failures = gate_failures(args, metrics)
    print(f"summary={summary_path}")
    print(f"conclusion={summary.get('conclusion', '')}")
    for key in [
        "case_count",
        "labeled_case_count",
        "false_positive_count",
        "false_negative_count",
        "safe_swallowed_high_risk_count",
        "manual_escalation_noise_count",
        "cross_domain_case_count",
    ]:
        print(f"{key}={metrics.get(key, 0)}")

    if failures:
        print("r18_gate=FAIL")
        for failure in failures:
            print(f"failure={failure}")
        return 1

    print("r18_gate=PASS")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="R18 gate for read-only real-log shadow summaries."
    )
    parser.add_argument(
        "--summary",
        required=True,
        help="Path to r17_real_log_shadow_summary.json or its output directory.",
    )
    parser.add_argument("--max-false-positive-count", type=int, default=0)
    parser.add_argument("--max-false-negative-count", type=int, default=0)
    parser.add_argument("--max-safe-swallowed-high-risk-count", type=int, default=0)
    parser.add_argument("--max-manual-escalation-noise-count", type=int, default=0)
    parser.add_argument("--min-labeled-case-count", type=int, default=1)
    return parser.parse_args()


def resolve_summary_path(raw: str) -> Path:
    path = Path(raw).expanduser().resolve()
    if path.is_dir():
        path = path / DEFAULT_SUMMARY_NAME
    if not path.exists():
        raise SystemExit(f"summary_not_found:{path}")
    return path


def gate_failures(args: argparse.Namespace, metrics: dict[str, Any]) -> list[str]:
    checks = [
        (
            "false_positive_count",
            int(metrics.get("false_positive_count", 0) or 0),
            args.max_false_positive_count,
        ),
        (
            "false_negative_count",
            int(metrics.get("false_negative_count", 0) or 0),
            args.max_false_negative_count,
        ),
        (
            "safe_swallowed_high_risk_count",
            int(metrics.get("safe_swallowed_high_risk_count", 0) or 0),
            args.max_safe_swallowed_high_risk_count,
        ),
        (
            "manual_escalation_noise_count",
            int(metrics.get("manual_escalation_noise_count", 0) or 0),
            args.max_manual_escalation_noise_count,
        ),
    ]
    failures = [
        f"{name}:{value}>{limit}"
        for name, value, limit in checks
        if value > limit
    ]
    labeled_count = int(metrics.get("labeled_case_count", 0) or 0)
    if labeled_count < int(args.min_labeled_case_count):
        failures.append(
            f"labeled_case_count:{labeled_count}<"
            f"{int(args.min_labeled_case_count)}"
        )
    return failures


if __name__ == "__main__":
    sys.exit(main())
