#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from detectors import ErrorEvent, ErrorEventDetector
from monitors.project_registry import PolicyConfig, ProjectConfig
from policies import CompatibilityRemediationPolicy, RemediationDecision
from safe_recovery.registry import (
    SAFE_RECOVERY_FIX_IDS,
    manual_event_types,
    safe_event_types,
)


SCHEMA_VERSION = "r17.real_log_shadow.v1"

CROSS_DOMAIN_RULES: list[dict[str, Any]] = [
    {
        "reason": "worker_queue_domain_overlap",
        "domains": ["worker_overload", "queue_backpressure"],
        "event_types": {"worker_overload", "queue_backpressure"},
        "risk": "medium",
    },
    {
        "reason": "optional_python_env_overlap",
        "domains": ["optional_dependency", "python_env"],
        "event_types": {
            "optional_dependency_missing",
            "optional_integration_failed",
            "python_env",
        },
        "risk": "high",
    },
    {
        "reason": "cache_disk_domain_overlap",
        "domains": ["cache", "disk"],
        "event_types": {
            "cache_write_failed",
            "optional_cache_backend_failed",
            "disk_full",
        },
        "risk": "high",
    },
    {
        "reason": "queue_dependency_service_overlap",
        "domains": ["queue_backpressure", "dependency_service"],
        "event_types": {"queue_backpressure", "dependency_service"},
        "risk": "high",
    },
    {
        "reason": "notification_auth_overlap",
        "domains": ["notification_or_observability", "auth_cert"],
        "event_types": {
            "notification_sink_failed",
            "observability_export_failed",
            "auth_cert",
        },
        "risk": "high",
    },
    {
        "reason": "optional_service_network_overlap",
        "domains": ["optional_service_or_observability", "network_connectivity"],
        "event_types": {
            "optional_service_unavailable",
            "observability_export_failed",
            "network_connectivity",
        },
        "risk": "medium",
    },
]


@dataclass(frozen=True)
class ShadowCase:
    case_id: str
    log_file: Path
    expected_event_types: list[str]
    source_kind: str = "labeled"
    notes: str = ""
    labeled: bool = True


def main() -> int:
    args = parse_args()
    output_dir = resolve_output_dir(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    cases = load_cases(
        manifest_path=Path(args.manifest).expanduser().resolve()
        if args.manifest
        else None,
        log_paths=[Path(item).expanduser().resolve() for item in args.log],
        log_dir=Path(args.log_dir).expanduser().resolve() if args.log_dir else None,
        glob_pattern=args.glob,
    )
    if not cases:
        raise SystemExit("no shadow cases found")

    summary = evaluate_shadow_cases(cases)
    write_outputs(output_dir=output_dir, summary=summary)

    print(f"r17_shadow_output_dir={output_dir}")
    print(f"conclusion={summary['conclusion']}")
    print(f"case_count={summary['metrics']['case_count']}")
    print(f"false_positive_count={summary['metrics']['false_positive_count']}")
    print(f"false_negative_count={summary['metrics']['false_negative_count']}")
    print(
        "safe_swallowed_high_risk_count="
        f"{summary['metrics']['safe_swallowed_high_risk_count']}"
    )
    print(f"cross_domain_case_count={summary['metrics']['cross_domain_case_count']}")
    print(
        "manual_escalation_noise_count="
        f"{summary['metrics']['manual_escalation_noise_count']}"
    )
    return 0 if summary["conclusion"] == "PASS" else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "R17 real-log shadow evaluator. Reads real or production-like logs, "
            "runs detector/policy in read-only shadow mode, and reports FP/FN, "
            "manual escalation noise, and cross-domain risk metrics."
        )
    )
    parser.add_argument(
        "--manifest",
        default="",
        help=(
            "JSON manifest with cases. If omitted, use --log or --log-dir as "
            "unlabeled shadow input."
        ),
    )
    parser.add_argument(
        "--log",
        action="append",
        default=[],
        help="Unlabeled log file to shadow-evaluate. Can be passed multiple times.",
    )
    parser.add_argument(
        "--log-dir",
        default="",
        help="Directory of unlabeled logs to shadow-evaluate.",
    )
    parser.add_argument(
        "--glob",
        default="*.log",
        help="Glob used with --log-dir. Default: *.log",
    )
    parser.add_argument(
        "--output-dir",
        default="",
        help="Output directory. Default: outputs/r17_real_log_shadow/<timestamp>",
    )
    return parser.parse_args()


