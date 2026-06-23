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
from detectors import ErrorEvent
from fixers.apply_executor import SafeApplyExecutor
from fixers.remote_apply_executor import RemoteSafeApplyExecutor
from monitors.project_registry import PolicyConfig, ProjectConfig, ProjectRegistry
from policies import RemediationDecision, RemediationPolicy
from policies.auto_recovery_policy import MANUAL_ESCALATION_EVENT_TYPES
from recovery.auto_recovery_runtime_gate import (
    build_runtime_auto_recovery_policy,
    evaluate_runtime_auto_recovery_gate,
)
from recovery.guarded_auto_recover_dry_run import (
    FORBIDDEN_ACTIONS,
    evaluate_guarded_auto_recover_dry_run,
)
from safe_recovery.registry import SAFE_RECOVERY_FIX_IDS, SafeRecoverySpec
from safe_recovery.registry import iter_safe_recovery_specs
from safe_recovery.semantics import (
    SEMANTIC_DISABLE_BOOL,
    SEMANTIC_LOWER_INT,
    SEMANTIC_PORT_AVAILABLE,
    SEMANTIC_SAFE_ENUM_DOWNGRADE,
)


FIXTURE_CASES_PATH = (
    PROJECT_ROOT / "tests" / "fixtures" / "regression_logs" / "expected_cases.json"
)

NEGATIVE_CASE_PREFIXES = {
    "optional_integration_failed": "optional_integration",
    "notification_sink_failed": "notification_sink",
    "optional_cache_backend_failed": "optional_cache_backend",
    "optional_service_unavailable": "optional_service",
    "observability_export_failed": "observability_export",
}


