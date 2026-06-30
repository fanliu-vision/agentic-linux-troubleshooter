#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import recovery.auto_recovery_runtime_controls as runtime_controls
from detectors import ErrorEvent, ErrorEventDetector
from fixers.apply_executor import SafeApplyExecutor
from monitors.project_registry import NotificationConfig, PolicyConfig, ProjectConfig
from notifiers import NotificationManager
from recovery.auto_recovery_runner import AutoRecoveryRunner, AutoRecoveryResult
from safe_recovery.registry import (
    SAFE_RECOVERY_FIX_IDS,
    SafeRecoveryFieldCandidate,
    SafeRecoverySpec,
    get_safe_recovery_spec_for_event_type,
    iter_safe_recovery_specs,
)
from safe_recovery.semantics import (
    SEMANTIC_DISABLE_BOOL,
    SEMANTIC_LOWER_INT,
    SEMANTIC_PORT_AVAILABLE,
    SEMANTIC_SAFE_ENUM_DOWNGRADE,
)
from scripts.r16_isolated_fault_injection_validate import (
    HIGH_RISK_INJECTION_LOGS,
    SAFE_INJECTION_LOGS,
    first_matching_event,
)
from sessions import TroubleshootingSession


DEFAULT_HIGH_RISK_EVENT_TYPES = [
    "disk_full",
    "python_env",
    "process_crash",
    "container_k8s",
    "auth_cert",
    "permission_denied",
]


def main() -> int:
    args = parse_args()
    output_dir = resolve_output_dir(args.output_dir)
    safe_event_types = parse_event_type_filter(args.safe_event_types)
    high_risk_event_types = parse_event_type_filter(args.high_risk_event_types)

    original_runtime_port_probe = runtime_controls._is_tcp_port_available
    original_apply_port_probe = SafeApplyExecutor._is_tcp_port_available
    runtime_controls._is_tcp_port_available = lambda host, port: True
    SafeApplyExecutor._is_tcp_port_available = staticmethod(lambda host, port: True)
    try:
        summary = build_live_injection_summary(
            output_dir=output_dir,
            report_mode=args.report_mode,
            safe_event_types=safe_event_types,
            high_risk_event_types=high_risk_event_types,
        )
    finally:
        runtime_controls._is_tcp_port_available = original_runtime_port_probe
        SafeApplyExecutor._is_tcp_port_available = original_apply_port_probe

    write_summary_markdown(output_dir, summary)
    write_summary_json(output_dir, summary)

    print(f"live_injection_output_dir={output_dir}")
    print(f"safe_rows={len(summary['safe_rows'])}")
    print(f"high_risk_rows={len(summary['high_risk_rows'])}")
    print(f"conclusion={summary['conclusion']}")
    return 0 if summary["conclusion"] == "PASS" else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "R16 isolated live fault injection validation for reports, "
            "safe auto recovery, and non-safe notification/audit."
        )
    )
    parser.add_argument("--output-dir", default="")
    parser.add_argument(
        "--report-mode",
        choices=["auto", "rule", "llm"],
        default="auto",
        help="TroubleshootingSession report mode.",
    )
    parser.add_argument(
        "--safe-event-types",
        default="",
        help="Comma-separated safe event_type filter. Defaults to all safe domains.",
    )
    parser.add_argument(
        "--high-risk-event-types",
        default="",
        help=(
            "Comma-separated high-risk event_type filter. Defaults to a representative "
            "non-safe set."
        ),
    )
    return parser.parse_args()


def parse_event_type_filter(raw_value: str) -> list[str] | None:
    values = [item.strip() for item in raw_value.split(",") if item.strip()]
    return values or None


def resolve_output_dir(raw_output_dir: str) -> Path:
    if raw_output_dir:
        return Path(raw_output_dir).expanduser().resolve()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return (
        PROJECT_ROOT
        / "acceptance_artifacts"
        / f"r16_live_fault_injection_{timestamp}"
    )


