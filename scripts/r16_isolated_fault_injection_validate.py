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
from monitors.project_registry import PolicyConfig, ProjectConfig
from policies import CompatibilityRemediationPolicy
from recovery.auto_recovery_runtime_gate import evaluate_runtime_auto_recovery_gate
from safe_recovery.registry import (
    SAFE_RECOVERY_FIX_IDS,
    SafeRecoveryFieldCandidate,
    SafeRecoverySpec,
    iter_safe_recovery_specs,
)
from safe_recovery.semantics import (
    SEMANTIC_DISABLE_BOOL,
    SEMANTIC_LOWER_INT,
    SEMANTIC_PORT_AVAILABLE,
    SEMANTIC_SAFE_ENUM_DOWNGRADE,
)


SAFE_INJECTION_LOGS = {
    "network_port": (
        "2026-06-30T10:00:01Z ERROR metrics exporter bind failed: "
        "OSError [Errno 98] Address already in use on port 9100"
    ),
    "gpu_oom": (
        "2026-06-30T10:00:02Z ERROR trainer failed: "
        "torch.cuda.OutOfMemoryError: CUDA out of memory while allocating 1024 MiB"
    ),
    "cache_write_failed": (
        "2026-06-30T10:00:03Z WARNING feature cache write failed: "
        "fallback: continue with in-memory feature cache"
    ),
    "optional_dependency_missing": (
        "2026-06-30T10:00:04Z WARNING optional dependency missing: "
        "internal risk sdk unavailable; fallback local rule engine"
    ),
    "optional_integration_failed": (
        "2026-06-30T10:00:05Z WARNING optional integration failed: "
        "enrichment client timeout; fallback local enrichment degraded mode "
        "optional integration"
    ),
    "optional_cache_backend_failed": (
        "2026-06-30T10:00:06Z ERROR optional cache backend unavailable: "
        "redis cache backend timeout; cache backend fallback to memory"
    ),
    "optional_service_unavailable": (
        "2026-06-30T10:00:07Z ERROR optional enrichment service unavailable: "
        "fallback local enrichment in degraded mode"
    ),
    "notification_sink_failed": (
        "2026-06-30T10:00:08Z ERROR notification webhook failed http 500; "
        "fallback file notification"
    ),
    "observability_export_failed": (
        "2026-06-30T10:00:09Z ERROR observability exporter failed timeout; "
        "fallback file metrics observability"
    ),
    "queue_backpressure": (
        "2026-06-30T10:00:10Z ERROR queue backpressure: "
        "prefetch too high and consumer lag too high"
    ),
    "worker_overload": (
        "2026-06-30T10:00:11Z ERROR worker pool exhausted: concurrency too high"
    ),
}

HIGH_RISK_INJECTION_LOGS = {
    "disk_full": (
        "2026-06-30T10:10:01Z ERROR write failed: "
        "OSError [Errno 28] No space left on device: /var/lib/demo/data.db"
    ),
    "python_env": (
        "2026-06-30T10:10:02Z ERROR startup failed: "
        "ModuleNotFoundError: No module named 'yaml'"
    ),
    "process_crash": (
        "2026-06-30T10:10:03Z systemd[1]: demo.service: "
        "Main process exited status=11; core dumped"
    ),
    "container_k8s": (
        "2026-06-30T10:10:04Z Warning BackOff pod/demo CrashLoopBackOff"
    ),
    "auth_cert": (
        "2026-06-30T10:10:05Z ERROR HTTP 403 token expired while calling api"
    ),
    "slurm": (
        "2026-06-30T10:10:06Z slurmstepd: error: "
        "Batch job failed because node gpu001 down"
    ),
    "host_resource": (
        "2026-06-30T10:10:07Z ERROR cannot allocate memory; "
        "too many open files"
    ),
    "network_connectivity": (
        "2026-06-30T10:10:08Z ERROR DNS resolution failed for upstream"
    ),
    "dependency_service": (
        "2026-06-30T10:10:09Z ERROR postgresql connection failed; "
        "database connection pool exhausted"
    ),
    "config_error": (
        "2026-06-30T10:10:10Z ERROR invalid yaml in config file: "
        "missing required config key api_key"
    ),
    "permission_denied": (
        "2026-06-30T10:10:11Z ERROR Permission denied while opening "
        "/etc/shadow EACCES"
    ),
    "process_kill": (
        "2026-06-30T10:10:12Z ERROR worker exited with status 137 "
        "signal=SIGKILL"
    ),
}