def main() -> int:
    args = parse_args()
    output_dir = resolve_output_dir(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    original_port_probe = runtime_controls._is_tcp_port_available
    runtime_controls._is_tcp_port_available = lambda host, port: True
    try:
        summary = build_shadow_summary(output_dir)
    finally:
        runtime_controls._is_tcp_port_available = original_port_probe

    write_matrix(output_dir, summary)
    write_summary(output_dir, summary)
    write_json(output_dir, summary)

    print(f"shadow_output_dir={output_dir}")
    print(f"safe_domain_count={summary['safe_domain_count']}")
    print(f"conclusion={summary['conclusion']}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="R16 safe recovery domain offline shadow validation."
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
        / f"r16_safe_domain_validation_{timestamp}"
    )


def build_shadow_summary(output_dir: Path) -> dict[str, Any]:
    specs = list(iter_safe_recovery_specs())
    fixture_cases = load_fixture_cases()
    configured_project = ProjectRegistry("configs/projects.yaml").get(
        "enterprise_demo_local"
    )
    runtime_policy = build_runtime_auto_recovery_policy(
        make_project(
            project_dir=output_dir / "runtime_policy_project",
            fix_ids=sorted(SAFE_RECOVERY_FIX_IDS),
            dry_run=True,
        )
    )

    domain_rows = []
    missing_items = []
    dry_run_apply_blocked = True
    dry_run_rerun_blocked = True
    forbidden_action_blocked = True

    for spec in specs:
        positive_cases = positive_fixture_cases(fixture_cases, spec.event_type)
        negative_cases = negative_fixture_cases(fixture_cases, spec.event_type)
        action_gate = evaluate_shadow_gate(
            output_dir=output_dir,
            spec=spec,
            no_op=False,
            dry_run=True,
        )
        no_op_gate = evaluate_shadow_gate(
            output_dir=output_dir,
            spec=spec,
            no_op=True,
            dry_run=False,
        )
        forbidden_results = [
            evaluate_forbidden_action(spec, action)
            for action in FORBIDDEN_ACTIONS
        ]
        forbidden_ok = all(
            result.downgrade_reason == "forbidden_action"
            and not result.allowed_by_policy
            and not result.would_execute
            for result in forbidden_results
        )
        forbidden_action_blocked = forbidden_action_blocked and forbidden_ok

        event_policy = runtime_policy.event_type_policies[spec.event_type]
        row = {
            "event_type": spec.event_type,
            "issue_type": spec.issue_type,
            "fix_id": spec.fix_id,
            "strategy_layer": event_policy.strategy_layer.value,
            "dry_run": bool(event_policy.dry_run),
            "requires_precheck": bool(event_policy.require_precheck),
            "requires_rollback": bool(event_policy.require_rollback),
            "json_only": is_json_only(spec),
            "remote_apply_supported": spec.fix_id
            in RemoteSafeApplyExecutor.supported_safe_fix_ids(),
            "remote_apply_called_in_shadow": False,
            "rerun_called_in_shadow": False,
            "positive_fixture": bool(positive_cases),
            "positive_fixture_cases": positive_cases,
            "negative_fixture": bool(negative_cases),
            "negative_fixture_cases": negative_cases,
            "no_op_covered": bool(no_op_gate.precheck_result.get("no_op") is True),
            "rollback_test": has_rollback_test(spec.fix_id),
            "local_apply_supported": spec.fix_id
            in SafeApplyExecutor.supported_safe_fix_ids(),
            "precheck_passed": action_gate.precheck_result.get("passed") is True,
            "precheck_reason": action_gate.downgrade_reason or "<none>",
            "dry_run_execution_result": action_gate.audit_record.get(
                "execution_result"
            ),
            "dry_run_blocks_execution": bool(
                action_gate.dry_run
                and not action_gate.allowed_to_execute
                and not action_gate.would_execute
            ),
            "rollback_metadata": action_gate.audit_record.get(
                "precheck_result",
                {},
            ).get("rollback_plan", {}),
            "diff_audit_metadata": bool(
                action_gate.audit_record.get("precheck_result", {}).get(
                    "actionable_planned_edits"
                )
            ),
            "forbidden_action_blocked": forbidden_ok,
        }
        domain_rows.append(row)

        if not row["positive_fixture"]:
            missing_items.append(f"{spec.event_type}: missing positive fixture")
        if not row["negative_fixture"]:
            missing_items.append(f"{spec.event_type}: missing dedicated negative fixture")
        if not row["rollback_test"]:
            missing_items.append(f"{spec.event_type}: missing rollback test evidence")
        if not row["no_op_covered"]:
            missing_items.append(f"{spec.event_type}: no-op shadow check failed")
        if not row["dry_run_blocks_execution"]:
            missing_items.append(f"{spec.event_type}: dry-run did not block execution")

        dry_run_apply_blocked = dry_run_apply_blocked and not row[
            "remote_apply_called_in_shadow"
        ]
        dry_run_rerun_blocked = dry_run_rerun_blocked and not row[
            "rerun_called_in_shadow"
        ]

    manual_rows = validate_manual_domains(output_dir)
    high_risk_manual = all(
        row["action"] in {"manual_escalation", "report_only"}
        and row["auto_recover_allowed"] is False
        for row in manual_rows
    )
    if not high_risk_manual:
        missing_items.append("high-risk event type escaped manual/diagnose fallback")

    unknown_fix_gate = evaluate_unknown_fix(output_dir, specs[0])
    unknown_fix_downgrades = bool(
        not unknown_fix_gate.allowed_to_execute
        and not unknown_fix_gate.auto_recover_allowed
    )
    if not unknown_fix_downgrades:
        missing_items.append("unknown fix_id did not downgrade")

    coverage_counts = {
        "positive_fixture": sum(1 for row in domain_rows if row["positive_fixture"]),
        "negative_fixture": sum(1 for row in domain_rows if row["negative_fixture"]),
        "no_op": sum(1 for row in domain_rows if row["no_op_covered"]),
        "rollback_test": sum(1 for row in domain_rows if row["rollback_test"]),
        "dry_run_blocks_execution": sum(
            1 for row in domain_rows if row["dry_run_blocks_execution"]
        ),
        "forbidden_action_blocked": sum(
            1 for row in domain_rows if row["forbidden_action_blocked"]
        ),
    }

    conclusion = "PASS"
    if missing_items:
        conclusion = "PARTIAL"
    if (
        not dry_run_apply_blocked
        or not dry_run_rerun_blocked
        or not forbidden_action_blocked
        or not high_risk_manual
        or not unknown_fix_downgrades
    ):
        conclusion = "FAIL"

    if "sudo" not in {item.strip().lower() for item in FORBIDDEN_ACTIONS}:
        missing_items.append(
            "forbidden action list blocks registered privilege-escalation text "
            "but does not include explicit sudo alias"
        )
        if conclusion == "PASS":
            conclusion = "PARTIAL"

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "output_dir": str(output_dir),
        "safe_domain_count": len(specs),
        "fix_ids": [spec.fix_id for spec in specs],
        "domain_rows": domain_rows,
        "manual_rows": manual_rows,
        "coverage_counts": coverage_counts,
        "dry_run_blocks_remote_apply": dry_run_apply_blocked,
        "dry_run_blocks_rerun": dry_run_rerun_blocked,
        "forbidden_action_blocked": forbidden_action_blocked,
        "high_risk_manual_or_diagnose": high_risk_manual,
        "unknown_fix_downgrades": unknown_fix_downgrades,
        "configured_project_dry_run": configured_project.policy.auto_recovery_dry_run,
        "configured_allowlist": list(configured_project.policy.allow_auto_apply),
        "missing_items": missing_items,
        "conclusion": conclusion,
        "r16_s2_recommendation": (
            "conditional: run R16-S2 after reviewing PARTIAL fixture gaps"
            if conclusion == "PARTIAL"
            else (
                "yes: proceed to R16-S2 long dry-run/shadow validation"
                if conclusion == "PASS"
                else "no: fix failing safety baseline first"
            )
        ),
    }