def resolve_output_dir(raw_output_dir: str) -> Path:
    if raw_output_dir:
        return Path(raw_output_dir).expanduser().resolve()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return PROJECT_ROOT / "outputs" / "r17_real_log_shadow" / timestamp


def load_cases(
    *,
    manifest_path: Path | None,
    log_paths: list[Path],
    log_dir: Path | None,
    glob_pattern: str,
) -> list[ShadowCase]:
    cases: list[ShadowCase] = []
    if manifest_path is not None:
        cases.extend(load_manifest_cases(manifest_path))

    for path in log_paths:
        cases.append(
            ShadowCase(
                case_id=path.stem,
                log_file=path,
                expected_event_types=[],
                source_kind="unlabeled_log",
                labeled=False,
            )
        )

    if log_dir is not None:
        for path in sorted(log_dir.glob(glob_pattern)):
            if not path.is_file():
                continue
            cases.append(
                ShadowCase(
                    case_id=path.stem,
                    log_file=path,
                    expected_event_types=[],
                    source_kind="unlabeled_log_dir",
                    labeled=False,
                )
            )

    return cases


def load_manifest_cases(manifest_path: Path) -> list[ShadowCase]:
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        raw_cases = raw
    else:
        raw_cases = list(raw.get("cases") or [])

    cases: list[ShadowCase] = []
    for index, item in enumerate(raw_cases, start=1):
        log_file = item.get("log_file") or item.get("path")
        if not log_file:
            raise ValueError(f"manifest case #{index} missing log_file")

        expected = normalize_expected_event_types(item)
        labeled = bool(item.get("labeled", True))
        if "expected_event_type" in item or "expected_event_types" in item:
            labeled = True

        cases.append(
            ShadowCase(
                case_id=str(item.get("case_id") or Path(log_file).stem),
                log_file=(manifest_path.parent / str(log_file)).resolve(),
                expected_event_types=expected,
                source_kind=str(item.get("source_kind") or "manifest"),
                notes=str(item.get("notes") or ""),
                labeled=labeled,
            )
        )

    return cases


def normalize_expected_event_types(item: dict[str, Any]) -> list[str]:
    if "expected_event_types" in item:
        value = item.get("expected_event_types")
        if value is None:
            return []
        if not isinstance(value, list):
            raise TypeError("expected_event_types must be a list")
        return sorted({str(event_type) for event_type in value if event_type})

    value = item.get("expected_event_type")
    if value is None:
        return []
    return [str(value)]


def evaluate_shadow_cases(cases: list[ShadowCase]) -> dict[str, Any]:
    detector = ErrorEventDetector()
    policy = CompatibilityRemediationPolicy()
    project = make_shadow_project()

    rows = []
    for case in cases:
        text = case.log_file.read_text(encoding="utf-8", errors="replace")
        events = detector.detect_all(text, source=f"r17_shadow:{case.log_file}")
        if not events:
            events = detector.detect(text, source=f"r17_shadow:{case.log_file}")

        decisions = [
            decision_to_row(event, policy.decide(event=event, project=project))
            for event in events
        ]
        rows.append(build_case_row(case=case, text=text, events=events, decisions=decisions))

    metrics = build_metrics(rows)
    conclusion = "PASS" if is_passing(metrics) else "REVIEW"
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "conclusion": conclusion,
        "project": {
            "project_id": "r17_real_log_shadow",
            "mode": "read_only_shadow",
            "recovery_executed": False,
            "reports_generated_by_monitor": False,
            "notifications_sent": False,
        },
        "metrics": metrics,
        "rows": rows,
    }


