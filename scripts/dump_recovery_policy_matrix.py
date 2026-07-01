from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from detectors import ErrorEvent, ErrorEventDetector
from fixers.apply_executor import SafeApplyExecutor
from monitors.project_registry import PolicyConfig, ProjectConfig, ProjectRegistry
from policies import CompatibilityRemediationPolicy
from policies.auto_recovery_policy import resolve_policy_for_event
from recovery.auto_recovery_runtime_gate import build_runtime_auto_recovery_policy
from safe_recovery.registry import (
    SAFE_RECOVERY_FIX_IDS,
    domain_event_types,
    fix_id_for_event_type,
    fix_mapping_by_event_type,
    get_recovery_domain_spec_for_event_type,
    manual_event_types,
    manual_event_types_without_fix,
    safe_event_types,
    strategy_for_event_type,
)


UNKNOWN_PROBE_EVENT_TYPE = "unknown_future_domain"
LEGACY_ONLY_FIX_IDS = {
    "fix-python-1",
    "fix-model-path-1",
    "fix-config-path-1",
}


@dataclass(frozen=True)
class RecoveryPolicyMatrixRow:
    event_type: str
    issue_type: str
    sources: str
    registry_strategy: str
    registry_fix_id: str
    legacy_action: str
    legacy_fix_id: str
    runtime_strategy: str
    runtime_auto_recover_allowed: bool
    runtime_selected_fix_id: str
    runtime_downgrade_reason: str
    project_allowlist: str
    local_executor_support: bool
    remote_executor_support: bool
    drift_notes: str


def build_policy_matrix(project: ProjectConfig | None = None) -> list[RecoveryPolicyMatrixRow]:
    project = project or make_default_matrix_project()
    runtime_policy = build_runtime_auto_recovery_policy(project)
    detector_issue_by_event_type = _detector_issue_by_event_type()
    detector_severity_by_event_type = _detector_severity_by_event_type()
    local_fix_ids = SafeApplyExecutor.supported_safe_fix_ids()
    remote_fix_ids = _remote_supported_safe_fix_ids()

    rows: list[RecoveryPolicyMatrixRow] = []
    for event_type in _event_universe(runtime_policy.event_type_policies):
        issue_type = _issue_type_for_event(
            event_type=event_type,
            detector_issue_by_event_type=detector_issue_by_event_type,
        )
        event = ErrorEvent(
            event_type=event_type,
            issue_type=issue_type,
            severity=detector_severity_by_event_type.get(event_type, "medium"),
            summary=f"policy matrix probe for {event_type}",
            source="policy_matrix",
            raw_excerpt=f"{event_type} policy matrix evidence",
            signature=f"policy-matrix-{event_type}",
        )

        legacy_decision = CompatibilityRemediationPolicy().decide(
            event=event,
            project=project,
        )
        registry_fix_id = _registry_fix_id(event_type)
        candidate_fix_id = registry_fix_id or legacy_decision.fix_id
        runtime_decision = resolve_policy_for_event(
            event_type=event.event_type,
            fingerprint=event.fingerprint,
            confidence=1.0,
            candidate_fix_id=candidate_fix_id,
            policy=runtime_policy,
        )

        row = RecoveryPolicyMatrixRow(
            event_type=event_type,
            issue_type=issue_type,
            sources=",".join(
                _sources_for_event(
                    event_type=event_type,
                    runtime_event_policies=runtime_policy.event_type_policies,
                    detector_issue_by_event_type=detector_issue_by_event_type,
                )
            ),
            registry_strategy=_registry_strategy(event_type),
            registry_fix_id=registry_fix_id,
            legacy_action=legacy_decision.action,
            legacy_fix_id=legacy_decision.fix_id,
            runtime_strategy=_as_value(runtime_decision.strategy_layer),
            runtime_auto_recover_allowed=runtime_decision.auto_recover_allowed,
            runtime_selected_fix_id=runtime_decision.selected_fix_id,
            runtime_downgrade_reason=runtime_decision.downgrade_reason,
            project_allowlist=_project_allowlist_status(
                fix_id=candidate_fix_id,
                project=project,
            ),
            local_executor_support=bool(candidate_fix_id and candidate_fix_id in local_fix_ids),
            remote_executor_support=bool(candidate_fix_id and candidate_fix_id in remote_fix_ids),
            drift_notes=",".join(
                _drift_notes(
                    event_type=event_type,
                    registry_fix_id=registry_fix_id,
                    candidate_fix_id=candidate_fix_id,
                    legacy_action=legacy_decision.action,
                    runtime_strategy=_as_value(runtime_decision.strategy_layer),
                    runtime_auto_allowed=runtime_decision.auto_recover_allowed,
                    local_supported=bool(candidate_fix_id and candidate_fix_id in local_fix_ids),
                    remote_supported=bool(candidate_fix_id and candidate_fix_id in remote_fix_ids),
                )
            ),
        )
        rows.append(row)

    return sorted(rows, key=lambda row: row.event_type)