def main() -> int:
    args = parse_args()
    output_dir = resolve_output_dir(args.output_dir)

    original_port_probe = runtime_controls._is_tcp_port_available
    runtime_controls._is_tcp_port_available = lambda host, port: True
    try:
        summary = build_isolated_injection_summary(output_dir)
    finally:
        runtime_controls._is_tcp_port_available = original_port_probe

    write_summary_markdown(output_dir, summary)
    write_summary_json(output_dir, summary)

    print(f"injection_output_dir={output_dir}")
    print(f"safe_rows={len(summary['safe_rows'])}")
    print(f"high_risk_rows={len(summary['high_risk_rows'])}")
    print(f"conclusion={summary['conclusion']}")
    return 0 if summary["conclusion"] == "PASS" else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="R16 isolated real-shape fault injection validation."
    )
    parser.add_argument(
        "--output-dir",
        default="",
        help="Optional acceptance output directory.",
    )
    return parser.parse_args()


def resolve_output_dir(raw_output_dir: str) -> Path:
    if raw_output_dir:
        return Path(raw_output_dir).expanduser().resolve()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return (
        PROJECT_ROOT
        / "acceptance_artifacts"
        / f"r16_isolated_fault_injection_{timestamp}"
    )


def build_isolated_injection_summary(output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    detector = ErrorEventDetector()

    safe_rows = [
        validate_safe_domain_injection(
            output_dir=output_dir,
            detector=detector,
            spec=spec,
        )
        for spec in iter_safe_recovery_specs()
    ]
    high_risk_rows = [
        validate_high_risk_injection(
            output_dir=output_dir,
            detector=detector,
            event_type=event_type,
            log_text=log_text,
        )
        for event_type, log_text in HIGH_RISK_INJECTION_LOGS.items()
    ]
    unknown_row = validate_unknown_event(output_dir)

    failed_checks = collect_failed_checks(
        safe_rows=safe_rows,
        high_risk_rows=high_risk_rows,
        unknown_row=unknown_row,
    )
    conclusion = "PASS" if not failed_checks else "FAIL"

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "output_dir": str(output_dir),
        "auto_recovery_dry_run": True,
        "safe_rows": safe_rows,
        "high_risk_rows": high_risk_rows,
        "unknown_row": unknown_row,
        "safe_domain_count": len(safe_rows),
        "high_risk_count": len(high_risk_rows),
        "failed_checks": failed_checks,
        "conclusion": conclusion,
    }


