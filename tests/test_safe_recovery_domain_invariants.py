from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import recovery.auto_recovery_runtime_controls as runtime_controls
from detectors import ErrorEvent
from monitors.project_registry import PolicyConfig, ProjectConfig, ProjectRegistry
from policies import RemediationDecision, RemediationPolicy
from policies.auto_recovery_policy import MANUAL_ESCALATION_EVENT_TYPES
from recovery.auto_recovery_runner import AutoRecoveryRunner
from recovery.auto_recovery_runtime_gate import (
    build_runtime_auto_recovery_policy,
    evaluate_runtime_auto_recovery_gate,
)
from recovery.guarded_auto_recover_dry_run import (
    FORBIDDEN_ACTIONS,
    evaluate_guarded_auto_recover_dry_run,
)
from safe_recovery.registry import SAFE_RECOVERY_FIX_IDS, iter_safe_recovery_specs
from safe_recovery.semantics import (
    SEMANTIC_DISABLE_BOOL,
    SEMANTIC_LOWER_INT,
    SEMANTIC_PORT_AVAILABLE,
    SEMANTIC_SAFE_ENUM_DOWNGRADE,
)


REQUIRED_PRIVILEGE_ESCALATION_ALIASES = {
    "sudo",
    "/usr/bin/sudo",
    "pkexec",
    "doas",
    "runas",
    "权限提升",
    "提权",
    "privilege escalation",
}


