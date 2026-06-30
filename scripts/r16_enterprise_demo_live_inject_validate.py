#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from monitors import MonitorLoop, ProjectRegistry
from monitors.project_registry import ProjectConfig


DEFAULT_SAFE_EVENTS = ["cache_write_failed", "optional_dependency_missing", "worker_overload", "queue_backpressure"]
DEFAULT_MANUAL_EVENTS = ["disk_full"]


@dataclass
class FileBackup:
    path: Path
    existed: bool
    content: bytes = b""

    @classmethod
    def capture(cls, path: Path) -> FileBackup:
        if path.exists():
            return cls(path=path, existed=True, content=path.read_bytes())
        return cls(path=path, existed=False)

    def restore(self) -> None:
        if self.existed:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_bytes(self.content)
            return

        if self.path.exists():
            self.path.unlink()


def main() -> int:
    args = parse_args()
    output_dir = resolve_output_dir(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    project = ProjectRegistry(args.config).get(args.project)
    runtime_dir = Path(project.remote_project_dir or project.project_dir).expanduser()
    if not runtime_dir.exists():
        raise FileNotFoundError(f"runtime project dir not found: {runtime_dir}")

    safe_events = parse_event_list(args.safe_events, DEFAULT_SAFE_EVENTS)
    manual_events = parse_event_list(args.manual_events, DEFAULT_MANUAL_EVENTS)

    backup_dir = output_dir / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    file_backups = capture_runtime_file_backups(runtime_dir)
    state_backup = capture_state_backup(project.project_id, backup_dir)

    summary: dict[str, Any] = {
        "generated_at": now_text(),
        "project_id": project.project_id,
        "runtime_dir": str(runtime_dir),
        "report_mode": args.report_mode,
        "require_llm": bool(args.require_llm),
        "output_dir": str(output_dir),
        "safe_events": safe_events,
        "manual_events": manual_events,
        "safe_rows": [],
        "manual_rows": [],
        "restore": {},
        "failed_checks": [],
        "conclusion": "FAIL",
    }

    try:
        for event_type in safe_events:
            row = run_scenario(
                project=project,
                runtime_dir=runtime_dir,
                event_type=event_type,
                scenario_kind="safe",
                report_mode=args.report_mode,
                require_llm=args.require_llm,
            )
            summary["safe_rows"].append(row)

        for event_type in manual_events:
            row = run_scenario(
                project=project,
                runtime_dir=runtime_dir,
                event_type=event_type,
                scenario_kind="manual",
                report_mode=args.report_mode,
                require_llm=args.require_llm,
            )
            summary["manual_rows"].append(row)

        summary["failed_checks"] = collect_failed_checks(summary)
        summary["conclusion"] = "PASS" if not summary["failed_checks"] else "FAIL"

    finally:
        for backup in file_backups:
            backup.restore()
        restore_state_backup(project.project_id, state_backup)
        summary["restore"] = {
            "runtime_files_restored": [str(item.path) for item in file_backups],
            "state_restored": bool(state_backup),
            "restored_at": now_text(),
        }
        write_summary(output_dir, summary)

    print(f"enterprise_live_output_dir={output_dir}")
    print(f"safe_rows={len(summary['safe_rows'])}")
    print(f"manual_rows={len(summary['manual_rows'])}")
    print(f"conclusion={summary['conclusion']}")
    if summary["failed_checks"]:
        print("failed_checks:")
        for item in summary["failed_checks"]:
            print(f"- {item}")

    return 0 if summary["conclusion"] == "PASS" else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Inject real-shaped errors into enterprise_demo_local runtime project "
            "and validate MonitorLoop reports, safe recovery, notifications, and audit."
        )
    )
    parser.add_argument("--config", default="configs/projects.yaml")
    parser.add_argument("--project", default="enterprise_demo_local")
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--report-mode", choices=["auto", "llm", "rule"], default="auto")
    parser.add_argument(
        "--require-llm",
        action="store_true",
        help="Fail if reports fall back to the rule report agent.",
    )
    parser.add_argument(
        "--safe-events",
        default=",".join(DEFAULT_SAFE_EVENTS),
        help="Comma-separated safe event types to inject.",
    )
    parser.add_argument(
        "--manual-events",
        default=",".join(DEFAULT_MANUAL_EVENTS),
        help="Comma-separated non-safe/manual event types to inject.",
    )
    return parser.parse_args()


def parse_event_list(raw_value: str, default: list[str]) -> list[str]:
    if raw_value.strip().lower() in {"none", "no", "false", "-"}:
        return []

    values = [item.strip() for item in raw_value.split(",") if item.strip()]
    return values or list(default)


