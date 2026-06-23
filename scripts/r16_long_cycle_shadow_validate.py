#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from recovery.guarded_auto_recover_dry_run import FORBIDDEN_ACTIONS
from scripts.r16_safe_recovery_shadow_validate import (
    build_shadow_summary,
    write_json,
    write_matrix,
    write_summary,
)


def main() -> int:
    args = parse_args()
    output_dir = resolve_output_dir(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    cycle_summaries: list[dict[str, Any]] = []
    for cycle_index in range(1, args.cycles + 1):
        cycle_dir = output_dir / f"cycle_{cycle_index:03d}"
        cycle_dir.mkdir(parents=True, exist_ok=True)
        started_at = now_text()
        exception_text = ""
        shadow_summary: dict[str, Any] | None = None

        try:
            shadow_summary = build_shadow_summary(cycle_dir)
            write_matrix(cycle_dir, shadow_summary)
            write_summary(cycle_dir, shadow_summary)
            write_json(cycle_dir, shadow_summary)
        except Exception as exc:  # pragma: no cover - exercised by pilot failure output
            exception_text = f"{type(exc).__name__}: {exc}"

        finished_at = now_text()
        cycle_summary = build_cycle_summary(
            cycle_index=cycle_index,
            cycle_dir=cycle_dir,
            started_at=started_at,
            finished_at=finished_at,
            shadow_summary=shadow_summary,
            exception_text=exception_text,
        )
        cycle_summaries.append(cycle_summary)
        write_cycle_summary(cycle_dir, cycle_summary)
        print_cycle_summary(cycle_summary)

        if cycle_index < args.cycles and args.interval_seconds > 0:
            time.sleep(args.interval_seconds)

    aggregate = build_aggregate_summary(
        output_dir=output_dir,
        requested_cycles=args.cycles,
        interval_seconds=args.interval_seconds,
        cycle_summaries=cycle_summaries,
    )
    write_aggregate_summary(output_dir, aggregate)
    write_aggregate_json(output_dir, aggregate)

    print(f"pilot_output_dir={output_dir}")
    print(f"cycles_completed={aggregate['cycles_completed']}")
    print(f"conclusion={aggregate['conclusion']}")
    return 0 if aggregate["conclusion"] in {"PASS", "PARTIAL"} else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="R16 long-cycle dry-run/shadow validation pilot."
    )
    parser.add_argument("--cycles", type=int, default=6)
    parser.add_argument("--interval-seconds", type=int, default=300)
    parser.add_argument("--output-dir", default="")
    args = parser.parse_args()
    if args.cycles < 1:
        parser.error("--cycles must be >= 1")
    if args.interval_seconds < 0:
        parser.error("--interval-seconds must be >= 0")
    return args


def resolve_output_dir(raw_output_dir: str) -> Path:
    if raw_output_dir:
        return Path(raw_output_dir).expanduser().resolve()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return (
        PROJECT_ROOT
        / "acceptance_artifacts"
        / f"r16_s2_shadow_pilot_{timestamp}"
    )


def build_cycle_summary(
    *,
    cycle_index: int,
    cycle_dir: Path,
    started_at: str,
    finished_at: str,
    shadow_summary: dict[str, Any] | None,
    exception_text: str,
) -> dict[str, Any]:
    if shadow_summary is None:
        return {
            "cycle": cycle_index,
            "cycle_dir": str(cycle_dir),
            "started_at": started_at,
            "finished_at": finished_at,
            "conclusion": "FAIL",
            "exception": exception_text or "shadow_summary_missing",
            **zero_counts(),
        }

    rows = list(shadow_summary.get("domain_rows") or [])
    manual_rows = list(shadow_summary.get("manual_rows") or [])
    coverage = dict(shadow_summary.get("coverage_counts") or {})
    safe_candidate_count = sum(
        1 for row in rows if row.get("strategy_layer") == "safe_auto_recover"
    )
    manual_escalation_count = sum(
        1 for row in manual_rows if row.get("action") == "manual_escalation"
    )
    diagnose_only_count = sum(
        1 for row in manual_rows if row.get("action") == "diagnose_only"
    )
    forbidden_blocked_count = sum(
        len(FORBIDDEN_ACTIONS)
        for row in rows
        if row.get("forbidden_action_blocked") is True
    )
    rollback_available_count = sum(
        1
        for row in rows
        if row.get("rollback_metadata", {}).get("available") is True
    )
    remote_apply_call_count = sum(
        1 for row in rows if row.get("remote_apply_called_in_shadow") is True
    )
    rerun_call_count = sum(
        1 for row in rows if row.get("rerun_called_in_shadow") is True
    )
    missing_items = list(shadow_summary.get("missing_items") or [])

    return {
        "cycle": cycle_index,
        "cycle_dir": str(cycle_dir),
        "started_at": started_at,
        "finished_at": finished_at,
        "conclusion": shadow_summary.get("conclusion", "FAIL"),
        "exception": exception_text,
        "safe_candidate_count": safe_candidate_count,
        "manual_escalation_count": manual_escalation_count,
        "diagnose_only_count": diagnose_only_count,
        "disabled_count": forbidden_blocked_count,
        "no_op_count": int(coverage.get("no_op", 0)),
        "forbidden_blocked_count": forbidden_blocked_count,
        "rollback_available_count": rollback_available_count,
        "rollback_unavailable_count": max(0, len(rows) - rollback_available_count),
        "remote_apply_call_count": remote_apply_call_count,
        "rerun_call_count": rerun_call_count,
        "misidentified_count": 0 if not missing_items else len(missing_items),
        "missed_detection_count": 0 if not missing_items else len(missing_items),
        "downgrade_count": manual_escalation_count
        + diagnose_only_count
        + forbidden_blocked_count
        + (1 if shadow_summary.get("unknown_fix_downgrades") is True else 0),
        "missing_items": missing_items,
        "summary_path": str(
            cycle_dir / "R16_SAFE_RECOVERY_DOMAIN_VALIDATION_SUMMARY.md"
        ),
    }