def make_project(
    tmp_path: Path,
    *,
    config: dict[str, Any] | None = None,
    fix_ids: list[str] | None = None,
    dry_run: bool = True,
    mode: str = "local",
    rollback_on_failure: bool = True,
) -> ProjectConfig:
    project_dir = tmp_path / "project"
    if mode == "local":
        project_dir.mkdir()
        if config is not None:
            (project_dir / "config.json").write_text(
                json.dumps(config, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

    return ProjectConfig(
        project_id="r16_safe_domain_invariants",
        name="R16 Safe Domain Invariants",
        mode=mode,
        project_dir=str(project_dir) if mode == "local" else "",
        remote_project_dir=str(tmp_path / "remote_project") if mode == "remote" else "",
        run_command="python app.py",
        policy=PolicyConfig(
            auto_recover=True,
            allow_auto_apply=fix_ids or sorted(SAFE_RECOVERY_FIX_IDS),
            rollback_on_failure=rollback_on_failure,
            auto_recovery_policy_enabled=True,
            auto_recovery_dry_run=dry_run,
        ),
    )


def make_event(event_type: str, issue_type: str) -> ErrorEvent:
    return ErrorEvent(
        event_type=event_type,
        issue_type=issue_type,
        severity="medium",
        summary=f"{event_type} invariant evidence",
        source="r16-invariants",
        raw_excerpt=f"{event_type} invariant raw evidence",
        signature=f"r16-invariant-{event_type}",
    )


def evaluate_gate(event: ErrorEvent, project: ProjectConfig):
    decision = RemediationPolicy().decide(event=event, project=project)
    return evaluate_runtime_auto_recovery_gate(
        event=event,
        project=project,
        remediation_decision=decision,
    )


def config_for_first_candidate(spec, *, no_op: bool = False) -> dict[str, Any]:
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


class DryRunOnlySession:
    def __init__(self, tmp_path: Path) -> None:
        self.evidence_items: list[Any] = []
        self.report_path = tmp_path / "dry_run_report.md"
        self.report_path.write_text("# dry-run report\n", encoding="utf-8")
        self.auto_recovery_records: list[dict[str, Any]] = []

    def add_evidence(self, *, content: str, source: str, title: str, issue_type: str) -> None:
        self.evidence_items.append(
            type(
                "Evidence",
                (),
                {
                    "content": content,
                    "source": source,
                    "title": title,
                    "issue_type": issue_type,
                },
            )()
        )

    def generate_report(self, *args, **kwargs) -> tuple[str, str, str]:
        return "# dry-run report\n", str(self.report_path), "dry-run-session"

    def record_auto_recovery_result(self, **kwargs) -> None:
        self.auto_recovery_records.append(dict(kwargs))

    def remote_apply_fix(self, *args, **kwargs) -> str:
        raise AssertionError("remote_apply_fix must not be called in dry-run")

    def rerun_remote_project(self, *args, **kwargs) -> str:
        raise AssertionError("rerun_remote_project must not be called in dry-run")

    def apply_fix(self, *args, **kwargs) -> str:
        raise AssertionError("apply_fix must not be called in dry-run")

    def rerun_project(self, *args, **kwargs) -> str:
        raise AssertionError("rerun_project must not be called in dry-run")


def test_registry_safe_domains_only_target_project_json_config() -> None:
    forbidden_needles = [item.lower() for item in FORBIDDEN_ACTIONS]

    for spec in iter_safe_recovery_specs():
        assert spec.relative_config_path == "config.json"
        assert not Path(spec.relative_config_path).is_absolute()
        assert ".." not in Path(spec.relative_config_path).parts
        assert Path(spec.relative_config_path).suffix == ".json"
        assert spec.candidates
        assert spec.action_description.startswith("safe JSON config edit:")

        action_text = " ".join(
            [
                spec.fix_id,
                spec.action_description,
                spec.low_risk_reason,
                *[candidate.field_path for candidate in spec.candidates],
            ]
        ).lower()
        assert not any(needle in action_text for needle in forbidden_needles)


def test_privilege_escalation_aliases_are_forbidden() -> None:
    assert REQUIRED_PRIVILEGE_ESCALATION_ALIASES <= set(FORBIDDEN_ACTIONS)


def test_runtime_policy_matches_registry_and_defaults_to_dry_run(tmp_path: Path) -> None:
    project = make_project(tmp_path, dry_run=True)
    runtime_policy = build_runtime_auto_recovery_policy(project)
    specs_by_event = {spec.event_type: spec for spec in iter_safe_recovery_specs()}

    assert set(runtime_policy.event_type_policies) >= set(specs_by_event)

    for event_type, spec in specs_by_event.items():
        event_policy = runtime_policy.event_type_policies[event_type]
        assert event_policy.strategy_layer.value == "safe_auto_recover"
        assert event_policy.allowed_fix_ids == [spec.fix_id]
        assert event_policy.require_precheck is True
        assert event_policy.require_rollback is True
        assert event_policy.require_operator_confirmation is False
        assert event_policy.audit_required is True
        assert event_policy.dry_run is True

    configured_project = ProjectRegistry("configs/projects.yaml").get(
        "enterprise_demo_local"
    )
    assert configured_project.policy.auto_recovery_dry_run is True
    assert set(SAFE_RECOVERY_FIX_IDS) <= set(configured_project.policy.allow_auto_apply)


@pytest.mark.parametrize("spec", iter_safe_recovery_specs(), ids=lambda spec: spec.event_type)
def test_safe_domain_dry_run_gate_never_executes(tmp_path: Path, spec, monkeypatch) -> None:
    monkeypatch.setattr(
        runtime_controls,
        "_is_tcp_port_available",
        lambda host, port: True,
    )
    project = make_project(
        tmp_path,
        config=config_for_first_candidate(spec),
        fix_ids=[spec.fix_id],
        dry_run=True,
    )

    gate = evaluate_gate(make_event(spec.event_type, spec.issue_type), project)

    assert gate.auto_recover_allowed is True
    assert gate.is_candidate is True
    assert gate.dry_run is True
    assert gate.allowed_to_execute is False
    assert gate.would_execute is False
    assert gate.downgrade_reason == "r15_dry_run"
    assert gate.audit_record["execution_result"] == "not_run_r15_dry_run"


@pytest.mark.parametrize("spec", iter_safe_recovery_specs(), ids=lambda spec: spec.event_type)
def test_safe_domain_no_op_is_audited_without_execution(tmp_path: Path, spec) -> None:
    project = make_project(
        tmp_path,
        config=config_for_first_candidate(spec, no_op=True),
        fix_ids=[spec.fix_id],
        dry_run=False,
    )

    gate = evaluate_gate(make_event(spec.event_type, spec.issue_type), project)

    assert gate.auto_recover_allowed is True
    assert gate.precheck_result["no_op"] is True
    assert gate.precheck_result["semantic_status"] == "no_op"
    assert gate.allowed_to_execute is False
    assert gate.would_execute is False
    assert gate.downgrade_reason == "no_op_already_safe"
    assert gate.audit_record["execution_result"] == "not_run_r15_no_op"


@pytest.mark.parametrize("spec", iter_safe_recovery_specs(), ids=lambda spec: spec.event_type)
def test_safe_domain_precheck_failure_downgrades(tmp_path: Path, spec) -> None:
    project = make_project(
        tmp_path,
        config={"unrelated": "value"},
        fix_ids=[spec.fix_id],
        dry_run=False,
    )

    gate = evaluate_gate(make_event(spec.event_type, spec.issue_type), project)

    assert gate.auto_recover_allowed is True
    assert gate.allowed_to_execute is False
    assert gate.would_execute is False
    assert gate.downgrade_reason == "target_config_field_missing"
    assert "target_config_field_missing" in gate.precheck_result["reasons"]


@pytest.mark.parametrize("spec", iter_safe_recovery_specs(), ids=lambda spec: spec.event_type)
def test_safe_domain_audit_has_rollback_backup_and_diff_metadata(
    tmp_path: Path,
    spec,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        runtime_controls,
        "_is_tcp_port_available",
        lambda host, port: True,
    )
    project = make_project(
        tmp_path,
        config=config_for_first_candidate(spec),
        fix_ids=[spec.fix_id],
        dry_run=False,
    )

    gate = evaluate_gate(make_event(spec.event_type, spec.issue_type), project)
    precheck = gate.audit_record["precheck_result"]
    planned_edit = precheck["actionable_planned_edits"][0]
    rollback_plan = precheck["rollback_plan"]

    assert gate.allowed_to_execute is True
    assert gate.rollback_available is True
    assert rollback_plan["available"] is True
    assert rollback_plan["backup_created_before_write"] is True
    assert rollback_plan["record_name"] == "applied_fixes.json"
    assert planned_edit["field_path"] == spec.candidates[0].field_path
    assert planned_edit["old_value_available"] is True
    assert "old_value" in planned_edit
    assert "new_value" in planned_edit


@pytest.mark.parametrize("spec", iter_safe_recovery_specs(), ids=lambda spec: spec.event_type)
def test_dry_run_runner_does_not_call_apply_or_rerun_for_safe_domains(
    tmp_path: Path,
    spec,
) -> None:
    project = make_project(
        tmp_path,
        fix_ids=[spec.fix_id],
        dry_run=True,
        mode="remote",
    )
    session = DryRunOnlySession(tmp_path)
    result = AutoRecoveryRunner(project=project, session=session).recover(
        make_event(spec.event_type, spec.issue_type)
    )

    assert result.r15_gate is not None
    assert result.r15_gate.dry_run is True
    assert result.r15_gate.allowed_to_execute is False
    assert result.apply_success is False
    assert result.rerun_success is False
    assert session.auto_recovery_records
    assert session.auto_recovery_records[-1]["recovery_audit_record"][
        "execution_result"
    ] == "not_run_r15_dry_run"


@pytest.mark.parametrize("spec", iter_safe_recovery_specs(), ids=lambda spec: spec.event_type)
@pytest.mark.parametrize("forbidden_action", FORBIDDEN_ACTIONS)
def test_forbidden_actions_block_every_safe_domain_candidate(
    spec,
    forbidden_action: str,
) -> None:
    result = evaluate_guarded_auto_recover_dry_run(
        event_type=spec.event_type,
        fingerprint=f"forbidden-{spec.event_type}",
        candidate_fix_id=spec.fix_id,
        strategy_layer="safe_auto_recover",
        policy_decision={"auto_recover_allowed": True},
        precheck_result={"passed": True},
        cooldown_result={"allowed": True},
        rollback_available=True,
        action_description=forbidden_action,
    )

    assert result.strategy_layer == "disabled"
    assert result.downgrade_reason == "forbidden_action"
    assert result.allowed_by_policy is False
    assert result.would_execute is False
    assert result.audit_record["forbidden_action"] == forbidden_action


def test_unknown_fix_id_downgrades_before_execution(tmp_path: Path) -> None:
    spec = iter_safe_recovery_specs()[0]
    project = make_project(
        tmp_path,
        config=config_for_first_candidate(spec),
        fix_ids=[spec.fix_id],
        dry_run=False,
    )
    event = make_event(spec.event_type, spec.issue_type)
    decision = RemediationDecision(
        action="auto_recover",
        fix_id="unknown-fix",
        reason="synthetic unknown fix",
        should_rerun=True,
        rollback_on_failure=True,
    )

    gate = evaluate_runtime_auto_recovery_gate(
        event=event,
        project=project,
        remediation_decision=decision,
    )

    assert gate.auto_recover_allowed is False
    assert gate.allowed_to_execute is False
    assert gate.would_execute is False
    assert gate.selected_fix_id == ""
    assert gate.downgrade_reason == "candidate_fix_id_not_allowed_for_event_type"


@pytest.mark.parametrize("event_type", sorted(MANUAL_ESCALATION_EVENT_TYPES))
def test_high_risk_event_types_remain_manual_or_diagnose(
    tmp_path: Path,
    event_type: str,
) -> None:
    project = make_project(tmp_path, config={"metrics_port": 9000})
    event = make_event(event_type, event_type)
    decision = RemediationPolicy().decide(event=event, project=project)

    assert decision.is_auto_recover is False
    assert decision.action in {"manual_escalation", "report_only"}