def make_shadow_project() -> ProjectConfig:
    return ProjectConfig(
        project_id="r17_real_log_shadow",
        name="R17 Real Log Shadow",
        mode="local",
        project_dir=".",
        run_command="",
        policy=PolicyConfig(
            auto_recover=True,
            allow_auto_apply=sorted(SAFE_RECOVERY_FIX_IDS),
            escalation_required=[],
            rollback_on_failure=True,
            auto_recovery_policy_enabled=True,
            auto_recovery_dry_run=True,
        ),
    )


def decision_to_row(event: ErrorEvent, decision: RemediationDecision) -> dict[str, Any]:
    return {
        "event_type": event.event_type,
        "issue_type": event.issue_type,
        "severity": event.severity,
        "fingerprint": event.fingerprint,
        "line_number": event.line_number,
        "matched_keywords": list(event.matched_keywords),
        "policy_action": decision.action,
        "fix_id": decision.fix_id,
        "is_safe_domain": event.event_type in safe_event_types(),
        "is_manual_domain": event.event_type in manual_event_types(),
    }


def build_case_row(
    *,
    case: ShadowCase,
    text: str,
    events: list[ErrorEvent],
    decisions: list[dict[str, Any]],
) -> dict[str, Any]:
    expected = set(case.expected_event_types)
    detected = {event.event_type for event in events}
    unexpected = sorted(detected - expected) if case.labeled else []
    missing = sorted(expected - detected) if case.labeled else []
    cross_domain = detect_cross_domain(detected)

    expected_manual = expected & manual_event_types()
    detected_safe = detected & safe_event_types()
    detected_manual = detected & manual_event_types()

    safe_swallowed_high_risk = bool(
        case.labeled
        and expected_manual
        and detected_safe
        and (expected_manual - detected)
    )
    safe_overlap_high_risk = bool(
        case.labeled
        and expected_manual
        and detected_safe
        and bool(expected_manual & detected)
    )

    manual_noise_events = sorted(
        item["event_type"]
        for item in decisions
        if item["policy_action"] == "manual_escalation"
        and case.labeled
        and item["event_type"] not in expected_manual
    )

    return {
        "case_id": case.case_id,
        "log_file": str(case.log_file),
        "source_kind": case.source_kind,
        "notes": case.notes,
        "labeled": case.labeled,
        "line_count": len(text.splitlines()),
        "size_bytes": len(text.encode("utf-8")),
        "expected_event_types": sorted(expected),
        "detected_event_types": sorted(detected),
        "unexpected_event_types": unexpected,
        "missing_event_types": missing,
        "exact_match": bool(case.labeled and not unexpected and not missing),
        "false_positive": bool(case.labeled and unexpected),
        "false_negative": bool(case.labeled and missing),
        "detected_safe_event_types": sorted(detected_safe),
        "detected_manual_event_types": sorted(detected_manual),
        "manual_escalation_event_types": sorted(
            item["event_type"]
            for item in decisions
            if item["policy_action"] == "manual_escalation"
        ),
        "manual_escalation_noise_event_types": manual_noise_events,
        "manual_escalation_noise": bool(manual_noise_events),
        "safe_swallowed_high_risk": safe_swallowed_high_risk,
        "safe_high_risk_overlap": safe_overlap_high_risk,
        "cross_domain": bool(cross_domain),
        "cross_domain_flags": cross_domain,
        "decisions": decisions,
    }


def detect_cross_domain(detected_event_types: set[str]) -> list[dict[str, Any]]:
    flags = []
    for rule in CROSS_DOMAIN_RULES:
        matched = sorted(detected_event_types & set(rule["event_types"]))
        if len(matched) < 2:
            continue
        flags.append(
            {
                "reason": rule["reason"],
                "risk": rule["risk"],
                "domains": list(rule["domains"]),
                "matched_event_types": matched,
            }
        )
    return flags


