#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

SCHEMA_VERSION = "r14.retention_dry_run.v1"

CORE_STATE_FILES = {
    "project_status.json",
    "events.jsonl",
    "alerts.jsonl",
    "recoveries.jsonl",
}

RISK_MARKERS = {
    "manual": [
        "manual_escalation",
        "[escalation]",
        "operator_required",
        "人工升级",
    ],
    "gate": [
        "ambiguous_event_evidence",
        "gate_blocked",
        "r15_gate_blocked",
        "forbidden_action",
        "manual_review_required",
    ],
    "rollback": [
        "rollback_failed",
        "rollback_failure",
        "rollback_error",
    ],
}

RUN_TIMESTAMP_RE = re.compile(r"^(?P<prefix>.+?)_(?P<stamp>\d{8}(?:_\d{4,6})?)$")


@dataclass(frozen=True)
class RetentionConfig:
    reports_retention_days: int = 30
    keep_latest_reports_per_project: int = 50
    alerts_retention_days: int = 90
    keep_latest_alerts_per_project: int = 200
    acceptance_artifacts_retention_days: int = 30
    keep_latest_artifact_runs_per_prefix: int = 5
    daemon_log_max_size_bytes: int = 50 * 1024 * 1024
    daemon_log_keep_backups: int = 5

    def to_dict(self) -> dict[str, Any]:
        return {
            "reports_retention_days": self.reports_retention_days,
            "keep_latest_reports_per_project": self.keep_latest_reports_per_project,
            "alerts_retention_days": self.alerts_retention_days,
            "keep_latest_alerts_per_project": self.keep_latest_alerts_per_project,
            "acceptance_artifacts_retention_days": (
                self.acceptance_artifacts_retention_days
            ),
            "keep_latest_artifact_runs_per_prefix": (
                self.keep_latest_artifact_runs_per_prefix
            ),
            "daemon_log_max_size_bytes": self.daemon_log_max_size_bytes,
            "daemon_log_keep_backups": self.daemon_log_keep_backups,
        }


@dataclass
class ArtifactRecord:
    path: Path
    relative_path: str
    artifact_type: str
    artifact_family: str
    project_id: str
    size_bytes: int
    mtime: str
    age_days: float
    suffix: str
    is_latest: bool = False
    referenced_by_alert: bool = False
    contains_manual_escalation: bool = False
    contains_gate_block: bool = False
    contains_rollback_failure: bool = False
    protected: bool = False
    protected_reasons: list[str] = field(default_factory=list)
    candidate: bool = False
    candidate_action: str = ""
    candidate_reasons: list[str] = field(default_factory=list)
    risk_level: str = "low"
    estimated_reclaim_bytes: int = 0
    details: dict[str, Any] = field(default_factory=dict)

    def protect(self, reason: str) -> None:
        if reason not in self.protected_reasons:
            self.protected_reasons.append(reason)
        self.protected = True

    def add_candidate(self, *, action: str, reason: str, risk_level: str) -> None:
        if reason not in self.candidate_reasons:
            self.candidate_reasons.append(reason)
        self.candidate = True
        self.candidate_action = action
        self.risk_level = risk_level
        if action == "delete_candidate":
            self.estimated_reclaim_bytes = self.size_bytes

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.relative_path,
            "absolute_path": str(self.path),
            "artifact_type": self.artifact_type,
            "artifact_family": self.artifact_family,
            "project_id": self.project_id,
            "size_bytes": self.size_bytes,
            "mtime": self.mtime,
            "age_days": self.age_days,
            "suffix": self.suffix,
            "is_latest": self.is_latest,
            "referenced_by_alert": self.referenced_by_alert,
            "contains_manual_escalation": self.contains_manual_escalation,
            "contains_gate_block": self.contains_gate_block,
            "contains_rollback_failure": self.contains_rollback_failure,
            "protected": self.protected,
            "protected_reason": ",".join(self.protected_reasons),
            "protected_reasons": list(self.protected_reasons),
            "candidate": self.candidate,
            "candidate_action": self.candidate_action,
            "candidate_reason": ",".join(self.candidate_reasons),
            "candidate_reasons": list(self.candidate_reasons),
            "risk_level": self.risk_level,
            "estimated_reclaim_bytes": self.estimated_reclaim_bytes,
            "details": self.details,
        }