def resolve_output_dir(raw_output_dir: str) -> Path:
    if raw_output_dir:
        return Path(raw_output_dir).expanduser().resolve()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return PROJECT_ROOT / "outputs" / "enterprise_demo_live_injection" / timestamp


def capture_runtime_file_backups(runtime_dir: Path) -> list[FileBackup]:
    paths = [
        runtime_dir / "config.json",
        runtime_dir / "outputs" / "service.log",
        runtime_dir / "outputs" / "service_result.json",
        runtime_dir / "outputs" / "service_started.ok",
    ]
    return [FileBackup.capture(path) for path in paths]


def capture_state_backup(project_id: str, backup_dir: Path) -> Path | None:
    state_path = PROJECT_ROOT / "state" / project_id
    if not state_path.exists():
        return None

    target = backup_dir / "state" / project_id
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(state_path, target)
    return target


def restore_state_backup(project_id: str, backup_path: Path | None) -> None:
    state_path = PROJECT_ROOT / "state" / project_id
    if state_path.exists():
        shutil.rmtree(state_path)

    if backup_path is not None and backup_path.exists():
        state_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(backup_path, state_path)


def run_scenario(
    *,
    project: ProjectConfig,
    runtime_dir: Path,
    event_type: str,
    scenario_kind: str,
    report_mode: str,
    require_llm: bool,
) -> dict[str, Any]:
    smoke_id = f"r16-enterprise-{scenario_kind}-{event_type}-{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
    log_text = log_text_for_event(event_type=event_type, smoke_id=smoke_id)
    config = scenario_config(
        base_config=load_json(runtime_dir / "config.json"),
        event_type=event_type,
        scenario_kind=scenario_kind,
    )

    write_json(runtime_dir / "config.json", config)
    service_log = runtime_dir / "outputs" / "service.log"
    service_log.parent.mkdir(parents=True, exist_ok=True)
    service_log.write_text(log_text + "\n", encoding="utf-8")

    scenario_project = clone_project_for_scenario(
        project=project,
        scenario_kind=scenario_kind,
    )
    reports_before = snapshot_report_files()
    alerts_before = snapshot_alert_files(project.project_id)

    loop = MonitorLoop(
        project=scenario_project,
        agent_depth="balanced",
        report_mode=report_mode,
        output_root="outputs/monitors",
        state_dir="state",
        enable_persistent_state=True,
    )
    events = loop.run_once()

    reports_after = snapshot_report_files()
    alerts_after = snapshot_alert_files(project.project_id)
    new_reports = sorted(str(path) for path in (reports_after - reports_before))
    new_alerts = sorted(str(path) for path in (alerts_after - alerts_before))

    row = {
        "event_type": event_type,
        "scenario_kind": scenario_kind,
        "smoke_id": smoke_id,
        "events_detected": [event.event_type for event in events],
        "reports_generated": list(loop.reports_generated),
        "new_report_files": new_reports,
        "new_alert_files": new_alerts,
        "llm_report_files": [
            path for path in [*new_reports, *loop.reports_generated] if "llm" in Path(path).name
        ],
        "rule_report_files": [
            path for path in [*new_reports, *loop.reports_generated] if "rule" in Path(path).name
        ],
        "llm_fallback_used": bool(loop._current_cycle_llm_fallback_used),
        "state_status": loop.project_state.status,
        "last_report_path": loop.project_state.last_report_path,
        "runtime_config_after_scenario": load_json(runtime_dir / "config.json"),
    }
    row.update(validate_scenario_row(row, scenario_kind=scenario_kind, require_llm=require_llm))
    return row


def clone_project_for_scenario(
    *,
    project: ProjectConfig,
    scenario_kind: str,
) -> ProjectConfig:
    import copy

    cloned = copy.deepcopy(project)
    if scenario_kind == "safe":
        cloned.policy.auto_recovery_dry_run = False
    return cloned


def scenario_config(
    *,
    base_config: dict[str, Any],
    event_type: str,
    scenario_kind: str,
) -> dict[str, Any]:
    config = dict(base_config)
    config.update(
        {
            "simulate_disk_full": False,
            "simulate_python_env_mismatch": False,
            "fail_on_python_env": False,
            "simulate_worker_overload": False,
            "metrics_host": "127.0.0.1",
            "metrics_port": 9101,
            "worker_concurrency": 2,
            "prefetch_count": 2,
        }
    )

    if scenario_kind != "safe":
        return config

    if event_type == "network_port":
        config["metrics_port"] = 9100
        config["simulate_port_conflict"] = True
    elif event_type == "cache_write_failed":
        config["simulate_disk_full"] = True
    elif event_type == "optional_dependency_missing":
        config["simulate_python_env_mismatch"] = True
    elif event_type == "worker_overload":
        config["simulate_worker_overload"] = True
        config["worker_concurrency"] = 8
    elif event_type == "queue_backpressure":
        config["prefetch_count"] = 64
    else:
        raise ValueError(f"enterprise demo safe injection not mapped: {event_type}")

    return config