def make_default_matrix_project() -> ProjectConfig:
    return ProjectConfig(
        project_id="policy_matrix_synthetic",
        name="Policy Matrix Synthetic",
        mode="local",
        project_dir=".",
        run_command="python app.py",
        policy=PolicyConfig(
            auto_recover=True,
            allow_auto_apply=sorted(SAFE_RECOVERY_FIX_IDS | LEGACY_ONLY_FIX_IDS),
            rollback_on_failure=True,
            auto_recovery_policy_enabled=True,
            auto_recovery_dry_run=True,
        ),
    )


def load_project(config_path: str, project_id: str) -> ProjectConfig:
    return ProjectRegistry(config_path).get(project_id)


def rows_as_dicts(rows: Iterable[RecoveryPolicyMatrixRow]) -> list[dict[str, Any]]:
    return [asdict(row) for row in rows]


def render_markdown(rows: Iterable[RecoveryPolicyMatrixRow]) -> str:
    headers = [
        "event_type",
        "issue_type",
        "sources",
        "registry_strategy",
        "registry_fix_id",
        "legacy_action",
        "legacy_fix_id",
        "runtime_strategy",
        "runtime_auto",
        "runtime_fix_id",
        "runtime_reason",
        "project_allowlist",
        "local",
        "remote",
        "drift_notes",
    ]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for row in rows:
        values = [
            row.event_type,
            row.issue_type,
            row.sources,
            row.registry_strategy,
            row.registry_fix_id or "<none>",
            row.legacy_action,
            row.legacy_fix_id or "<none>",
            row.runtime_strategy,
            str(row.runtime_auto_recover_allowed),
            row.runtime_selected_fix_id or "<none>",
            row.runtime_downgrade_reason or "<none>",
            row.project_allowlist,
            str(row.local_executor_support),
            str(row.remote_executor_support),
            row.drift_notes or "<none>",
        ]
        lines.append("| " + " | ".join(_escape_markdown(value) for value in values) + " |")
    return "\n".join(lines)


def render_csv(rows: Iterable[RecoveryPolicyMatrixRow]) -> str:
    from io import StringIO

    output = StringIO()
    fieldnames = list(RecoveryPolicyMatrixRow.__dataclass_fields__)
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        writer.writerow(asdict(row))
    return output.getvalue()


def _event_universe(runtime_event_policies: dict[str, Any]) -> set[str]:
    return (
        set(safe_event_types())
        | set(domain_event_types())
        | {rule.event_type for rule in ErrorEventDetector.RULES}
        | set(fix_mapping_by_event_type())
        | set(manual_event_types_without_fix())
        | set(manual_event_types())
        | set(runtime_event_policies)
        | {"unknown", UNKNOWN_PROBE_EVENT_TYPE}
    )


def _remote_supported_safe_fix_ids() -> set[str]:
    try:
        from fixers.remote_apply_executor import RemoteSafeApplyExecutor

        return RemoteSafeApplyExecutor.supported_safe_fix_ids()
    except ModuleNotFoundError:
        # Some minimal local environments do not install optional agent tooling
        # imported by the remote executor module. The executor advertises the
        # registry set today, so keep the matrix usable without weakening the
        # governance tests that import the real class in full environments.
        return set(SAFE_RECOVERY_FIX_IDS)