def main() -> int:
    args = parse_args()
    now = parse_now(args.now)
    project_root = Path(args.project_root).expanduser().resolve()
    output_dir = resolve_output_dir(args.output_dir, project_root)
    config = RetentionConfig(
        reports_retention_days=args.reports_retention_days,
        keep_latest_reports_per_project=args.keep_latest_reports_per_project,
        alerts_retention_days=args.alerts_retention_days,
        keep_latest_alerts_per_project=args.keep_latest_alerts_per_project,
        acceptance_artifacts_retention_days=args.acceptance_artifacts_retention_days,
        keep_latest_artifact_runs_per_prefix=(
            args.keep_latest_artifact_runs_per_prefix
        ),
        daemon_log_max_size_bytes=args.daemon_log_max_size_bytes,
        daemon_log_keep_backups=args.daemon_log_keep_backups,
    )

    summary = run_dry_run(
        project_root=project_root,
        output_dir=output_dir,
        config=config,
        now=now,
    )

    print(f"retention_dry_run_output_dir={output_dir}")
    print(f"inventory_count={summary['metrics']['inventory_count']}")
    print(f"candidate_count={summary['metrics']['candidate_count']}")
    print(f"protected_count={summary['metrics']['protected_count']}")
    print(f"estimated_reclaim_bytes={summary['metrics']['estimated_reclaim_bytes']}")
    print(f"daemon_rotation_plan_count={summary['metrics']['daemon_rotation_plan_count']}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "R14 retention/log rotation dry-run. It inventories outputs/state/"
            "acceptance artifacts, classifies artifacts, and writes a candidate "
            "plan without deleting, moving, or truncating anything."
        )
    )
    parser.add_argument("--project-root", default=str(PROJECT_ROOT))
    parser.add_argument(
        "--output-dir",
        default="",
        help=(
            "Dry-run output directory. Default: "
            "acceptance_artifacts/r14_3b_retention_dry_run_<timestamp>"
        ),
    )
    parser.add_argument("--now", default="", help="ISO timestamp for deterministic runs")
    parser.add_argument("--reports-retention-days", type=int, default=30)
    parser.add_argument("--keep-latest-reports-per-project", type=int, default=50)
    parser.add_argument("--alerts-retention-days", type=int, default=90)
    parser.add_argument("--keep-latest-alerts-per-project", type=int, default=200)
    parser.add_argument("--acceptance-artifacts-retention-days", type=int, default=30)
    parser.add_argument("--keep-latest-artifact-runs-per-prefix", type=int, default=5)
    parser.add_argument(
        "--daemon-log-max-size-bytes",
        type=int,
        default=50 * 1024 * 1024,
    )
    parser.add_argument("--daemon-log-keep-backups", type=int, default=5)
    return parser.parse_args()


def parse_now(raw: str) -> datetime:
    if not raw:
        return datetime.now(timezone.utc)
    value = raw.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def resolve_output_dir(raw_output_dir: str, project_root: Path) -> Path:
    if raw_output_dir:
        return Path(raw_output_dir).expanduser().resolve()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return (
        project_root
        / "acceptance_artifacts"
        / f"r14_3b_retention_dry_run_{timestamp}"
    )