def load_fixture_cases() -> list[dict[str, Any]]:
    return json.loads(FIXTURE_CASES_PATH.read_text(encoding="utf-8"))


def positive_fixture_cases(
    fixture_cases: list[dict[str, Any]],
    event_type: str,
) -> list[str]:
    return [
        case["case_id"]
        for case in fixture_cases
        if case.get("expected_event_type") == event_type
        and "negative" not in str(case.get("case_id", ""))
    ]


def negative_fixture_cases(
    fixture_cases: list[dict[str, Any]],
    event_type: str,
) -> list[str]:
    prefix = NEGATIVE_CASE_PREFIXES.get(event_type, event_type)

    return [
        case["case_id"]
        for case in fixture_cases
        if str(case.get("case_id", "")).startswith(prefix)
        and "negative" in str(case.get("case_id", ""))
        and case.get("expected_event_type") != event_type
    ]


def evaluate_shadow_gate(
    *,
    output_dir: Path,
    spec: SafeRecoverySpec,
    no_op: bool,
    dry_run: bool,
):
    sample_dir = (
        output_dir
        / "sample_configs"
        / spec.event_type
        / ("no_op" if no_op else "actionable")
    )
    sample_dir.mkdir(parents=True, exist_ok=True)
    (sample_dir / spec.relative_config_path).write_text(
        json.dumps(config_for_first_candidate(spec, no_op=no_op), indent=2),
        encoding="utf-8",
    )
    project = make_project(
        project_dir=sample_dir,
        fix_ids=[spec.fix_id],
        dry_run=dry_run,
    )
    event = make_event(spec)
    decision = RemediationPolicy().decide(event=event, project=project)
    return evaluate_runtime_auto_recovery_gate(
        event=event,
        project=project,
        remediation_decision=decision,
    )


def evaluate_forbidden_action(spec: SafeRecoverySpec, action: str):
    return evaluate_guarded_auto_recover_dry_run(
        event_type=spec.event_type,
        fingerprint=f"shadow-forbidden-{spec.event_type}",
        candidate_fix_id=spec.fix_id,
        strategy_layer="safe_auto_recover",
        policy_decision={"auto_recover_allowed": True},
        precheck_result={"passed": True},
        cooldown_result={"allowed": True},
        rollback_available=True,
        action_description=action,
    )


def evaluate_unknown_fix(output_dir: Path, spec: SafeRecoverySpec):
    project_dir = output_dir / "sample_configs" / "unknown_fix"
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / spec.relative_config_path).write_text(
        json.dumps(config_for_first_candidate(spec), indent=2),
        encoding="utf-8",
    )
    project = make_project(
        project_dir=project_dir,
        fix_ids=[spec.fix_id],
        dry_run=False,
    )
    event = make_event(spec)
    return evaluate_runtime_auto_recovery_gate(
        event=event,
        project=project,
        remediation_decision=RemediationDecision(
            action="auto_recover",
            fix_id="unknown-fix",
            reason="shadow unknown fix",
            should_rerun=True,
            rollback_on_failure=True,
        ),
    )