def build_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    labeled_rows = [row for row in rows if row["labeled"]]
    expected_total = sum(len(row["expected_event_types"]) for row in labeled_rows)
    detected_labeled_total = sum(len(row["detected_event_types"]) for row in labeled_rows)
    false_positive_count = sum(len(row["unexpected_event_types"]) for row in labeled_rows)
    false_negative_count = sum(len(row["missing_event_types"]) for row in labeled_rows)
    true_positive_count = sum(
        len(set(row["expected_event_types"]) & set(row["detected_event_types"]))
        for row in labeled_rows
    )
    manual_escalation_count = sum(
        len(row["manual_escalation_event_types"]) for row in rows
    )
    manual_escalation_noise_count = sum(
        len(row["manual_escalation_noise_event_types"]) for row in rows
    )
    safe_swallowed_high_risk_count = sum(
        1 for row in rows if row["safe_swallowed_high_risk"]
    )
    safe_high_risk_overlap_count = sum(
        1 for row in rows if row["safe_high_risk_overlap"]
    )
    cross_domain_case_count = sum(1 for row in rows if row["cross_domain"])

    return {
        "case_count": len(rows),
        "labeled_case_count": len(labeled_rows),
        "unlabeled_case_count": len(rows) - len(labeled_rows),
        "expected_event_count": expected_total,
        "detected_event_instance_count": sum(
            len(row["decisions"]) for row in rows
        ),
        "detected_event_count": sum(len(row["detected_event_types"]) for row in rows),
        "detected_labeled_event_count": detected_labeled_total,
        "true_positive_count": true_positive_count,
        "false_positive_count": false_positive_count,
        "false_negative_count": false_negative_count,
        "false_positive_case_count": sum(1 for row in labeled_rows if row["false_positive"]),
        "false_negative_case_count": sum(1 for row in labeled_rows if row["false_negative"]),
        "exact_match_case_count": sum(1 for row in labeled_rows if row["exact_match"]),
        "manual_escalation_count": manual_escalation_count,
        "manual_escalation_noise_count": manual_escalation_noise_count,
        "manual_escalation_noise_case_count": sum(
            1 for row in rows if row["manual_escalation_noise"]
        ),
        "safe_swallowed_high_risk_count": safe_swallowed_high_risk_count,
        "safe_high_risk_overlap_count": safe_high_risk_overlap_count,
        "cross_domain_case_count": cross_domain_case_count,
        "false_positive_rate": ratio(false_positive_count, detected_labeled_total),
        "false_negative_rate": ratio(false_negative_count, expected_total),
        "manual_noise_rate": ratio(manual_escalation_noise_count, manual_escalation_count),
        "safe_swallow_high_risk_rate": ratio(
            safe_swallowed_high_risk_count,
            sum(1 for row in labeled_rows if set(row["expected_event_types"]) & manual_event_types()),
        ),
    }


def ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 6)


def is_passing(metrics: dict[str, Any]) -> bool:
    return (
        metrics["false_positive_count"] == 0
        and metrics["false_negative_count"] == 0
        and metrics["safe_swallowed_high_risk_count"] == 0
        and metrics["manual_escalation_noise_count"] == 0
    )


def write_outputs(*, output_dir: Path, summary: dict[str, Any]) -> None:
    json_path = output_dir / "r17_real_log_shadow_summary.json"
    json_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    md_path = output_dir / "R17_REAL_LOG_SHADOW_SUMMARY.md"
    md_path.write_text(render_markdown(summary), encoding="utf-8")


