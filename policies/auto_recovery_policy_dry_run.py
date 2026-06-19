from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

from .auto_recovery_policy import (
    AutoRecoveryDecision,
    PolicyValidationError,
    StrategyLayer,
    resolve_policy_for_event,
    validate_policy,
)


@dataclass
class PolicyDryRunDecision:
    event_type: str
    fingerprint: str
    strategy_layer: str
    auto_recover_allowed: bool
    dry_run: bool
    selected_fix_id: str = ""
    downgrade_reason: str = ""
    operator_required: bool = False
    audit_required: bool = True

    @classmethod
    def from_decision(cls, decision: AutoRecoveryDecision) -> PolicyDryRunDecision:
        return cls(
            event_type=decision.event_type,
            fingerprint=decision.fingerprint,
            strategy_layer=decision.strategy_layer.value,
            auto_recover_allowed=decision.auto_recover_allowed,
            dry_run=decision.dry_run,
            selected_fix_id=decision.selected_fix_id,
            downgrade_reason=decision.downgrade_reason,
            operator_required=decision.operator_required,
            audit_required=decision.audit_required,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "fingerprint": self.fingerprint,
            "strategy_layer": self.strategy_layer,
            "auto_recover_allowed": self.auto_recover_allowed,
            "dry_run": self.dry_run,
            "selected_fix_id": self.selected_fix_id,
            "downgrade_reason": self.downgrade_reason,
            "operator_required": self.operator_required,
            "audit_required": self.audit_required,
        }


@dataclass
class PolicyDryRunResult:
    policy_valid: bool
    validation_errors: list[str] = field(default_factory=list)
    decisions: list[PolicyDryRunDecision] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    report_markdown: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_valid": self.policy_valid,
            "validation_errors": list(self.validation_errors),
            "decisions": [decision.to_dict() for decision in self.decisions],
            "summary": dict(self.summary),
            "report_markdown": self.report_markdown,
        }


def load_policy_schema(path: str | Path) -> dict[str, Any]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"policy schema must be a mapping: {path}")
    return data


def run_policy_dry_run(
    policy: Mapping[str, Any],
    sample_events: Iterable[Mapping[str, Any]],
) -> PolicyDryRunResult:
    sample_event_list = list(sample_events)

    try:
        validated_policy = validate_policy(policy)
    except PolicyValidationError as exc:
        result = PolicyDryRunResult(
            policy_valid=False,
            validation_errors=[str(exc)],
            decisions=[],
            summary=_build_summary(
                policy_valid=False,
                validation_errors=[str(exc)],
                decisions=[],
                total_events=len(sample_event_list),
            ),
        )
        result.report_markdown = render_policy_dry_run_report(result)
        return result

    decisions: list[PolicyDryRunDecision] = []
    validation_errors: list[str] = []

    for sample_event in sample_event_list:
        try:
            decision = resolve_policy_for_event(
                event_type=str(sample_event["event_type"]),
                fingerprint=str(sample_event["fingerprint"]),
                confidence=float(sample_event.get("confidence", 0.0)),
                candidate_fix_id=str(sample_event.get("candidate_fix_id", "") or ""),
                policy=validated_policy,
            )
            decisions.append(PolicyDryRunDecision.from_decision(decision))
        except (KeyError, TypeError, ValueError, PolicyValidationError) as exc:
            validation_errors.append(
                f"sample_event_invalid: {type(exc).__name__}: {exc}"
            )

    result = PolicyDryRunResult(
        policy_valid=not validation_errors,
        validation_errors=validation_errors,
        decisions=decisions,
        summary=_build_summary(
            policy_valid=not validation_errors,
            validation_errors=validation_errors,
            decisions=decisions,
            total_events=len(sample_event_list),
        ),
    )
    result.report_markdown = render_policy_dry_run_report(result)
    return result