def validate_safe_domain_injection(
    *,
    output_dir: Path,
    detector: ErrorEventDetector,
    spec: SafeRecoverySpec,
) -> dict[str, Any]:
    project_dir = output_dir / "projects" / "safe" / spec.event_type
    config = config_for_first_candidate(spec)
    paths = write_simulated_project(
        project_dir=project_dir,
        event_type=spec.event_type,
        log_text=SAFE_INJECTION_LOGS[spec.event_type],
        config=config,
        risk="safe",
    )
    original_config = json.loads(paths["config_path"].read_text(encoding="utf-8"))

    events = detector.detect(
        paths["log_path"].read_text(encoding="utf-8"),
        source=f"isolated:{spec.event_type}:service.log",
    )
    event = first_matching_event(events, spec.event_type)
    project = make_project(
        project_dir=project_dir,
        fix_ids=[spec.fix_id],
        dry_run=True,
    )

    decision = CompatibilityRemediationPolicy().decide(
        event=event,
        project=project,
    )
    gate = evaluate_runtime_auto_recovery_gate(
        event=event,
        project=project,
        remediation_decision=decision,
    )
    audit_path = project_dir / "state" / "dry_run_audit.json"
    audit_path.write_text(
        json.dumps(gate.audit_record, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    current_config = json.loads(paths["config_path"].read_text(encoding="utf-8"))
    backup_or_diff_files = find_backup_or_diff_files(project_dir)
    precheck = dict(gate.audit_record.get("precheck_result") or {})
    rollback_plan = dict(precheck.get("rollback_plan") or {})
    actionable_edits = list(precheck.get("actionable_planned_edits") or [])

    detector_ok = bool(event.event_type == spec.event_type)
    fix_id_ok = bool(decision.fix_id == spec.fix_id and gate.selected_fix_id == spec.fix_id)
    dry_run_audit_ok = bool(
        gate.auto_recover_allowed
        and gate.dry_run
        and not gate.allowed_to_execute
        and not gate.would_execute
        and gate.audit_record.get("execution_result") == "not_run_r15_dry_run"
    )
    rollback_plan_ok = bool(
        rollback_plan.get("available") is True
        and rollback_plan.get("backup_created_before_write") is True
    )
    diff_plan_ok = bool(
        actionable_edits
        and actionable_edits[0].get("old_value_available") is True
        and "new_value" in actionable_edits[0]
    )
    no_write_ok = bool(
        current_config == original_config
        and not backup_or_diff_files
        and not (project_dir / "state" / "applied_fixes.json").exists()
    )

    return {
        "event_type": spec.event_type,
        "issue_type": spec.issue_type,
        "fix_id": spec.fix_id,
        "project_dir": str(project_dir),
        "log_path": str(paths["log_path"]),
        "config_path": str(paths["config_path"]),
        "state_files": [str(path) for path in paths["state_files"]],
        "audit_path": str(audit_path),
        "detected_event_types": [item.event_type for item in events],
        "detected_fix_id": decision.fix_id,
        "strategy_layer": gate.strategy_layer,
        "decision_action": decision.action,
        "dry_run": gate.dry_run,
        "auto_recover_allowed": gate.auto_recover_allowed,
        "allowed_to_execute": gate.allowed_to_execute,
        "would_execute": gate.would_execute,
        "execution_result": gate.audit_record.get("execution_result"),
        "rollback_result": gate.audit_record.get("rollback_result"),
        "precheck_passed": gate.precheck_result.get("passed") is True,
        "actionable_edit_count": gate.precheck_result.get("actionable_edit_count"),
        "planned_field_path": actionable_edits[0].get("field_path")
        if actionable_edits
        else "",
        "rollback_plan": rollback_plan,
        "backup_plan_present": rollback_plan_ok,
        "diff_plan_present": diff_plan_ok,
        "backup_or_diff_files_created": backup_or_diff_files,
        "config_unchanged": current_config == original_config,
        "detector_ok": detector_ok,
        "fix_id_ok": fix_id_ok,
        "dry_run_audit_ok": dry_run_audit_ok,
        "rollback_plan_ok": rollback_plan_ok,
        "diff_plan_ok": diff_plan_ok,
        "no_write_ok": no_write_ok,
        "passed": all(
            [
                detector_ok,
                fix_id_ok,
                dry_run_audit_ok,
                rollback_plan_ok,
                diff_plan_ok,
                no_write_ok,
            ]
        ),
    }


def validate_high_risk_injection(
    *,
    output_dir: Path,
    detector: ErrorEventDetector,
    event_type: str,
    log_text: str,
) -> dict[str, Any]:
    project_dir = output_dir / "projects" / "high_risk" / event_type
    paths = write_simulated_project(
        project_dir=project_dir,
        event_type=event_type,
        log_text=log_text,
        config={"service_name": "r16-high-risk", "safe_mode": True},
        risk="high",
    )
    events = detector.detect(
        paths["log_path"].read_text(encoding="utf-8"),
        source=f"isolated:{event_type}:service.log",
    )
    event = first_matching_event(events, event_type)
    project = make_project(
        project_dir=project_dir,
        fix_ids=sorted(SAFE_RECOVERY_FIX_IDS),
        dry_run=True,
    )
    decision = CompatibilityRemediationPolicy().decide(
        event=event,
        project=project,
    )
    gate = evaluate_runtime_auto_recovery_gate(
        event=event,
        project=project,
        remediation_decision=decision,
    )
    audit_path = project_dir / "state" / "manual_or_diagnose_audit.json"
    audit_path.write_text(
        json.dumps(gate.audit_record, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    detector_ok = bool(event.event_type == event_type)
    fallback_ok = bool(
        decision.action in {"manual_escalation", "report_only"}
        and gate.strategy_layer in {"manual_escalation", "diagnose_only", "disabled"}
        and not gate.auto_recover_allowed
        and not gate.allowed_to_execute
        and not gate.would_execute
    )

    return {
        "event_type": event_type,
        "project_dir": str(project_dir),
        "log_path": str(paths["log_path"]),
        "state_files": [str(path) for path in paths["state_files"]],
        "audit_path": str(audit_path),
        "detected_event_types": [item.event_type for item in events],
        "decision_action": decision.action,
        "fix_id": decision.fix_id,
        "strategy_layer": gate.strategy_layer,
        "auto_recover_allowed": gate.auto_recover_allowed,
        "allowed_to_execute": gate.allowed_to_execute,
        "would_execute": gate.would_execute,
        "execution_result": gate.audit_record.get("execution_result"),
        "downgrade_reason": gate.downgrade_reason,
        "detector_ok": detector_ok,
        "fallback_ok": fallback_ok,
        "passed": detector_ok and fallback_ok,
    }


def validate_unknown_event(output_dir: Path) -> dict[str, Any]:
    project_dir = output_dir / "projects" / "diagnose_only" / "unknown_fault"
    paths = write_simulated_project(
        project_dir=project_dir,
        event_type="unknown_fault",
        log_text=(
            "2026-06-30T10:20:01Z WARNING subsystem emitted "
            "unclassified degraded signal"
        ),
        config={"service_name": "r16-unknown", "safe_mode": True},
        risk="unknown",
    )
    event = ErrorEvent(
        event_type="unknown_fault",
        issue_type="unknown_fault",
        severity="medium",
        summary="Unknown isolated injected fault",
        source=f"isolated:{paths['log_path'].name}",
        raw_excerpt=paths["log_path"].read_text(encoding="utf-8"),
        signature="r16-isolated-unknown-fault",
    )
    project = make_project(
        project_dir=project_dir,
        fix_ids=sorted(SAFE_RECOVERY_FIX_IDS),
        dry_run=True,
    )
    decision = CompatibilityRemediationPolicy().decide(
        event=event,
        project=project,
    )
    gate = evaluate_runtime_auto_recovery_gate(
        event=event,
        project=project,
        remediation_decision=decision,
    )
    audit_path = project_dir / "state" / "diagnose_only_audit.json"
    audit_path.write_text(
        json.dumps(gate.audit_record, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    passed = bool(
        decision.action == "report_only"
        and gate.strategy_layer == "diagnose_only"
        and not gate.auto_recover_allowed
        and not gate.allowed_to_execute
    )
    return {
        "event_type": event.event_type,
        "project_dir": str(project_dir),
        "log_path": str(paths["log_path"]),
        "state_files": [str(path) for path in paths["state_files"]],
        "audit_path": str(audit_path),
        "decision_action": decision.action,
        "strategy_layer": gate.strategy_layer,
        "auto_recover_allowed": gate.auto_recover_allowed,
        "allowed_to_execute": gate.allowed_to_execute,
        "would_execute": gate.would_execute,
        "execution_result": gate.audit_record.get("execution_result"),
        "passed": passed,
    }


def write_simulated_project(
    *,
    project_dir: Path,
    event_type: str,
    log_text: str,
    config: dict[str, Any],
    risk: str,
) -> dict[str, Any]:
    logs_dir = project_dir / "logs"
    state_dir = project_dir / "state"
    etc_dir = project_dir / "etc" / "demo-service"
    var_dir = project_dir / "var" / "lib" / "demo-service"
    for path in (logs_dir, state_dir, etc_dir, var_dir):
        path.mkdir(parents=True, exist_ok=True)

    config_path = project_dir / "config.json"
    log_path = logs_dir / "service.log"
    project_status_path = state_dir / "project_status.json"
    events_path = state_dir / "events.jsonl"
    unit_state_path = state_dir / "systemd_unit_state.json"
    etc_config_path = etc_dir / "service.conf"
    runtime_state_path = var_dir / "runtime_state.json"

    config_path.write_text(
        json.dumps(config, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    log_path.write_text(log_text + "\n", encoding="utf-8")
    project_status_path.write_text(
        json.dumps(
            {
                "project_id": f"r16_isolated_{event_type}",
                "event_type": event_type,
                "risk": risk,
                "auto_recovery_dry_run": True,
                "service_status": "degraded",
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    events_path.write_text(
        json.dumps(
            {
                "ts": "2026-06-30T10:00:00Z",
                "event_type": event_type,
                "source": str(log_path),
                "injection": True,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    unit_state_path.write_text(
        json.dumps(
            {
                "unit": "demo.service",
                "active_state": "active",
                "sub_state": "running",
                "restart_count": 0,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    etc_config_path.write_text(
        "SERVICE_ENV=isolated\nAUTO_RECOVERY_DRY_RUN=true\n",
        encoding="utf-8",
    )
    runtime_state_path.write_text(
        json.dumps({"pid": 4242, "health": "degraded"}, indent=2),
        encoding="utf-8",
    )

    return {
        "config_path": config_path,
        "log_path": log_path,
        "state_files": [
            project_status_path,
            events_path,
            unit_state_path,
            etc_config_path,
            runtime_state_path,
        ],
    }


def make_project(
    *,
    project_dir: Path,
    fix_ids: list[str],
    dry_run: bool,
) -> ProjectConfig:
    return ProjectConfig(
        project_id=f"r16_isolated_{project_dir.name}",
        name="R16 Isolated Fault Injection",
        mode="local",
        project_dir=str(project_dir),
        run_command="python app.py",
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
    )


def first_matching_event(events: list[ErrorEvent], event_type: str) -> ErrorEvent:
    for event in events:
        if event.event_type == event_type:
            return event

    if events:
        return events[0]

    return ErrorEvent(
        event_type="detector_miss",
        issue_type="detector_miss",
        severity="high",
        summary=f"Detector missed expected event {event_type}",
        source="isolated-detector",
        raw_excerpt="",
        signature=f"detector-miss-{event_type}",
    )


def config_for_first_candidate(spec: SafeRecoverySpec) -> dict[str, Any]:
    candidate = spec.candidates[0]
    config = {"service_name": "r16-safe-injection", "untouched": "keep"}
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
        next_value = current.get(part)
        if not isinstance(next_value, dict):
            next_value = {}
            current[part] = next_value
        current = next_value
    current[parts[-1]] = value


def find_backup_or_diff_files(project_dir: Path) -> list[str]:
    suffixes = {".bak", ".diff"}
    result = []
    for path in project_dir.rglob("*"):
        if path.is_file() and path.suffix in suffixes:
            result.append(str(path))
    return sorted(result)


def collect_failed_checks(
    *,
    safe_rows: list[dict[str, Any]],
    high_risk_rows: list[dict[str, Any]],
    unknown_row: dict[str, Any],
) -> list[str]:
    failures = []
    for row in safe_rows:
        if row["passed"]:
            continue
        for check_name in [
            "detector_ok",
            "fix_id_ok",
            "dry_run_audit_ok",
            "rollback_plan_ok",
            "diff_plan_ok",
            "no_write_ok",
        ]:
            if not row.get(check_name):
                failures.append(f"{row['event_type']}: {check_name}")

    for row in high_risk_rows:
        if row["passed"]:
            continue
        for check_name in ["detector_ok", "fallback_ok"]:
            if not row.get(check_name):
                failures.append(f"{row['event_type']}: {check_name}")

    if not unknown_row.get("passed"):
        failures.append("unknown_fault: diagnose_only fallback")

    return failures


def write_summary_markdown(output_dir: Path, summary: dict[str, Any]) -> Path:
    path = output_dir / "R16_ISOLATED_FAULT_INJECTION_SUMMARY.md"
    lines = [
        "# R16 Isolated Fault Injection Summary",
        "",
        f"- generated_at: `{summary['generated_at']}`",
        f"- conclusion: `{summary['conclusion']}`",
        f"- auto_recovery_dry_run: `{summary['auto_recovery_dry_run']}`",
        f"- safe_domain_count: `{summary['safe_domain_count']}`",
        f"- high_risk_count: `{summary['high_risk_count']}`",
        "",
        "## Safe Domains",
        "",
        "| event_type | fix_id | detected | dry-run audit | backup plan | diff plan | rollback plan | no write |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in summary["safe_rows"]:
        lines.append(
            "| "
            f"{row['event_type']} | "
            f"{row['fix_id']} | "
            f"{row['detector_ok']} | "
            f"{row['dry_run_audit_ok']} | "
            f"{row['backup_plan_present']} | "
            f"{row['diff_plan_present']} | "
            f"{row['rollback_plan_ok']} | "
            f"{row['no_write_ok']} |"
        )

    lines.extend(
        [
            "",
            "## High-Risk Domains",
            "",
            "| event_type | detected | decision_action | strategy_layer | auto_recover_allowed | allowed_to_execute |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in summary["high_risk_rows"]:
        lines.append(
            "| "
            f"{row['event_type']} | "
            f"{row['detector_ok']} | "
            f"{row['decision_action']} | "
            f"{row['strategy_layer']} | "
            f"{row['auto_recover_allowed']} | "
            f"{row['allowed_to_execute']} |"
        )

    unknown = summary["unknown_row"]
    lines.extend(
        [
            "",
            "## Diagnose-Only Fallback",
            "",
            "| event_type | decision_action | strategy_layer | auto_recover_allowed | allowed_to_execute |",
            "| --- | --- | --- | --- | --- |",
            "| "
            f"{unknown['event_type']} | "
            f"{unknown['decision_action']} | "
            f"{unknown['strategy_layer']} | "
            f"{unknown['auto_recover_allowed']} | "
            f"{unknown['allowed_to_execute']} |",
            "",
            "## Failed Checks",
            "",
        ]
    )
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
            "- each scenario includes `config.json`, `logs/service.log`, "
            "`state/project_status.json`, `state/events.jsonl`, "
            "and a dry-run audit JSON file.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_summary_json(output_dir: Path, summary: dict[str, Any]) -> Path:
    path = output_dir / "isolated_fault_injection_summary.json"
    path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


if __name__ == "__main__":
    raise SystemExit(main())