def log_text_for_event(*, event_type: str, smoke_id: str) -> str:
    if event_type == "network_port":
        return "\n".join(
            [
                f"2026-06-30T12:00:00Z [{smoke_id}] [metrics] simulated real enterprise runtime failure",
                f"2026-06-30T12:00:00Z [{smoke_id}] OSError: [Errno 98] Address already in use while binding metrics_port=9100",
                f"2026-06-30T12:00:00Z [{smoke_id}] primary_failure=Address already in use event_type_hint=network_port candidate_fix_id=fix-network-1",
            ]
        )

    if event_type == "cache_write_failed":
        return "\n".join(
            [
                f"2026-06-30T12:00:00Z [{smoke_id}] [cache] WARNING: cache write failed while writing feature cache artifact",
                f"2026-06-30T12:00:00Z [{smoke_id}] [cache] OSError: [Errno 28] No space left on device: '/tmp/acme_order_cache/features_{smoke_id}.bin'",
                f"2026-06-30T12:00:00Z [{smoke_id}] [cache] fallback: continue with in-memory feature cache",
            ]
        )

    if event_type == "optional_dependency_missing":
        return "\n".join(
            [
                f"2026-06-30T12:00:00Z [{smoke_id}] [env] optional dependency missing: internal risk sdk unavailable",
                f"2026-06-30T12:00:00Z [{smoke_id}] [env] optional dependency fallback activated for internal risk scoring",
                f"2026-06-30T12:00:00Z [{smoke_id}] [fallback] continue with local rule engine because optional dependency fallback is available.",
            ]
        )

    if event_type == "worker_overload":
        return "\n".join(
            [
                f"2026-06-30T12:00:00Z [{smoke_id}] [worker] worker overload: worker_concurrency=8 is too high for startup queue",
                f"2026-06-30T12:00:00Z [{smoke_id}] [worker] worker pool exhausted; concurrency too high",
                f"2026-06-30T12:00:00Z [{smoke_id}] [worker] too many workers configured for enterprise order startup",
            ]
        )

    if event_type == "queue_backpressure":
        return "\n".join(
            [
                f"2026-06-30T12:00:00Z [{smoke_id}] [queue] queue backpressure detected for order ingestion stream",
                f"2026-06-30T12:00:00Z [{smoke_id}] [queue] prefetch too high; consumer lag too high",
                f"2026-06-30T12:00:00Z [{smoke_id}] [queue] max_inflight exhausted while processing enterprise orders",
            ]
        )

    if event_type == "disk_full":
        return "\n".join(
            [
                f"2026-06-30T12:00:00Z [{smoke_id}] [storage] persistent volume write failed for order ledger",
                f"2026-06-30T12:00:00Z [{smoke_id}] OSError: [Errno 28] No space left on device: '/var/lib/order-service/orders.db'",
                f"2026-06-30T12:00:00Z [{smoke_id}] operator_action_required=true domain=disk_full",
            ]
        )

    if event_type == "process_crash":
        return "\n".join(
            [
                f"2026-06-30T12:00:00Z [{smoke_id}] systemd[1]: enterprise-order.service: Main process exited, code=dumped, status=11/SEGV",
                f"2026-06-30T12:00:00Z [{smoke_id}] kernel: segmentation fault in enterprise order worker",
                f"2026-06-30T12:00:00Z [{smoke_id}] kernel: core dumped while processing order reconciliation task",
            ]
        )

    raise ValueError(f"unsupported injection event_type: {event_type}")


def validate_scenario_row(
    row: dict[str, Any],
    *,
    scenario_kind: str,
    require_llm: bool,
) -> dict[str, Any]:
    event_type = row["event_type"]
    event_detected = event_type in row["events_detected"]
    reports_ok = bool(row["reports_generated"] or row["new_report_files"])
    alerts_ok = bool(row["new_alert_files"])
    llm_ok = bool(row["llm_report_files"]) and not row["llm_fallback_used"]

    if scenario_kind == "safe":
        config = row["runtime_config_after_scenario"]
        if event_type == "network_port":
            safe_effect_ok = config.get("metrics_port") == 9101
        elif event_type == "cache_write_failed":
            safe_effect_ok = config.get("simulate_disk_full") is False
        elif event_type == "optional_dependency_missing":
            safe_effect_ok = config.get("simulate_python_env_mismatch") is False
        elif event_type == "worker_overload":
            safe_effect_ok = config.get("worker_concurrency") == 2
        elif event_type == "queue_backpressure":
            safe_effect_ok = config.get("prefetch_count") == 2
        else:
            safe_effect_ok = False
        status_ok = row["state_status"] == "recovered"
        manual_ok = True
    else:
        safe_effect_ok = True
        status_ok = row["state_status"] == "manual_escalation"
        manual_ok = True

    passed = all([event_detected, reports_ok, alerts_ok, safe_effect_ok, status_ok, manual_ok])
    if require_llm:
        passed = passed and llm_ok

    return {
        "event_detected_ok": event_detected,
        "reports_ok": reports_ok,
        "alerts_ok": alerts_ok,
        "safe_effect_ok": safe_effect_ok,
        "status_ok": status_ok,
        "llm_ok": llm_ok,
        "passed": passed,
    }