def render_markdown(summary: dict[str, Any]) -> str:
    metrics = summary["metrics"]
    metric_labels = {
        "case_count": "样本总数",
        "labeled_case_count": "已标注样本数",
        "unlabeled_case_count": "未标注样本数",
        "expected_event_count": "期望事件类型总数",
        "detected_event_instance_count": "检测事件实例数",
        "detected_event_count": "检测事件类型数",
        "true_positive_count": "真阳性数量",
        "false_positive_count": "误报数量",
        "false_negative_count": "漏报数量",
        "false_positive_rate": "误报率",
        "false_negative_rate": "漏报率",
        "safe_swallowed_high_risk_count": "safe 域吞掉高风险域数量",
        "safe_high_risk_overlap_count": "safe 与高风险同窗共现数量",
        "manual_escalation_count": "人工升级事件数",
        "manual_escalation_noise_count": "人工升级噪声数",
        "manual_noise_rate": "人工升级噪声率",
        "cross_domain_case_count": "串域样本数",
    }
    lines = [
        "# R17 真实日志 Shadow 汇总",
        "",
        f"- 生成时间: `{summary['generated_at']}`",
        f"- 结论: `{summary['conclusion']}`",
        f"- schema_version: `{summary['schema_version']}`",
        f"- 是否执行恢复: `{summary['project']['recovery_executed']}`",
        f"- 是否发送通知: `{summary['project']['notifications_sent']}`",
        "",
        "## 统计指标",
        "",
        "| 指标 | 字段 | 值 |",
        "| --- | --- | --- |",
    ]
    for key in [
        "case_count",
        "labeled_case_count",
        "unlabeled_case_count",
        "expected_event_count",
        "detected_event_instance_count",
        "detected_event_count",
        "true_positive_count",
        "false_positive_count",
        "false_negative_count",
        "false_positive_rate",
        "false_negative_rate",
        "safe_swallowed_high_risk_count",
        "safe_high_risk_overlap_count",
        "manual_escalation_count",
        "manual_escalation_noise_count",
        "manual_noise_rate",
        "cross_domain_case_count",
    ]:
        lines.append(f"| {metric_labels[key]} | `{key}` | `{metrics[key]}` |")

    lines.extend(
        [
            "",
            "## 样本结果",
            "",
            "| case_id | 期望事件 | 检测事件 | 误报 | 漏报 | 人工升级噪声 | safe 吞高风险 | 串域 |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in summary["rows"]:
        lines.append(
            "| "
            f"`{row['case_id']}` | "
            f"`{','.join(row['expected_event_types']) or '<none>'}` | "
            f"`{','.join(row['detected_event_types']) or '<none>'}` | "
            f"`{row['false_positive']}` | "
            f"`{row['false_negative']}` | "
            f"`{row['manual_escalation_noise']}` | "
            f"`{row['safe_swallowed_high_risk']}` | "
            f"`{row['cross_domain']}` |"
        )

    notable_rows = [
        row
        for row in summary["rows"]
        if row["false_positive"]
        or row["false_negative"]
        or row["manual_escalation_noise"]
        or row["safe_swallowed_high_risk"]
        or row["cross_domain"]
    ]
    lines.extend(["", "## 需要关注的样本", ""])
    if not notable_rows:
        lines.append("- <none>")
    for row in notable_rows:
        lines.extend(
            [
                f"### {row['case_id']}",
                f"- 日志文件: `{row['log_file']}`",
                f"- 期望事件类型: `{row['expected_event_types']}`",
                f"- 检测事件类型: `{row['detected_event_types']}`",
                f"- 非预期事件类型: `{row['unexpected_event_types']}`",
                f"- 缺失事件类型: `{row['missing_event_types']}`",
                f"- 人工升级噪声事件类型: `{row['manual_escalation_noise_event_types']}`",
                f"- safe 域是否吞掉高风险域: `{row['safe_swallowed_high_risk']}`",
                f"- 串域标记: `{row['cross_domain_flags']}`",
                "",
            ]
        )

    lines.extend(
        [
            "## 安全说明",
            "",
            "- 本 evaluator 是只读 shadow：不会调用 MonitorLoop、AutoRecoveryRunner、apply、rerun、rollback、notification 或报告生成链路。",
            "- FP/FN 指标依赖带标注 manifest。未标注真实日志只统计检测量、人工升级量和串域数量。",
            "- 只要 `safe_swallowed_high_risk_count > 0`，就应暂停扩大自动恢复范围，先审查样本和 detector 行为。",
            "",
        ]
    )
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