def build_live_injection_summary(
    *,
    output_dir: Path,
    report_mode: str = "auto",
    safe_event_types: list[str] | None = None,
    high_risk_event_types: list[str] | None = None,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    detector = ErrorEventDetector()

    safe_specs = list(iter_safe_recovery_specs())
    if safe_event_types is not None:
        allowed_safe = set(safe_event_types)
        safe_specs = [spec for spec in safe_specs if spec.event_type in allowed_safe]

    selected_high_risk = (
        list(high_risk_event_types)
        if high_risk_event_types is not None
        else list(DEFAULT_HIGH_RISK_EVENT_TYPES)
    )

    safe_rows = [
        run_safe_live_case(
            output_dir=output_dir,
            detector=detector,
            spec=spec,
            report_mode=report_mode,
        )
        for spec in safe_specs
    ]
    high_risk_rows = [
        run_high_risk_live_case(
            output_dir=output_dir,
            detector=detector,
            event_type=event_type,
            report_mode=report_mode,
        )
        for event_type in selected_high_risk
    ]

    failed_checks = collect_failed_checks(
        safe_rows=safe_rows,
        high_risk_rows=high_risk_rows,
    )

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "output_dir": str(output_dir),
        "report_mode": report_mode,
        "safe_rows": safe_rows,
        "high_risk_rows": high_risk_rows,
        "failed_checks": failed_checks,
        "conclusion": "PASS" if not failed_checks else "FAIL",
    }