def zero_counts() -> dict[str, int]:
    return {
        "safe_candidate_count": 0,
        "manual_escalation_count": 0,
        "diagnose_only_count": 0,
        "disabled_count": 0,
        "no_op_count": 0,
        "forbidden_blocked_count": 0,
        "rollback_available_count": 0,
        "rollback_unavailable_count": 0,
        "remote_apply_call_count": 0,
        "rerun_call_count": 0,
        "misidentified_count": 0,
        "missed_detection_count": 0,
        "downgrade_count": 0,
        "missing_items": [],
    }


def build_aggregate_summary(
    *,
    output_dir: Path,
    requested_cycles: int,
    interval_seconds: int,
    cycle_summaries: list[dict[str, Any]],
) -> dict[str, Any]:
    totals = {
        "safe_candidate_count": sum_int(cycle_summaries, "safe_candidate_count"),
        "manual_escalation_count": sum_int(
            cycle_summaries, "manual_escalation_count"
        ),
        "diagnose_only_count": sum_int(cycle_summaries, "diagnose_only_count"),
        "disabled_count": sum_int(cycle_summaries, "disabled_count"),
        "no_op_count": sum_int(cycle_summaries, "no_op_count"),
        "forbidden_blocked_count": sum_int(
            cycle_summaries, "forbidden_blocked_count"
        ),
        "rollback_available_count": sum_int(
            cycle_summaries, "rollback_available_count"
        ),
        "rollback_unavailable_count": sum_int(
            cycle_summaries, "rollback_unavailable_count"
        ),
        "remote_apply_call_count": sum_int(
            cycle_summaries, "remote_apply_call_count"
        ),
        "rerun_call_count": sum_int(cycle_summaries, "rerun_call_count"),
        "misidentified_count": sum_int(cycle_summaries, "misidentified_count"),
        "missed_detection_count": sum_int(cycle_summaries, "missed_detection_count"),
        "downgrade_count": sum_int(cycle_summaries, "downgrade_count"),
    }
    exceptions = [
        {
            "cycle": item["cycle"],
            "exception": item.get("exception", ""),
        }
        for item in cycle_summaries
        if item.get("exception")
    ]
    all_cycle_pass = all(
        item.get("conclusion") == "PASS" for item in cycle_summaries
    )
    remote_or_rerun_called = bool(
        totals["remote_apply_call_count"] or totals["rerun_call_count"]
    )

    if exceptions or remote_or_rerun_called:
        conclusion = "FAIL"
    elif all_cycle_pass:
        conclusion = "PASS"
    else:
        conclusion = "PARTIAL"

    return {
        "generated_at": now_text(),
        "output_dir": str(output_dir),
        "requested_cycles": requested_cycles,
        "cycles_completed": len(cycle_summaries),
        "interval_seconds": interval_seconds,
        "cycle_summaries": cycle_summaries,
        "exceptions": exceptions,
        "remote_apply_fix_called": totals["remote_apply_call_count"] > 0,
        "rerun_remote_project_called": totals["rerun_call_count"] > 0,
        "conclusion": conclusion,
        "recommend_r16_s2_formal_shadow": conclusion == "PASS",
        **totals,
    }


def sum_int(items: list[dict[str, Any]], key: str) -> int:
    return sum(int(item.get(key, 0) or 0) for item in items)


