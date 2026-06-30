#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.r16_long_cycle_shadow_validate import (
    build_aggregate_summary,
    build_cycle_summary,
    write_aggregate_json,
    write_aggregate_summary,
    write_cycle_summary,
    write_cycle_summary_json,
)


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir).expanduser().resolve()
    if not output_dir.exists():
        raise SystemExit(f"output dir does not exist: {output_dir}")

    cycle_summaries = load_or_rebuild_cycle_summaries(output_dir)
    if not cycle_summaries:
        raise SystemExit(f"no cycle summaries found under: {output_dir}")

    aggregate = build_aggregate_summary(
        output_dir=output_dir,
        requested_cycles=int(args.requested_cycles or len(cycle_summaries)),
        interval_seconds=int(args.interval_seconds),
        cycle_summaries=cycle_summaries,
    )
    write_aggregate_summary(output_dir, aggregate)
    write_aggregate_json(output_dir, aggregate)

    print(f"summary_output_dir={output_dir}")
    print(f"cycles_completed={aggregate['cycles_completed']}")
    print(f"conclusion={aggregate['conclusion']}")
    print(
        "summary_path="
        f"{output_dir / 'R16_S2_LONG_CYCLE_SHADOW_PILOT_SUMMARY.md'}"
    )
    return 0 if aggregate["conclusion"] in {"PASS", "PARTIAL"} else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rebuild aggregate R16 long-cycle shadow summary from cycle outputs."
    )
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--requested-cycles", type=int, default=0)
    parser.add_argument("--interval-seconds", type=int, default=0)
    return parser.parse_args()


def load_or_rebuild_cycle_summaries(output_dir: Path) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for cycle_dir in sorted(output_dir.glob("cycle_[0-9][0-9][0-9]*")):
        if not cycle_dir.is_dir():
            continue

        summary_path = cycle_dir / "cycle_summary.json"
        if summary_path.exists():
            summaries.append(load_json(summary_path))
            continue

        rebuilt = rebuild_cycle_summary(cycle_dir)
        if rebuilt is None:
            continue
        write_cycle_summary(cycle_dir, rebuilt)
        write_cycle_summary_json(cycle_dir, rebuilt)
        summaries.append(rebuilt)

    return sorted(summaries, key=lambda item: int(item.get("cycle", 0) or 0))


def rebuild_cycle_summary(cycle_dir: Path) -> dict[str, Any] | None:
    shadow_summary_path = cycle_dir / "shadow_summary.json"
    if not shadow_summary_path.exists():
        return None

    shadow_summary = load_json(shadow_summary_path)
    generated_at = str(shadow_summary.get("generated_at") or "")
    return build_cycle_summary(
        cycle_index=cycle_number(cycle_dir),
        cycle_dir=cycle_dir,
        started_at=generated_at,
        finished_at=generated_at,
        shadow_summary=shadow_summary,
        exception_text="",
    )


def cycle_number(cycle_dir: Path) -> int:
    try:
        return int(cycle_dir.name.rsplit("_", 1)[1])
    except (IndexError, ValueError):
        return 0


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    raise SystemExit(main())