def render_policy_dry_run_report(result: PolicyDryRunResult) -> str:
    lines = [
        "# R15-4 Policy Schema Dry-Run Report",
        "",
        "## Summary",
        "",
        f"- policy_valid: `{result.policy_valid}`",
        f"- total_events: `{result.summary.get('total_events', 0)}`",
        f"- decisions_count: `{result.summary.get('decisions_count', 0)}`",
        f"- auto_recover_allowed_count: `{result.summary.get('auto_recover_allowed_count', 0)}`",
        f"- dry_run_auto_recover_count: `{result.summary.get('dry_run_auto_recover_count', 0)}`",
        f"- operator_required_count: `{result.summary.get('operator_required_count', 0)}`",
        f"- validation_error_count: `{result.summary.get('validation_error_count', 0)}`",
        "",
        "## Strategy Counts",
        "",
    ]

    by_strategy = result.summary.get("by_strategy_layer", {})
    if by_strategy:
        for strategy_layer in sorted(by_strategy):
            lines.append(f"- {strategy_layer}: `{by_strategy[strategy_layer]}`")
    else:
        lines.append("- <none>: `0`")

    if result.validation_errors:
        lines.extend(["", "## Validation Errors", ""])
        for error in result.validation_errors:
            lines.append(f"- {error}")

    lines.extend(
        [
            "",
            "## Decisions",
            "",
            "| event_type | fingerprint | strategy_layer | auto_recover_allowed | dry_run | selected_fix_id | downgrade_reason | operator_required | audit_required |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )

    for decision in result.decisions:
        lines.append(
            "| "
            f"{decision.event_type} | "
            f"{decision.fingerprint} | "
            f"{decision.strategy_layer} | "
            f"{decision.auto_recover_allowed} | "
            f"{decision.dry_run} | "
            f"{decision.selected_fix_id or '<none>'} | "
            f"{decision.downgrade_reason or '<none>'} | "
            f"{decision.operator_required} | "
            f"{decision.audit_required} |"
        )

    if not result.decisions:
        lines.append("| <none> | <none> | <none> | False | True | <none> | <none> | False | False |")

    lines.extend(
        [
            "",
            "## Safety",
            "",
            "- This dry-run does not execute recovery actions.",
            "- This dry-run does not call AutoRecoveryRunner.",
            "- `safe_auto_recover` only means a policy candidate with `dry_run=true`.",
        ]
    )

    return "\n".join(lines)


def write_policy_dry_run_report(
    result: PolicyDryRunResult,
    output_path: str | Path,
) -> Path:
    path = Path(output_path)
    _ensure_allowed_report_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(result.report_markdown, encoding="utf-8")
    return path


def _build_summary(
    *,
    policy_valid: bool,
    validation_errors: list[str],
    decisions: list[PolicyDryRunDecision],
    total_events: int,
) -> dict[str, Any]:
    by_strategy = {layer.value: 0 for layer in StrategyLayer}
    for decision in decisions:
        by_strategy[decision.strategy_layer] = by_strategy.get(decision.strategy_layer, 0) + 1

    return {
        "policy_valid": policy_valid,
        "total_events": total_events,
        "decisions_count": len(decisions),
        "validation_error_count": len(validation_errors),
        "auto_recover_allowed_count": sum(
            1 for decision in decisions if decision.auto_recover_allowed
        ),
        "dry_run_auto_recover_count": sum(
            1
            for decision in decisions
            if decision.auto_recover_allowed and decision.dry_run
        ),
        "operator_required_count": sum(
            1 for decision in decisions if decision.operator_required
        ),
        "audit_required_count": sum(1 for decision in decisions if decision.audit_required),
        "by_strategy_layer": by_strategy,
    }


def _ensure_allowed_report_path(path: Path) -> None:
    resolved = path.resolve()
    repo_root = Path(__file__).resolve().parents[1]
    acceptance_root = (repo_root / "acceptance_artifacts").resolve()
    tmp_root = Path("/tmp").resolve()

    if _is_relative_to(resolved, acceptance_root) or _is_relative_to(resolved, tmp_root):
        return

    raise ValueError(
        "dry-run reports may only be written under /tmp or acceptance_artifacts"
    )


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True