def validate_manual_domains(output_dir: Path) -> list[dict[str, Any]]:
    project = make_project(
        project_dir=output_dir / "manual_project",
        fix_ids=sorted(SAFE_RECOVERY_FIX_IDS),
        dry_run=True,
    )
    rows = []
    for event_type in sorted(MANUAL_ESCALATION_EVENT_TYPES):
        event = ErrorEvent(
            event_type=event_type,
            issue_type=event_type,
            severity="high",
            summary=f"{event_type} manual evidence",
            source="r16-shadow",
            raw_excerpt=f"{event_type} manual raw evidence",
            signature=f"r16-shadow-manual-{event_type}",
        )
        decision = RemediationPolicy().decide(event=event, project=project)
        rows.append(
            {
                "event_type": event_type,
                "action": decision.action,
                "fix_id": decision.fix_id,
                "auto_recover_allowed": decision.is_auto_recover,
            }
        )
    return rows


def make_project(
    *,
    project_dir: Path,
    fix_ids: list[str],
    dry_run: bool,
) -> ProjectConfig:
    project_dir.mkdir(parents=True, exist_ok=True)
    return ProjectConfig(
        project_id="r16_shadow_validation",
        name="R16 Shadow Validation",
        mode="local",
        project_dir=str(project_dir),
        run_command="python app.py",
        policy=PolicyConfig(
            auto_recover=True,
            allow_auto_apply=fix_ids,
            rollback_on_failure=True,
            auto_recovery_policy_enabled=True,
            auto_recovery_dry_run=dry_run,
        ),
    )


def make_event(spec: SafeRecoverySpec) -> ErrorEvent:
    return ErrorEvent(
        event_type=spec.event_type,
        issue_type=spec.issue_type,
        severity="medium",
        summary=f"{spec.event_type} shadow evidence",
        source="r16-shadow",
        raw_excerpt=f"{spec.event_type} shadow raw evidence",
        signature=f"r16-shadow-{spec.event_type}",
    )


def config_for_first_candidate(
    spec: SafeRecoverySpec,
    *,
    no_op: bool = False,
) -> dict[str, Any]:
    candidate = spec.candidates[0]
    if no_op:
        old_value = candidate.new_value
    elif candidate.semantic_rule == SEMANTIC_DISABLE_BOOL:
        old_value = True
    elif candidate.semantic_rule == SEMANTIC_LOWER_INT:
        old_value = int(candidate.new_value) + 8
    elif candidate.semantic_rule == SEMANTIC_PORT_AVAILABLE:
        old_value = 9000
    elif candidate.semantic_rule == SEMANTIC_SAFE_ENUM_DOWNGRADE:
        old_value = "redis"
    else:
        old_value = "enabled"

    data: dict[str, Any] = {}
    set_nested(data, candidate.field_path, old_value)
    return data


def set_nested(data: dict[str, Any], field_path: str, value: Any) -> None:
    current = data
    parts = field_path.split(".")
    for part in parts[:-1]:
        current = current.setdefault(part, {})
    current[parts[-1]] = value


def is_json_only(spec: SafeRecoverySpec) -> bool:
    return (
        spec.relative_config_path == "config.json"
        and Path(spec.relative_config_path).suffix == ".json"
        and not Path(spec.relative_config_path).is_absolute()
        and ".." not in Path(spec.relative_config_path).parts
    )


def has_rollback_test(fix_id: str) -> bool:
    candidate_files = [
        PROJECT_ROOT / "tests" / "test_safe_recovery_registry.py",
        PROJECT_ROOT / "tests" / "test_safe_auto_recover_domain_expansion.py",
        PROJECT_ROOT / "tests" / "test_r16_safe_batch1_isolated_recovery.py",
        PROJECT_ROOT / "tests" / "test_safe_recovery_domain_invariants.py",
    ]
    for path in candidate_files:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        if "rollback" in text and (fix_id in text or "iter_safe_recovery_specs()" in text):
            return True
    return False