def _detector_issue_by_event_type() -> dict[str, str]:
    return {rule.event_type: rule.issue_type for rule in ErrorEventDetector.RULES}


def _detector_severity_by_event_type() -> dict[str, str]:
    return {rule.event_type: rule.severity for rule in ErrorEventDetector.RULES}


def _issue_type_for_event(
    *,
    event_type: str,
    detector_issue_by_event_type: dict[str, str],
) -> str:
    spec = get_recovery_domain_spec_for_event_type(event_type)
    if spec is not None:
        return spec.issue_type
    return detector_issue_by_event_type.get(event_type, event_type)


def _registry_strategy(event_type: str) -> str:
    return strategy_for_event_type(event_type)


def _registry_fix_id(event_type: str) -> str:
    return fix_id_for_event_type(event_type)


def _sources_for_event(
    *,
    event_type: str,
    runtime_event_policies: dict[str, Any],
    detector_issue_by_event_type: dict[str, str],
) -> list[str]:
    sources: list[str] = []
    if event_type in safe_event_types():
        sources.append("registry_safe")
    elif event_type in domain_event_types():
        sources.append("registry_domain")
    if event_type in detector_issue_by_event_type:
        sources.append("detector")
    if event_type in fix_mapping_by_event_type():
        sources.append("legacy_mapping")
    if event_type in manual_event_types_without_fix():
        sources.append("legacy_escalate")
    if event_type in manual_event_types():
        sources.append("registry_manual")
    if event_type in runtime_event_policies:
        sources.append("runtime_policy")
    if event_type == UNKNOWN_PROBE_EVENT_TYPE:
        sources.append("unknown_probe")
    return sources


def _project_allowlist_status(*, fix_id: str, project: ProjectConfig) -> str:
    if not fix_id:
        return "no_fix_id"
    if fix_id in project.policy.allow_auto_apply:
        return "allowlisted"
    return "not_allowlisted"


def _drift_notes(
    *,
    event_type: str,
    registry_fix_id: str,
    candidate_fix_id: str,
    legacy_action: str,
    runtime_strategy: str,
    runtime_auto_allowed: bool,
    local_supported: bool,
    remote_supported: bool,
) -> list[str]:
    notes: list[str] = []
    if legacy_action == "auto_recover" and not runtime_auto_allowed:
        notes.append("legacy_auto_but_runtime_blocks")
    if registry_fix_id and candidate_fix_id and registry_fix_id != candidate_fix_id:
        notes.append("registry_fix_mismatch")
    if not registry_fix_id and candidate_fix_id:
        notes.append("legacy_only_fix_mapping")
    if registry_fix_id and (not local_supported or not remote_supported):
        notes.append("registry_fix_without_executor_support")
    if (
        event_type not in domain_event_types()
        and runtime_strategy == "manual_escalation"
    ):
        notes.append("manual_outside_registry")
    if event_type == UNKNOWN_PROBE_EVENT_TYPE:
        notes.append("unknown_event_probe")
    return notes


def _as_value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _escape_markdown(value: str) -> str:
    return str(value).replace("|", "\\|")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Dump a cross-layer recovery policy matrix without changing behavior."
    )
    parser.add_argument(
        "--project-id",
        default="",
        help="Use a project from configs/projects.yaml instead of the synthetic all-known-fixes project.",
    )
    parser.add_argument(
        "--config",
        default="configs/projects.yaml",
        help="Project registry config path used with --project-id.",
    )
    parser.add_argument(
        "--format",
        choices=["markdown", "json", "csv"],
        default="markdown",
        help="Output format.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    project = (
        load_project(args.config, args.project_id)
        if args.project_id
        else make_default_matrix_project()
    )
    rows = build_policy_matrix(project)

    if args.format == "json":
        print(json.dumps(rows_as_dicts(rows), ensure_ascii=False, indent=2))
    elif args.format == "csv":
        print(render_csv(rows), end="")
    else:
        print(render_markdown(rows))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