def write_cycle_summary(cycle_dir: Path, cycle_summary: dict[str, Any]) -> Path:
    path = cycle_dir / "R16_S2_CYCLE_SUMMARY.md"
    lines = [
        "# R16-S2 Shadow Cycle Summary",
        "",
        f"- cycle: `{cycle_summary['cycle']}`",
        f"- conclusion: `{cycle_summary['conclusion']}`",
        f"- started_at: `{cycle_summary['started_at']}`",
        f"- finished_at: `{cycle_summary['finished_at']}`",
        f"- safe_candidate_count: `{cycle_summary['safe_candidate_count']}`",
        f"- manual_escalation_count: `{cycle_summary['manual_escalation_count']}`",
        f"- diagnose_only_count: `{cycle_summary['diagnose_only_count']}`",
        f"- disabled_count: `{cycle_summary['disabled_count']}`",
        f"- no_op_count: `{cycle_summary['no_op_count']}`",
        f"- forbidden_blocked_count: `{cycle_summary['forbidden_blocked_count']}`",
        f"- rollback_available_count: `{cycle_summary['rollback_available_count']}`",
        f"- remote_apply_call_count: `{cycle_summary['remote_apply_call_count']}`",
        f"- rerun_call_count: `{cycle_summary['rerun_call_count']}`",
        f"- exception: `{cycle_summary.get('exception') or '<none>'}`",
        f"- summary_path: `{cycle_summary.get('summary_path', '<none>')}`",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_aggregate_summary(output_dir: Path, aggregate: dict[str, Any]) -> Path:
    path = output_dir / "R16_S2_LONG_CYCLE_SHADOW_PILOT_SUMMARY.md"
    lines = [
        "# R16-S2 Long-Cycle Shadow Pilot Summary",
        "",
        f"- generated_at: `{aggregate['generated_at']}`",
        f"- conclusion: `{aggregate['conclusion']}`",
        f"- requested_cycles: `{aggregate['requested_cycles']}`",
        f"- cycles_completed: `{aggregate['cycles_completed']}`",
        f"- interval_seconds: `{aggregate['interval_seconds']}`",
        f"- safe_candidate_count: `{aggregate['safe_candidate_count']}`",
        f"- manual_escalation_count: `{aggregate['manual_escalation_count']}`",
        f"- diagnose_only_count: `{aggregate['diagnose_only_count']}`",
        f"- disabled_count: `{aggregate['disabled_count']}`",
        f"- no_op_count: `{aggregate['no_op_count']}`",
        f"- forbidden_blocked_count: `{aggregate['forbidden_blocked_count']}`",
        f"- rollback_available_count: `{aggregate['rollback_available_count']}`",
        f"- rollback_unavailable_count: `{aggregate['rollback_unavailable_count']}`",
        f"- remote_apply_fix_called: `{aggregate['remote_apply_fix_called']}`",
        f"- rerun_remote_project_called: `{aggregate['rerun_remote_project_called']}`",
        f"- exception_count: `{len(aggregate['exceptions'])}`",
        f"- recommend_formal_3_7_day_shadow: `{aggregate['recommend_r16_s2_formal_shadow']}`",
        "",
        "## Cycle Results",
        "",
        "| cycle | conclusion | safe | manual | diagnose | disabled | no-op | forbidden | rollback_available | remote_apply | rerun | exception |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in aggregate["cycle_summaries"]:
        lines.append(
            "| "
            f"{item['cycle']} | "
            f"{item['conclusion']} | "
            f"{item['safe_candidate_count']} | "
            f"{item['manual_escalation_count']} | "
            f"{item['diagnose_only_count']} | "
            f"{item['disabled_count']} | "
            f"{item['no_op_count']} | "
            f"{item['forbidden_blocked_count']} | "
            f"{item['rollback_available_count']} | "
            f"{item['remote_apply_call_count']} | "
            f"{item['rerun_call_count']} | "
            f"{item.get('exception') or '<none>'} |"
        )

    lines.extend(
        [
            "",
            "## Safety Boundary",
            "",
            "- The pilot only calls offline shadow validation helpers.",
            "- The pilot does not call `remote_apply_fix`.",
            "- The pilot does not call `rerun_remote_project`.",
            "- The pilot writes artifacts only under the requested acceptance directory.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_aggregate_json(output_dir: Path, aggregate: dict[str, Any]) -> Path:
    path = output_dir / "long_cycle_shadow_summary.json"
    path.write_text(json.dumps(aggregate, indent=2), encoding="utf-8")
    return path


def print_cycle_summary(cycle_summary: dict[str, Any]) -> None:
    print(
        "cycle={cycle} conclusion={conclusion} safe={safe} manual={manual} "
        "disabled={disabled} no_op={no_op} exception={exception}".format(
            cycle=cycle_summary["cycle"],
            conclusion=cycle_summary["conclusion"],
            safe=cycle_summary["safe_candidate_count"],
            manual=cycle_summary["manual_escalation_count"],
            disabled=cycle_summary["disabled_count"],
            no_op=cycle_summary["no_op_count"],
            exception=cycle_summary.get("exception") or "<none>",
        )
    )


def now_text() -> str:
    return datetime.now().isoformat(timespec="seconds")


if __name__ == "__main__":
    raise SystemExit(main())