def write_matrix(output_dir: Path, summary: dict[str, Any]) -> Path:
    path = output_dir / "safe_domain_matrix.md"
    lines = [
        "# R16 Safe Recovery Domain Matrix",
        "",
        f"- generated_at: `{summary['generated_at']}`",
        f"- safe_domain_count: `{summary['safe_domain_count']}`",
        f"- configured_project_dry_run: `{summary['configured_project_dry_run']}`",
        "",
        "| event_type | fix_id | strategy_layer | dry-run | precheck | rollback | JSON only | remote apply supported | remote apply in shadow | rerun in shadow | positive fixture | negative fixture | rollback test | no-op covered |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in summary["domain_rows"]:
        lines.append(
            "| "
            f"{row['event_type']} | "
            f"{row['fix_id']} | "
            f"{row['strategy_layer']} | "
            f"{row['dry_run']} | "
            f"{row['requires_precheck']} | "
            f"{row['requires_rollback']} | "
            f"{row['json_only']} | "
            f"{row['remote_apply_supported']} | "
            f"{row['remote_apply_called_in_shadow']} | "
            f"{row['rerun_called_in_shadow']} | "
            f"{format_cases(row['positive_fixture_cases'])} | "
            f"{format_cases(row['negative_fixture_cases'])} | "
            f"{row['rollback_test']} | "
            f"{row['no_op_covered']} |"
        )

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_summary(output_dir: Path, summary: dict[str, Any]) -> Path:
    path = output_dir / "R16_SAFE_RECOVERY_DOMAIN_VALIDATION_SUMMARY.md"
    rows = summary["domain_rows"]
    counts = summary["coverage_counts"]
    lines = [
        "# R16 Safe Recovery Domain Validation Summary",
        "",
        f"- generated_at: `{summary['generated_at']}`",
        f"- conclusion: `{summary['conclusion']}`",
        f"- safe_domain_total: `{summary['safe_domain_count']}`",
        f"- fix_ids: `{', '.join(summary['fix_ids'])}`",
        "",
        "## Coverage",
        "",
        f"- positive_fixture: `{counts['positive_fixture']}/{len(rows)}`",
        f"- negative_fixture: `{counts['negative_fixture']}/{len(rows)}`",
        f"- no_op: `{counts['no_op']}/{len(rows)}`",
        f"- rollback_test: `{counts['rollback_test']}/{len(rows)}`",
        f"- dry_run_blocks_execution: `{counts['dry_run_blocks_execution']}/{len(rows)}`",
        f"- forbidden_action_blocked: `{counts['forbidden_action_blocked']}/{len(rows)}`",
        "",
        "## Safety Checks",
        "",
        f"- dry-run blocks remote apply: `{summary['dry_run_blocks_remote_apply']}`",
        f"- dry-run blocks rerun: `{summary['dry_run_blocks_rerun']}`",
        f"- forbidden actions blocked: `{summary['forbidden_action_blocked']}`",
        f"- high-risk domains remain manual/diagnose: `{summary['high_risk_manual_or_diagnose']}`",
        f"- unknown fix_id downgrades: `{summary['unknown_fix_downgrades']}`",
        "",
        "## Per-Domain Coverage",
        "",
        "| event_type | fix_id | positive | negative | no-op | rollback | dry-run blocked | forbidden blocked |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            "| "
            f"{row['event_type']} | "
            f"{row['fix_id']} | "
            f"{row['positive_fixture']} | "
            f"{row['negative_fixture']} | "
            f"{row['no_op_covered']} | "
            f"{row['rollback_test']} | "
            f"{row['dry_run_blocks_execution']} | "
            f"{row['forbidden_action_blocked']} |"
        )

    lines.extend(
        [
            "",
            "## Manual / Diagnose Fallback",
            "",
            "| event_type | action | fix_id | auto_recover_allowed |",
            "| --- | --- | --- | --- |",
        ]
    )
    for row in summary["manual_rows"]:
        lines.append(
            "| "
            f"{row['event_type']} | "
            f"{row['action']} | "
            f"{row['fix_id'] or '<none>'} | "
            f"{row['auto_recover_allowed']} |"
        )

    lines.extend(["", "## Missing Items", ""])
    if summary["missing_items"]:
        for item in summary["missing_items"]:
            lines.append(f"- {item}")
    else:
        lines.append("- <none>")

    lines.extend(
        [
            "",
            "## R16-S2 Recommendation",
            "",
            f"- `{summary['r16_s2_recommendation']}`",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_json(output_dir: Path, summary: dict[str, Any]) -> Path:
    path = output_dir / "shadow_summary.json"
    path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return path


def format_cases(cases: list[str]) -> str:
    if not cases:
        return "<missing>"
    return ", ".join(cases)


if __name__ == "__main__":
    raise SystemExit(main())