def run_safe_live_case(
    *,
    output_dir: Path,
    detector: ErrorEventDetector,
    spec: SafeRecoverySpec,
    report_mode: str,
) -> dict[str, Any]:
    project_dir = output_dir / "projects" / "safe_live" / spec.event_type
    session_root = output_dir / "sessions"
    candidate = spec.candidates[0]
    config = config_for_candidate(spec, candidate)
    expected_value = candidate.new_value

    paths = write_live_project(
        project_dir=project_dir,
        event_type=spec.event_type,
        log_text=SAFE_INJECTION_LOGS[spec.event_type],
        config=config,
        mode="safe",
        field_path=candidate.field_path,
        expected_value=expected_value,
    )
    original_config = json.loads(paths["config_path"].read_text(encoding="utf-8"))
    event = detect_expected_event(
        detector=detector,
        log_path=paths["log_path"],
        event_type=spec.event_type,
    )

    project = make_project(
        project_dir=project_dir,
        event_type=spec.event_type,
        fix_ids=[spec.fix_id],
        dry_run=False,
        alerts_dir=project_dir / "alerts",
    )
    session = make_session(
        project=project,
        session_root=session_root,
        report_mode=report_mode,
    )
    session.start_from_log_file(str(paths["log_path"]))
    initial_rerun_text = session.rerun_project()
    initial_rerun_failed = session.latest_rerun_success is False
    session.add_monitor_event(
        event.to_evidence_text(),
        event_type=event.event_type,
        title=f"Injected event: {event.event_type}",
    )

    result = AutoRecoveryRunner(project=project, session=session).recover(event)
    notification = notify_and_record(
        project=project,
        event=event,
        result=result,
        session=session,
    )
    post_report, post_report_path, post_report_source = session.generate_report(
        report_intent="post_notification",
    )

    audit_path = project_dir / "state" / "live_recovery_audit.json"
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit = result.recovery_audit_record()
    audit_path.write_text(
        json.dumps(audit, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    updated_config = json.loads(paths["config_path"].read_text(encoding="utf-8"))
    current_value = get_nested(updated_config, candidate.field_path)
    backup_paths = [
        item.get("backup_path", "")
        for item in result.apply_edit_summary
        if item.get("backup_path")
    ]
    diff_paths = [
        item.get("diff_path", "")
        for item in result.apply_edit_summary
        if item.get("diff_path")
    ]
    report_paths = list(result.report_paths) + [str(post_report_path)]
    report_checks = validate_reports(
        report_paths=report_paths,
        event_type=spec.event_type,
        fix_id=spec.fix_id,
        expected_terms=["Stage 6C", "AutoRecoveryAgent", "recovered"],
    )
    notification_checks = validate_notification_payload(
        alerts_dir=Path(project.notification.alerts_dir),
        project_id=project.project_id,
        expected_status="recovered",
        expected_event_type=spec.event_type,
    )

    safe_recovery_ok = bool(
        initial_rerun_failed
        and result.apply_success
        and result.rerun_success
        and result.recovered
        and current_value == expected_value
        and updated_config != original_config
        and audit.get("execution_result") == "executed_recovered"
        and audit.get("rollback_result") == "not_needed_recovered"
        and audit.get("dry_run") is False
        and audit.get("allowed_to_execute") is True
    )
    backup_diff_ok = bool(
        backup_paths
        and diff_paths
        and all(Path(path).exists() for path in backup_paths + diff_paths)
    )

    return {
        "event_type": spec.event_type,
        "issue_type": spec.issue_type,
        "fix_id": spec.fix_id,
        "project_dir": str(project_dir),
        "log_path": str(paths["log_path"]),
        "config_path": str(paths["config_path"]),
        "session_dir": str(session.output_dir),
        "initial_rerun_failed": initial_rerun_failed,
        "initial_rerun_excerpt": initial_rerun_text[:600],
        "detected_event_type": event.event_type,
        "apply_success": result.apply_success,
        "rerun_success": result.rerun_success,
        "rollback_executed": result.rollback_executed,
        "rollback_success": result.rollback_success,
        "recovered": result.recovered,
        "decision_action": result.decision.action,
        "strategy_layer": audit.get("strategy_layer"),
        "dry_run": audit.get("dry_run"),
        "allowed_to_execute": audit.get("allowed_to_execute"),
        "execution_result": audit.get("execution_result"),
        "rollback_result": audit.get("rollback_result"),
        "planned_field_path": candidate.field_path,
        "expected_value": expected_value,
        "current_value": current_value,
        "backup_paths": backup_paths,
        "diff_paths": diff_paths,
        "audit_path": str(audit_path),
        "report_paths": report_paths,
        "report_sources": sorted(
            {
                _report_source_from_path(path)
                for path in report_paths
                if Path(path).exists()
            }
            | {post_report_source}
        ),
        "post_report_path": str(post_report_path),
        "post_report_source": post_report_source,
        "post_report_has_notification": "NotificationAgent" in post_report
        or "Stage 6D" in post_report
        or "通知" in post_report,
        "notification_status": notification["status"],
        "notification_results": notification["results"],
        "notification_payload_path": notification_checks["payload_path"],
        "notification_ok": notification_checks["ok"],
        "safe_recovery_ok": safe_recovery_ok,
        "backup_diff_ok": backup_diff_ok,
        "report_ok": report_checks["ok"],
        "report_missing_terms": report_checks["missing_terms"],
        "post_notification_report_ok": bool(
            Path(post_report_path).exists()
            and (
                "NotificationAgent" in post_report
                or "Stage 6D" in post_report
                or "通知" in post_report
            )
        ),
        "passed": all(
            [
                event.event_type == spec.event_type,
                safe_recovery_ok,
                backup_diff_ok,
                report_checks["ok"],
                notification_checks["ok"],
            ]
        ),
    }


def run_high_risk_live_case(
    *,
    output_dir: Path,
    detector: ErrorEventDetector,
    event_type: str,
    report_mode: str,
) -> dict[str, Any]:
    project_dir = output_dir / "projects" / "high_risk_live" / event_type
    session_root = output_dir / "sessions"
    log_text = HIGH_RISK_INJECTION_LOGS[event_type]
    paths = write_live_project(
        project_dir=project_dir,
        event_type=event_type,
        log_text=log_text,
        config={"service_name": "r16-high-risk-live", "safe_mode": True},
        mode="high_risk",
        field_path="safe_mode",
        expected_value=True,
    )
    original_config = json.loads(paths["config_path"].read_text(encoding="utf-8"))
    event = detect_expected_event(
        detector=detector,
        log_path=paths["log_path"],
        event_type=event_type,
    )

    project = make_project(
        project_dir=project_dir,
        event_type=event_type,
        fix_ids=sorted(SAFE_RECOVERY_FIX_IDS),
        dry_run=False,
        alerts_dir=project_dir / "alerts",
    )
    session = make_session(
        project=project,
        session_root=session_root,
        report_mode=report_mode,
    )
    session.start_from_log_file(str(paths["log_path"]))
    session.add_monitor_event(
        event.to_evidence_text(),
        event_type=event.event_type,
        title=f"Injected event: {event.event_type}",
    )

    result = AutoRecoveryRunner(project=project, session=session).recover(event)
    notification = notify_and_record(
        project=project,
        event=event,
        result=result,
        session=session,
    )
    post_report, post_report_path, post_report_source = session.generate_report(
        report_intent="post_notification",
    )

    audit = result.recovery_audit_record()
    audit_path = project_dir / "state" / "manual_notification_audit.json"
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(
        json.dumps(audit, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    current_config = json.loads(paths["config_path"].read_text(encoding="utf-8"))
    report_paths = list(result.report_paths) + [str(post_report_path)]
    report_checks = validate_reports(
        report_paths=report_paths,
        event_type=event_type,
        fix_id="",
        expected_terms=["Stage 6C", "manual_escalation"],
    )
    notification_checks = validate_notification_payload(
        alerts_dir=Path(project.notification.alerts_dir),
        project_id=project.project_id,
        expected_status="manual_escalation",
        expected_event_type=event_type,
    )

    manual_audit_ok = bool(
        result.decision.action == "manual_escalation"
        and not result.apply_success
        and not result.rerun_success
        and not result.recovered
        and audit.get("auto_recover_allowed") is False
        and audit.get("allowed_to_execute") is False
        and audit.get("would_execute") is False
        and audit.get("execution_result") == "not_run_r15_gate_blocked"
        and current_config == original_config
    )

    return {
        "event_type": event_type,
        "project_dir": str(project_dir),
        "log_path": str(paths["log_path"]),
        "config_path": str(paths["config_path"]),
        "session_dir": str(session.output_dir),
        "detected_event_type": event.event_type,
        "decision_action": result.decision.action,
        "strategy_layer": audit.get("strategy_layer"),
        "auto_recover_allowed": audit.get("auto_recover_allowed"),
        "allowed_to_execute": audit.get("allowed_to_execute"),
        "would_execute": audit.get("would_execute"),
        "apply_success": result.apply_success,
        "rerun_success": result.rerun_success,
        "recovered": result.recovered,
        "execution_result": audit.get("execution_result"),
        "rollback_result": audit.get("rollback_result"),
        "audit_path": str(audit_path),
        "report_paths": report_paths,
        "report_sources": sorted(
            {
                _report_source_from_path(path)
                for path in report_paths
                if Path(path).exists()
            }
            | {post_report_source}
        ),
        "post_report_path": str(post_report_path),
        "post_report_source": post_report_source,
        "post_report_has_notification": "NotificationAgent" in post_report
        or "Stage 6D" in post_report
        or "通知" in post_report,
        "notification_status": notification["status"],
        "notification_results": notification["results"],
        "notification_payload_path": notification_checks["payload_path"],
        "notification_ok": notification_checks["ok"],
        "manual_audit_ok": manual_audit_ok,
        "report_ok": report_checks["ok"],
        "report_missing_terms": report_checks["missing_terms"],
        "passed": all(
            [
                event.event_type == event_type,
                manual_audit_ok,
                report_checks["ok"],
                notification_checks["ok"],
            ]
        ),
    }


def detect_expected_event(
    *,
    detector: ErrorEventDetector,
    log_path: Path,
    event_type: str,
) -> ErrorEvent:
    events = detector.detect(
        log_path.read_text(encoding="utf-8"),
        source=f"live_injection:{event_type}:service.log",
    )
    return first_matching_event(events, event_type)


def make_project(
    *,
    project_dir: Path,
    event_type: str,
    fix_ids: list[str],
    dry_run: bool,
    alerts_dir: Path,
) -> ProjectConfig:
    return ProjectConfig(
        project_id=f"r16_live_{event_type}",
        name=f"R16 Live {event_type}",
        mode="local",
        owner="r16-validation",
        owner_contact="file",
        project_dir=str(project_dir),
        run_command=f"{sys.executable} app.py",
        log_files=[str(project_dir / "logs" / "service.log")],
        policy=PolicyConfig(
            auto_recover=True,
            allow_auto_apply=fix_ids,
            rollback_on_failure=True,
            auto_rerun_after_apply=True,
            auto_recovery_policy_enabled=True,
            auto_recovery_dry_run=dry_run,
            auto_recovery_fingerprint_cooldown_seconds=0,
            auto_recovery_event_type_cooldown_seconds=0,
            auto_recovery_project_cooldown_seconds=0,
        ),
        notification=NotificationConfig(
            enabled=True,
            channels=["file"],
            alerts_dir=str(alerts_dir),
            notify_on_recovered=True,
            notify_on_escalation=True,
            notify_on_report_only=True,
        ),
    )


def make_session(
    *,
    project: ProjectConfig,
    session_root: Path,
    report_mode: str,
) -> TroubleshootingSession:
    return TroubleshootingSession(
        session_id=project.project_id,
        output_root=str(session_root),
        agent_depth="balanced",
        report_mode=report_mode,
        project_dir=project.project_dir,
        run_command=project.run_command,
        rerun_timeout=30,
    )


def notify_and_record(
    *,
    project: ProjectConfig,
    event: ErrorEvent,
    result: AutoRecoveryResult,
    session: TroubleshootingSession,
) -> dict[str, Any]:
    manager = NotificationManager(project)
    message = manager.build_message_from_recovery(event, result)
    notification_results = manager.notify_recovery(event, result)
    session.record_notification_result(
        "\n".join(notification_results),
        status=message.status,
        channels=list(project.notification.channels),
    )
    return {
        "status": message.status,
        "message": message.message,
        "results": notification_results,
    }


def write_live_project(
    *,
    project_dir: Path,
    event_type: str,
    log_text: str,
    config: dict[str, Any],
    mode: str,
    field_path: str,
    expected_value: Any,
) -> dict[str, Path]:
    logs_dir = project_dir / "logs"
    state_dir = project_dir / "state"
    for path in (logs_dir, state_dir):
        path.mkdir(parents=True, exist_ok=True)

    config_path = project_dir / "config.json"
    log_path = logs_dir / "service.log"
    scenario_path = project_dir / "scenario.json"
    app_path = project_dir / "app.py"
    project_status_path = state_dir / "project_status.json"
    events_path = state_dir / "events.jsonl"

    config_path.write_text(
        json.dumps(config, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    log_path.write_text(log_text + "\n", encoding="utf-8")
    scenario_path.write_text(
        json.dumps(
            {
                "event_type": event_type,
                "mode": mode,
                "field_path": field_path,
                "expected_value": expected_value,
                "failure_log": log_text,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    app_path.write_text(LIVE_APP_SOURCE, encoding="utf-8")
    project_status_path.write_text(
        json.dumps(
            {
                "project_id": f"r16_live_{event_type}",
                "event_type": event_type,
                "mode": mode,
                "service_status": "degraded",
                "auto_recovery_dry_run": False,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    events_path.write_text(
        json.dumps(
            {
                "ts": "2026-06-30T11:00:00Z",
                "event_type": event_type,
                "mode": mode,
                "injection": True,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    return {
        "config_path": config_path,
        "log_path": log_path,
        "scenario_path": scenario_path,
        "app_path": app_path,
        "project_status_path": project_status_path,
        "events_path": events_path,
    }


LIVE_APP_SOURCE = r'''from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def get_nested(data: dict[str, Any], field_path: str) -> Any:
    current: Any = data
    for part in field_path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


root = Path(__file__).resolve().parent
scenario = json.loads((root / "scenario.json").read_text(encoding="utf-8"))
config = json.loads((root / "config.json").read_text(encoding="utf-8"))
event_type = scenario["event_type"]

if scenario["mode"] != "safe":
    print(f"HIGH_RISK_DIAGNOSE_ONLY event_type={event_type}")
    sys.exit(0)

field_path = scenario["field_path"]
expected_value = scenario["expected_value"]
current_value = get_nested(config, field_path)

if current_value == expected_value:
    print(f"RECOVERED event_type={event_type} field_path={field_path}")
    sys.exit(0)

print(scenario["failure_log"], file=sys.stderr)
print(
    f"UNRECOVERED event_type={event_type} field_path={field_path} "
    f"current_value={current_value!r} expected_value={expected_value!r}",
    file=sys.stderr,
)
sys.exit(1)
'''


def config_for_candidate(
    spec: SafeRecoverySpec,
    candidate: SafeRecoveryFieldCandidate,
) -> dict[str, Any]:
    config = {"service_name": f"r16-live-{spec.event_type}", "untouched": "keep"}
    if candidate.semantic_rule == SEMANTIC_PORT_AVAILABLE:
        config["metrics_host"] = "127.0.0.1"
    set_nested(config, candidate.field_path, old_value_for_candidate(candidate))
    return config


def old_value_for_candidate(candidate: SafeRecoveryFieldCandidate) -> Any:
    if candidate.semantic_rule == SEMANTIC_DISABLE_BOOL:
        return True
    if candidate.semantic_rule == SEMANTIC_LOWER_INT:
        return int(candidate.new_value) + 8
    if candidate.semantic_rule == SEMANTIC_PORT_AVAILABLE:
        return 9100
    if candidate.semantic_rule == SEMANTIC_SAFE_ENUM_DOWNGRADE:
        return "redis"
    if isinstance(candidate.new_value, bool):
        return not candidate.new_value
    if isinstance(candidate.new_value, int):
        return candidate.new_value + 8
    if isinstance(candidate.new_value, str):
        return f"old-{candidate.new_value}"
    return None


def set_nested(data: dict[str, Any], field_path: str, value: Any) -> None:
    current = data
    parts = field_path.split(".")
    for part in parts[:-1]:
        current = current.setdefault(part, {})
    current[parts[-1]] = value


def get_nested(data: dict[str, Any], field_path: str) -> Any:
    current: Any = data
    for part in field_path.split("."):
        current = current[part]
    return current


def validate_reports(
    *,
    report_paths: list[str],
    event_type: str,
    fix_id: str,
    expected_terms: list[str],
) -> dict[str, Any]:
    existing_paths = [Path(path) for path in report_paths if Path(path).exists()]
    combined = "\n\n".join(path.read_text(encoding="utf-8") for path in existing_paths)
    terms = [event_type, *expected_terms]
    if fix_id:
        terms.append(fix_id)
    missing_terms = [term for term in terms if term not in combined]
    return {
        "ok": bool(existing_paths) and not missing_terms,
        "missing_terms": missing_terms,
    }


def validate_notification_payload(
    *,
    alerts_dir: Path,
    project_id: str,
    expected_status: str,
    expected_event_type: str,
) -> dict[str, Any]:
    jsonl_path = alerts_dir / f"{project_id}_alerts.jsonl"
    if not jsonl_path.exists():
        return {"ok": False, "payload_path": "", "reason": "jsonl_missing"}

    lines = [line for line in jsonl_path.read_text(encoding="utf-8").splitlines() if line]
    if not lines:
        return {"ok": False, "payload_path": str(jsonl_path), "reason": "jsonl_empty"}

    payload = json.loads(lines[-1])
    archive_dir = alerts_dir / f"{project_id}_alerts"
    archive_json_paths = sorted(archive_dir.glob("*.json"))
    payload_path = str(archive_json_paths[-1]) if archive_json_paths else str(jsonl_path)
    ok = bool(
        payload.get("status") == expected_status
        and payload.get("event_type") == expected_event_type
        and payload.get("audit_required") is True
        and payload.get("recovery_audit_record")
        and Path(alerts_dir / f"{project_id}_latest_alert.md").exists()
    )
    return {
        "ok": ok,
        "payload_path": payload_path,
        "payload": payload,
    }


def collect_failed_checks(
    *,
    safe_rows: list[dict[str, Any]],
    high_risk_rows: list[dict[str, Any]],
) -> list[str]:
    failures: list[str] = []
    for row in safe_rows:
        for check_name in [
            "safe_recovery_ok",
            "backup_diff_ok",
            "report_ok",
            "notification_ok",
        ]:
            if not row.get(check_name):
                failures.append(f"{row['event_type']}: {check_name}")
        if row.get("detected_event_type") != row.get("event_type"):
            failures.append(f"{row['event_type']}: detector_mismatch")

    for row in high_risk_rows:
        for check_name in ["manual_audit_ok", "report_ok", "notification_ok"]:
            if not row.get(check_name):
                failures.append(f"{row['event_type']}: {check_name}")
        if row.get("detected_event_type") != row.get("event_type"):
            failures.append(f"{row['event_type']}: detector_mismatch")

    return failures


def _report_source_from_path(path: str) -> str:
    name = Path(path).name
    if "llm" in name:
        return "LLMReportAgent"
    if "rule" in name:
        return "Rule ReportAgent"
    return "unknown"


def write_summary_markdown(output_dir: Path, summary: dict[str, Any]) -> Path:
    path = output_dir / "R16_LIVE_FAULT_INJECTION_SUMMARY.md"
    lines = [
        "# R16 Live Fault Injection Summary",
        "",
        f"- generated_at: `{summary['generated_at']}`",
        f"- conclusion: `{summary['conclusion']}`",
        f"- report_mode: `{summary['report_mode']}`",
        f"- safe_rows: `{len(summary['safe_rows'])}`",
        f"- high_risk_rows: `{len(summary['high_risk_rows'])}`",
        "",
        "## Safe Auto Recovery",
        "",
        "| event_type | fix_id | recovered | apply | rerun | backup/diff | report | notification | execution_result |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in summary["safe_rows"]:
        lines.append(
            "| "
            f"{row['event_type']} | "
            f"{row['fix_id']} | "
            f"{row['recovered']} | "
            f"{row['apply_success']} | "
            f"{row['rerun_success']} | "
            f"{row['backup_diff_ok']} | "
            f"{row['report_ok']} | "
            f"{row['notification_ok']} | "
            f"{row['execution_result']} |"
        )

    lines.extend(
        [
            "",
            "## Non-Safe Notification And Audit",
            "",
            "| event_type | action | strategy_layer | auto_allowed | report | notification | execution_result |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in summary["high_risk_rows"]:
        lines.append(
            "| "
            f"{row['event_type']} | "
            f"{row['decision_action']} | "
            f"{row['strategy_layer']} | "
            f"{row['auto_recover_allowed']} | "
            f"{row['report_ok']} | "
            f"{row['notification_ok']} | "
            f"{row['execution_result']} |"
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
            "## Artifacts",
            "",
            f"- output_dir: `{summary['output_dir']}`",
            "- safe cases include live `app.py` rerun, config backup, diff, report, alert, and audit JSON.",
            "- non-safe cases include report, file notification, and recovery audit JSON without apply/rerun.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_summary_json(output_dir: Path, summary: dict[str, Any]) -> Path:
    path = output_dir / "live_fault_injection_summary.json"
    path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


if __name__ == "__main__":
    raise SystemExit(main())