def run_dry_run(
    *,
    project_root: Path,
    output_dir: Path,
    config: RetentionConfig,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    output_dir.mkdir(parents=True, exist_ok=True)

    records = scan_inventory(project_root=project_root, output_dir=output_dir, now=now)
    referenced_report_paths = enrich_alert_records(records, project_root=project_root)
    apply_retention_decisions(
        records=records,
        project_root=project_root,
        output_dir=output_dir,
        referenced_report_paths=referenced_report_paths,
        config=config,
        now=now,
    )

    summary = build_summary(
        project_root=project_root,
        output_dir=output_dir,
        config=config,
        records=records,
        generated_at=now,
    )
    write_outputs(output_dir=output_dir, summary=summary)
    return summary


def scan_inventory(*, project_root: Path, output_dir: Path, now: datetime) -> list[ArtifactRecord]:
    records: list[ArtifactRecord] = []
    scan_roots = [
        project_root / "outputs" / "monitors",
        project_root / "outputs" / "alerts",
        project_root / "acceptance_artifacts",
        project_root / "state",
    ]
    for root in scan_roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            if is_relative_to(path.resolve(), output_dir):
                continue
            record = classify_path(path=path, project_root=project_root, now=now)
            if record is not None:
                records.append(record)
    return records


def classify_path(
    *,
    path: Path,
    project_root: Path,
    now: datetime,
) -> ArtifactRecord | None:
    relative = relative_to_root(path, project_root)
    parts = Path(relative).parts
    if len(parts) < 2:
        return None

    if parts[0] == "outputs" and parts[1] == "monitors":
        record = base_record(
            path=path,
            project_root=project_root,
            now=now,
            artifact_family="monitor",
            artifact_type=classify_monitor_type(path),
            project_id=parts[2] if len(parts) > 2 else "unknown",
        )
        if len(parts) > 3:
            record.details["monitor_run_id"] = parts[3]
        enrich_content_markers(record)
        return record

    if parts[0] == "outputs" and parts[1] == "alerts":
        record = base_record(
            path=path,
            project_root=project_root,
            now=now,
            artifact_family="alert",
            artifact_type=classify_alert_type(path),
            project_id=infer_alert_project_id(path),
        )
        enrich_content_markers(record)
        return record

    if parts[0] == "acceptance_artifacts":
        top_dir = parts[1] if len(parts) > 1 else ""
        prefix, has_timestamp = split_run_prefix(top_dir)
        record = base_record(
            path=path,
            project_root=project_root,
            now=now,
            artifact_family="acceptance",
            artifact_type="acceptance_artifact",
            project_id="acceptance_artifacts",
        )
        record.details.update(
            {
                "acceptance_run": top_dir,
                "acceptance_prefix": prefix,
                "acceptance_run_has_timestamp": has_timestamp,
            }
        )
        enrich_content_markers(record)
        return record

    if parts[0] == "state":
        name = path.name
        if name == "daemon.log":
            artifact_type = "daemon_log"
        elif name.startswith("daemon.log."):
            artifact_type = "daemon_log_backup"
        elif name in CORE_STATE_FILES:
            artifact_type = "state_core_file"
        else:
            return None

        project_id = "global"
        if len(parts) >= 3:
            project_id = parts[1]

        record = base_record(
            path=path,
            project_root=project_root,
            now=now,
            artifact_family="state",
            artifact_type=artifact_type,
            project_id=project_id,
        )
        if artifact_type in {"daemon_log", "daemon_log_backup"}:
            record.details["line_count"] = count_lines(path)
        enrich_content_markers(record)
        return record

    return None


def base_record(
    *,
    path: Path,
    project_root: Path,
    now: datetime,
    artifact_family: str,
    artifact_type: str,
    project_id: str,
) -> ArtifactRecord:
    stat = path.stat()
    mtime_dt = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
    age_seconds = max(0.0, (now - mtime_dt).total_seconds())
    return ArtifactRecord(
        path=path.resolve(),
        relative_path=relative_to_root(path, project_root),
        artifact_type=artifact_type,
        artifact_family=artifact_family,
        project_id=project_id,
        size_bytes=stat.st_size,
        mtime=mtime_dt.isoformat(),
        age_days=round(age_seconds / 86400, 3),
        suffix=path.suffix,
    )


def classify_monitor_type(path: Path) -> str:
    name = path.name
    if name.startswith("event_") and name.endswith("_final_llm_report.md"):
        return "monitor_event_report"
    if name.startswith("cycle_") and name.endswith("_summary_report.md"):
        return "monitor_cycle_report"
    if name == "final_llm_report.md":
        return "monitor_final_report"
    if name == "fix_plan.md":
        return "monitor_fix_plan"
    if name == "combined_evidence.log":
        return "monitor_evidence"
    if name == "remote_applied_fixes.json":
        return "monitor_recovery_state"
    return "monitor_file"


def classify_alert_type(path: Path) -> str:
    name = path.name
    parent = path.parent.name
    if name.endswith("_latest_alert.md"):
        return "alert_latest"
    if name.endswith("_alerts.jsonl"):
        return "alert_jsonl"
    if parent.endswith("_alerts") and path.suffix == ".json":
        return "alert_archive_json"
    if parent.endswith("_alerts") and path.suffix == ".md":
        return "alert_archive_markdown"
    return "alert_file"


def infer_alert_project_id(path: Path) -> str:
    name = path.name
    parent = path.parent.name
    if name.endswith("_latest_alert.md"):
        return name.removesuffix("_latest_alert.md")
    if name.endswith("_alerts.jsonl"):
        return name.removesuffix("_alerts.jsonl")
    if parent.endswith("_alerts"):
        return parent.removesuffix("_alerts")
    return "unknown"


def split_run_prefix(run_name: str) -> tuple[str, bool]:
    match = RUN_TIMESTAMP_RE.match(run_name)
    if not match:
        return run_name, False
    return match.group("prefix"), True


def enrich_content_markers(record: ArtifactRecord) -> None:
    haystack = f"{record.relative_path}\n{read_text_prefix(record.path)}".lower()
    record.contains_manual_escalation = any(
        marker in haystack for marker in RISK_MARKERS["manual"]
    )
    record.contains_gate_block = any(
        marker in haystack for marker in RISK_MARKERS["gate"]
    )
    record.contains_rollback_failure = any(
        marker in haystack for marker in RISK_MARKERS["rollback"]
    )


def read_text_prefix(path: Path, limit: int = 262_144) -> str:
    try:
        data = path.read_bytes()[:limit]
    except OSError:
        return ""
    return data.decode("utf-8", errors="replace")


def count_lines(path: Path) -> int:
    try:
        with path.open("rb") as handle:
            return sum(1 for _ in handle)
    except OSError:
        return 0


def enrich_alert_records(
    records: list[ArtifactRecord],
    *,
    project_root: Path,
) -> set[Path]:
    referenced_report_paths: set[Path] = set()
    archive_json_by_stem: dict[tuple[Path, str], dict[str, Any]] = {}

    for record in records:
        if record.artifact_family != "alert":
            continue
        if record.artifact_type not in {"alert_jsonl", "alert_archive_json"}:
            continue
        alert_items = load_alert_items(record.path, jsonl=record.artifact_type == "alert_jsonl")
        metadata = summarize_alert_items(alert_items, project_root=project_root)
        record.details.update(metadata)
        record.contains_manual_escalation = (
            record.contains_manual_escalation or metadata["contains_manual_escalation"]
        )
        record.contains_gate_block = (
            record.contains_gate_block or metadata["contains_gate_block"]
        )
        record.contains_rollback_failure = (
            record.contains_rollback_failure or metadata["contains_rollback_failure"]
        )
        referenced_report_paths.update(
            Path(path_text) for path_text in metadata["normalized_report_paths"]
        )
        if record.artifact_type == "alert_archive_json":
            archive_json_by_stem[(record.path.parent, record.path.stem)] = metadata

    for record in records:
        if record.artifact_type != "alert_archive_markdown":
            continue
        metadata = archive_json_by_stem.get((record.path.parent, record.path.stem))
        if not metadata:
            continue
        record.details.update(
            {
                "paired_json_report_paths": metadata["report_paths"],
                "paired_json_actions": metadata["actions"],
                "paired_json_statuses": metadata["statuses"],
                "paired_json_event_types": metadata["event_types"],
            }
        )
        if metadata["contains_manual_escalation"]:
            record.contains_manual_escalation = True
        if metadata["contains_gate_block"]:
            record.contains_gate_block = True
        if metadata["contains_rollback_failure"]:
            record.contains_rollback_failure = True

    return referenced_report_paths


def load_alert_items(path: Path, *, jsonl: bool) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    try:
        if jsonl:
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                if not line.strip():
                    continue
                parsed = json.loads(line)
                if isinstance(parsed, dict):
                    items.append(parsed)
        else:
            parsed = json.loads(path.read_text(encoding="utf-8", errors="replace"))
            if isinstance(parsed, dict):
                items.append(parsed)
    except (OSError, json.JSONDecodeError):
        return items
    return items


def summarize_alert_items(
    items: list[dict[str, Any]],
    *,
    project_root: Path,
) -> dict[str, Any]:
    report_paths: list[str] = []
    normalized_report_paths: list[Path] = []
    actions: set[str] = set()
    statuses: set[str] = set()
    event_types: set[str] = set()
    contains_gate_block = False
    contains_rollback_failure = False

    for item in items:
        for path_text in extract_report_paths(item):
            report_paths.append(path_text)
            normalized_report_paths.append(normalize_artifact_path(path_text, project_root))
        for key, bucket in [
            ("action", actions),
            ("status", statuses),
            ("strategy_layer", actions),
            ("event_type", event_types),
        ]:
            value = item.get(key)
            if value:
                bucket.add(str(value))
        downgrade_reason = str(item.get("downgrade_reason") or "")
        execution_result = str(item.get("execution_result") or "")
        rollback_result = str(item.get("rollback_result") or "")
        contains_gate_block = contains_gate_block or any(
            marker in f"{downgrade_reason} {execution_result}".lower()
            for marker in RISK_MARKERS["gate"]
        )
        contains_rollback_failure = contains_rollback_failure or any(
            marker in rollback_result.lower() for marker in RISK_MARKERS["rollback"]
        )

    lowered_actions = {item.lower() for item in actions | statuses}
    return {
        "report_paths": sorted(set(report_paths)),
        "normalized_report_paths": sorted(str(path) for path in set(normalized_report_paths)),
        "actions": sorted(actions),
        "statuses": sorted(statuses),
        "event_types": sorted(event_types),
        "contains_manual_escalation": "manual_escalation" in lowered_actions,
        "contains_gate_block": contains_gate_block,
        "contains_rollback_failure": contains_rollback_failure,
    }


def extract_report_paths(item: Any) -> list[str]:
    if isinstance(item, dict):
        paths: list[str] = []
        for key, value in item.items():
            if key == "report_paths" and isinstance(value, list):
                paths.extend(str(path) for path in value if path)
            else:
                paths.extend(extract_report_paths(value))
        return paths
    if isinstance(item, list):
        paths = []
        for value in item:
            paths.extend(extract_report_paths(value))
        return paths
    return []


def apply_retention_decisions(
    *,
    records: list[ArtifactRecord],
    project_root: Path,
    output_dir: Path,
    referenced_report_paths: set[Path],
    config: RetentionConfig,
    now: datetime,
) -> None:
    mark_latest_monitor_records(records, config.keep_latest_reports_per_project)
    mark_latest_alert_records(records, config.keep_latest_alerts_per_project)
    mark_latest_acceptance_runs(records, config.keep_latest_artifact_runs_per_prefix)
    mark_latest_daemon_backups(records, config.daemon_log_keep_backups)

    for record in records:
        if is_relative_to(record.path, output_dir):
            record.protect("current_dry_run_output_dir")
        if record.artifact_type == "state_core_file":
            record.protect("state_core_file")
        if record.artifact_type == "daemon_log":
            record.protect("current_daemon_log")
        if record.artifact_type == "monitor_recovery_state":
            record.protect("recovery_state_or_rollback_audit")
        if record.artifact_type == "alert_latest":
            record.protect("latest_alert_pointer")
        if record.artifact_type == "alert_jsonl":
            record.protect("alert_jsonl_audit_log")
        if record.referenced_by_alert:
            record.protect("referenced_by_alert")
        if record.contains_manual_escalation:
            record.protect("manual_escalation_or_operator_required")
        if record.contains_gate_block:
            record.protect("gate_block_or_forbidden_evidence")
        if record.contains_rollback_failure:
            record.protect("rollback_failure_evidence")
        if record.is_latest:
            record.protect(latest_protection_reason(record))
        if record.artifact_type == "acceptance_artifact" and not record.details.get(
            "acceptance_run_has_timestamp"
        ):
            record.protect("acceptance_run_without_timestamp")

    referenced_report_path_text = {str(path) for path in referenced_report_paths}
    for record in records:
        if record.artifact_family == "monitor" and str(record.path) in referenced_report_path_text:
            record.referenced_by_alert = True
            record.protect("referenced_by_alert")

    for record in records:
        assign_candidate_decision(record, records, project_root, config, now)


def mark_latest_monitor_records(records: list[ArtifactRecord], keep_count: int) -> None:
    by_project: dict[str, list[ArtifactRecord]] = {}
    for record in records:
        if record.artifact_family == "monitor":
            by_project.setdefault(record.project_id, []).append(record)
    for project_records in by_project.values():
        for index, record in enumerate(sort_newest(project_records), start=1):
            record.details["project_monitor_rank"] = index
            if index <= keep_count:
                record.is_latest = True


def mark_latest_alert_records(records: list[ArtifactRecord], keep_count: int) -> None:
    by_project: dict[str, list[ArtifactRecord]] = {}
    for record in records:
        if record.artifact_type in {"alert_archive_json", "alert_archive_markdown"}:
            by_project.setdefault(record.project_id, []).append(record)
    for project_records in by_project.values():
        for index, record in enumerate(sort_newest(project_records), start=1):
            record.details["project_alert_rank"] = index
            if index <= keep_count:
                record.is_latest = True


def mark_latest_acceptance_runs(records: list[ArtifactRecord], keep_count: int) -> None:
    run_records: dict[tuple[str, str], list[ArtifactRecord]] = {}
    for record in records:
        if record.artifact_type != "acceptance_artifact":
            continue
        prefix = str(record.details.get("acceptance_prefix") or "")
        run = str(record.details.get("acceptance_run") or "")
        run_records.setdefault((prefix, run), []).append(record)

    runs_by_prefix: dict[str, list[tuple[str, float]]] = {}
    for (prefix, run), items in run_records.items():
        newest_mtime = max(parse_mtime(item.mtime).timestamp() for item in items)
        runs_by_prefix.setdefault(prefix, []).append((run, newest_mtime))

    protected_runs: set[tuple[str, str]] = set()
    for prefix, runs in runs_by_prefix.items():
        for index, (run, _) in enumerate(
            sorted(runs, key=lambda item: item[1], reverse=True),
            start=1,
        ):
            for item in run_records[(prefix, run)]:
                item.details["acceptance_run_rank"] = index
            if index <= keep_count:
                protected_runs.add((prefix, run))

    for record in records:
        if record.artifact_type != "acceptance_artifact":
            continue
        prefix = str(record.details.get("acceptance_prefix") or "")
        run = str(record.details.get("acceptance_run") or "")
        if (prefix, run) in protected_runs:
            record.is_latest = True


def mark_latest_daemon_backups(records: list[ArtifactRecord], keep_count: int) -> None:
    by_project: dict[str, list[ArtifactRecord]] = {}
    for record in records:
        if record.artifact_type == "daemon_log_backup":
            by_project.setdefault(record.project_id, []).append(record)
    for project_records in by_project.values():
        for index, record in enumerate(sort_newest(project_records), start=1):
            record.details["daemon_backup_rank"] = index
            if index <= keep_count:
                record.is_latest = True


def latest_protection_reason(record: ArtifactRecord) -> str:
    if record.artifact_family == "monitor":
        return "within_latest_report_keep_limit"
    if record.artifact_family == "alert":
        return "within_latest_alert_keep_limit"
    if record.artifact_family == "acceptance":
        return "within_latest_acceptance_run_keep_limit"
    if record.artifact_type == "daemon_log_backup":
        return "within_daemon_backup_keep_limit"
    return "within_latest_keep_limit"


def assign_candidate_decision(
    record: ArtifactRecord,
    records: list[ArtifactRecord],
    project_root: Path,
    config: RetentionConfig,
    now: datetime,
) -> None:
    if record.artifact_type == "daemon_log":
        assign_daemon_rotation_plan(record, config, now)
        return

    if record.protected:
        return

    if record.artifact_family == "monitor":
        reasons = []
        if record.age_days > config.reports_retention_days:
            reasons.append("older_than_reports_retention_days")
        if int(record.details.get("project_monitor_rank") or 0) > (
            config.keep_latest_reports_per_project
        ):
            reasons.append("exceeds_project_report_count_limit")
        for reason in reasons:
            record.add_candidate(
                action="delete_candidate",
                reason=reason,
                risk_level=monitor_risk(record),
            )
        return

    if record.artifact_type in {"alert_archive_json", "alert_archive_markdown"}:
        if not alert_has_existing_report_reference(record, project_root):
            record.protect("alert_missing_or_unverified_report_reference")
            record.risk_level = "high"
            return
        if record.age_days > config.alerts_retention_days and int(
            record.details.get("project_alert_rank") or 0
        ) > config.keep_latest_alerts_per_project:
            record.add_candidate(
                action="delete_candidate",
                reason="older_than_alert_retention_days",
                risk_level="low",
            )
            record.add_candidate(
                action="delete_candidate",
                reason="exceeds_alert_count_limit",
                risk_level="low",
            )
        return

    if record.artifact_type == "acceptance_artifact":
        if record.age_days > config.acceptance_artifacts_retention_days and int(
            record.details.get("acceptance_run_rank") or 0
        ) > config.keep_latest_artifact_runs_per_prefix:
            record.add_candidate(
                action="delete_candidate",
                reason="older_than_acceptance_artifacts_retention_days",
                risk_level="low",
            )
            record.add_candidate(
                action="delete_candidate",
                reason="exceeds_acceptance_run_keep_limit",
                risk_level="low",
            )
        return

    if record.artifact_type == "daemon_log_backup":
        if int(record.details.get("daemon_backup_rank") or 0) > config.daemon_log_keep_backups:
            record.add_candidate(
                action="delete_candidate",
                reason="exceeds_daemon_backup_keep_limit",
                risk_level="low",
            )


def assign_daemon_rotation_plan(
    record: ArtifactRecord,
    config: RetentionConfig,
    now: datetime,
) -> None:
    if record.size_bytes <= config.daemon_log_max_size_bytes:
        return
    stamp = now.strftime("%Y%m%d_%H%M%S")
    record.details.update(
        {
            "would_copy_to": f"{record.relative_path}.{stamp}",
            "would_truncate": False,
            "would_delete_old_backups": False,
            "rotation_reason": "daemon_log_exceeds_size_threshold",
            "daemon_log_max_size_bytes": config.daemon_log_max_size_bytes,
        }
    )
    record.add_candidate(
        action="rotate_copy_plan",
        reason="daemon_log_exceeds_size_threshold",
        risk_level="medium",
    )
    record.estimated_reclaim_bytes = 0


def monitor_risk(record: ArtifactRecord) -> str:
    if record.artifact_type in {"monitor_event_report", "monitor_cycle_report"}:
        return "medium"
    return "low"


def alert_has_existing_report_reference(record: ArtifactRecord, project_root: Path) -> bool:
    raw_paths = record.details.get("report_paths") or record.details.get(
        "paired_json_report_paths"
    )
    if not raw_paths:
        return False
    for raw_path in raw_paths:
        if normalize_artifact_path(str(raw_path), project_root).exists():
            return True
    return False


def build_summary(
    *,
    project_root: Path,
    output_dir: Path,
    config: RetentionConfig,
    records: list[ArtifactRecord],
    generated_at: datetime,
) -> dict[str, Any]:
    rows = [record.to_dict() for record in records]
    candidates = [row for row in rows if row["candidate"]]
    protected = [row for row in rows if row["protected"]]
    delete_candidates = [
        row for row in candidates if row["candidate_action"] == "delete_candidate"
    ]
    daemon_rotation_plans = [
        row for row in candidates if row["candidate_action"] == "rotate_copy_plan"
    ]
    metrics = {
        "inventory_count": len(rows),
        "candidate_count": len(candidates),
        "delete_candidate_count": len(delete_candidates),
        "protected_count": len(protected),
        "daemon_rotation_plan_count": len(daemon_rotation_plans),
        "estimated_reclaim_bytes": sum(
            int(row["estimated_reclaim_bytes"]) for row in delete_candidates
        ),
        "total_scanned_bytes": sum(int(row["size_bytes"]) for row in rows),
        "by_artifact_family": count_by(rows, "artifact_family"),
        "by_artifact_type": count_by(rows, "artifact_type"),
        "by_risk_level": count_by(candidates, "risk_level"),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at.isoformat(),
        "mode": "dry_run",
        "project_root": str(project_root),
        "output_dir": str(output_dir),
        "safety": {
            "delete_executed": False,
            "move_executed": False,
            "truncate_executed": False,
            "rotation_executed": False,
            "state_modified": False,
        },
        "config": config.to_dict(),
        "metrics": metrics,
        "inventory": rows,
        "candidates": candidates,
        "protected": protected,
        "daemon_rotation_plans": daemon_rotation_plans,
        "outputs": {
            "inventory_json": "retention_inventory.json",
            "plan_json": "retention_plan.json",
            "candidates_jsonl": "retention_candidates.jsonl",
            "protected_jsonl": "retention_protected.jsonl",
            "summary_json": "retention_summary.json",
            "markdown_report": "retention_dry_run_report.md",
        },
    }


def count_by(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for row in rows:
        value = str(row.get(key) or "")
        result[value] = result.get(value, 0) + 1
    return dict(sorted(result.items()))


def write_outputs(*, output_dir: Path, summary: dict[str, Any]) -> None:
    inventory = {
        "schema_version": summary["schema_version"],
        "generated_at": summary["generated_at"],
        "project_root": summary["project_root"],
        "inventory": summary["inventory"],
    }
    plan = {
        "schema_version": summary["schema_version"],
        "mode": summary["mode"],
        "generated_at": summary["generated_at"],
        "project_root": summary["project_root"],
        "output_dir": summary["output_dir"],
        "config": summary["config"],
        "safety_rules": [
            "不删除、不移动、不清空、不 truncate",
            "alert 指向的 report 始终保护",
            "latest alert 与 alert JSONL 审计日志始终保护",
            "manual escalation、gate block、rollback failure 证据始终保护",
            "当前 daemon.log 只生成复制式轮转计划，不执行轮转",
            "state/project_status.json 与 state/events.jsonl 等状态文件始终保护",
        ],
    }
    (output_dir / "retention_inventory.json").write_text(
        json.dumps(inventory, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "retention_plan.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_jsonl(output_dir / "retention_candidates.jsonl", summary["candidates"])
    write_jsonl(output_dir / "retention_protected.jsonl", summary["protected"])
    (output_dir / "retention_summary.json").write_text(
        json.dumps(
            {
                key: value
                for key, value in summary.items()
                if key not in {"inventory", "candidates", "protected"}
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (output_dir / "retention_dry_run_report.md").write_text(
        render_markdown(summary),
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def render_markdown(summary: dict[str, Any]) -> str:
    metrics = summary["metrics"]
    lines = [
        "# R14-3b Retention / Log Rotation Dry-Run 报告",
        "",
        f"- 生成时间: `{summary['generated_at']}`",
        f"- 模式: `{summary['mode']}`",
        f"- 项目根目录: `{summary['project_root']}`",
        f"- 输出目录: `{summary['output_dir']}`",
        "",
        "## 安全边界",
        "",
        "- 本次运行只做 inventory、分类、保护判定和候选计划。",
        "- 不会删除文件，不会移动文件，不会清空文件，也不会 truncate `daemon.log`。",
        "- `daemon.log` 超过阈值时只生成复制式轮转 dry-run 计划，当前日志文件仍受保护。",
        "",
        "## 汇总指标",
        "",
        "| 指标 | 值 |",
        "| --- | --- |",
        f"| 扫描文件数 | `{metrics['inventory_count']}` |",
        f"| 候选项总数 | `{metrics['candidate_count']}` |",
        f"| 删除候选数 | `{metrics['delete_candidate_count']}` |",
        f"| 保护项数量 | `{metrics['protected_count']}` |",
        f"| daemon.log 轮转计划数 | `{metrics['daemon_rotation_plan_count']}` |",
        f"| 预计可释放字节数 | `{metrics['estimated_reclaim_bytes']}` |",
        f"| 扫描总字节数 | `{metrics['total_scanned_bytes']}` |",
        "",
        "## 候选清单",
        "",
    ]
    lines.extend(render_table(summary["candidates"], limit=40))
    lines.extend(["", "## daemon.log 轮转计划", ""])
    rotation_plans = summary["daemon_rotation_plans"]
    if not rotation_plans:
        lines.append("- <none>")
    else:
        for plan in rotation_plans:
            details = plan["details"]
            lines.extend(
                [
                    f"- 当前日志: `{plan['path']}`",
                    f"  - 当前大小: `{plan['size_bytes']}` bytes",
                    f"  - would_copy_to: `{details.get('would_copy_to')}`",
                    f"  - would_truncate: `{details.get('would_truncate')}`",
                    f"  - would_delete_old_backups: `{details.get('would_delete_old_backups')}`",
                    f"  - 风险等级: `{plan['risk_level']}`",
                ]
            )
    lines.extend(["", "## 保护清单", ""])
    lines.extend(render_table(summary["protected"], limit=40))
    lines.extend(
        [
            "",
            "## 策略配置",
            "",
            "```json",
            json.dumps(summary["config"], ensure_ascii=False, indent=2),
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def render_table(rows: list[dict[str, Any]], *, limit: int) -> list[str]:
    if not rows:
        return ["- <none>"]
    lines = [
        "| 路径 | 类型 | 项目 | 动作 | 原因 | 风险 | 预计释放 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows[:limit]:
        reasons = row["candidate_reason"] if row["candidate"] else row["protected_reason"]
        action = row["candidate_action"] or "protect"
        lines.append(
            "| "
            f"`{row['path']}` | "
            f"`{row['artifact_type']}` | "
            f"`{row['project_id']}` | "
            f"`{action}` | "
            f"`{reasons}` | "
            f"`{row['risk_level']}` | "
            f"`{row['estimated_reclaim_bytes']}` |"
        )
    if len(rows) > limit:
        lines.append(f"| ... | ... | ... | ... | 另有 {len(rows) - limit} 项 | ... | ... |")
    return lines


def sort_newest(records: list[ArtifactRecord]) -> list[ArtifactRecord]:
    return sorted(records, key=lambda record: parse_mtime(record.mtime), reverse=True)


def parse_mtime(value: str) -> datetime:
    return datetime.fromisoformat(value)


def normalize_artifact_path(raw_path: str, project_root: Path) -> Path:
    path = Path(raw_path)
    if not path.is_absolute():
        path = project_root / path
    return path.resolve()


def relative_to_root(path: Path, project_root: Path) -> str:
    try:
        return str(path.resolve().relative_to(project_root.resolve()))
    except ValueError:
        return str(path.resolve())


def is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


if __name__ == "__main__":
    raise SystemExit(main())