def collect_failed_checks(summary: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    for group_name in ["safe_rows", "manual_rows"]:
        for row in summary[group_name]:
            if row.get("passed"):
                continue
            for key in [
                "event_detected_ok",
                "reports_ok",
                "alerts_ok",
                "safe_effect_ok",
                "status_ok",
                "llm_ok",
            ]:
                if key == "llm_ok" and not summary.get("require_llm"):
                    continue
                if row.get(key) is False:
                    failures.append(f"{row['event_type']}: {key}")
    return failures


def snapshot_report_files() -> set[Path]:
    root = PROJECT_ROOT / "outputs" / "monitors"
    if not root.exists():
        return set()
    return {path for path in root.rglob("*.md") if path.is_file()}


def snapshot_alert_files(project_id: str) -> set[Path]:
    root = PROJECT_ROOT / "outputs" / "alerts"
    if not root.exists():
        return set()
    patterns = [
        f"{project_id}_alerts.jsonl",
        f"{project_id}_latest_alert.md",
        f"{project_id}_alerts/*.md",
        f"{project_id}_alerts/*.json",
    ]
    result: set[Path] = set()
    for pattern in patterns:
        result.update(path for path in root.glob(pattern) if path.is_file())
    return result


def write_summary(output_dir: Path, summary: dict[str, Any]) -> None:
    json_path = output_dir / "enterprise_demo_live_injection_summary.json"
    json_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    md_path = output_dir / "ENTERPRISE_DEMO_LIVE_INJECTION_SUMMARY.md"
    lines = [
        "# Enterprise Demo Live Injection Summary",
        "",
        f"- generated_at: `{summary['generated_at']}`",
        f"- conclusion: `{summary['conclusion']}`",
        f"- project_id: `{summary['project_id']}`",
        f"- runtime_dir: `{summary['runtime_dir']}`",
        f"- report_mode: `{summary['report_mode']}`",
        f"- require_llm: `{summary['require_llm']}`",
        "",
        "## Safe Recovery",
        "",
        "| event_type | detected | status | reports | alerts | safe_effect | llm | fallback |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in summary["safe_rows"]:
        lines.append(
            "| "
            f"{row['event_type']} | "
            f"{row['event_detected_ok']} | "
            f"{row['state_status']} | "
            f"{row['reports_ok']} | "
            f"{row['alerts_ok']} | "
            f"{row['safe_effect_ok']} | "
            f"{row['llm_ok']} | "
            f"{row['llm_fallback_used']} |"
        )

    lines.extend(
        [
            "",
            "## Manual / Audit",
            "",
            "| event_type | detected | status | reports | alerts | llm | fallback |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in summary["manual_rows"]:
        lines.append(
            "| "
            f"{row['event_type']} | "
            f"{row['event_detected_ok']} | "
            f"{row['state_status']} | "
            f"{row['reports_ok']} | "
            f"{row['alerts_ok']} | "
            f"{row['llm_ok']} | "
            f"{row['llm_fallback_used']} |"
        )

    lines.extend(["", "## Failed Checks", ""])
    if summary["failed_checks"]:
        for item in summary["failed_checks"]:
            lines.append(f"- {item}")
    else:
        lines.append("- <none>")

    lines.extend(
        [
            "",
            "## Restore",
            "",
            f"- restored_at: `{summary.get('restore', {}).get('restored_at', '')}`",
            f"- state_restored: `{summary.get('restore', {}).get('state_restored', False)}`",
            "",
            "## Report Files",
            "",
        ]
    )
    for row in [*summary["safe_rows"], *summary["manual_rows"]]:
        lines.append(f"### {row['event_type']}")
        for path in row.get("reports_generated", []):
            lines.append(f"- `{path}`")
        for path in row.get("new_report_files", []):
            lines.append(f"- `{path}`")
        lines.append("")

    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def now_text() -> str:
    return datetime.now().isoformat(timespec="seconds")


if __name__ == "__main__":
    raise SystemExit(main())
