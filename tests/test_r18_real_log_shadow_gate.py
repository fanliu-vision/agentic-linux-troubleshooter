from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.r18_real_log_shadow_gate import gate_failures, resolve_summary_path


def args(**overrides: int) -> argparse.Namespace:
    values = {
        "max_false_positive_count": 0,
        "max_false_negative_count": 0,
        "max_safe_swallowed_high_risk_count": 0,
        "max_manual_escalation_noise_count": 0,
        "min_labeled_case_count": 1,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_r18_shadow_gate_passes_clean_labeled_summary(tmp_path: Path) -> None:
    metrics = {
        "labeled_case_count": 3,
        "false_positive_count": 0,
        "false_negative_count": 0,
        "safe_swallowed_high_risk_count": 0,
        "manual_escalation_noise_count": 0,
    }

    assert gate_failures(args(), metrics) == []

    summary_dir = tmp_path / "shadow"
    summary_dir.mkdir()
    summary_path = summary_dir / "r17_real_log_shadow_summary.json"
    summary_path.write_text(json.dumps({"metrics": metrics}), encoding="utf-8")

    assert resolve_summary_path(str(summary_dir)) == summary_path.resolve()


def test_r18_shadow_gate_reports_threshold_failures() -> None:
    metrics = {
        "labeled_case_count": 0,
        "false_positive_count": 1,
        "false_negative_count": 2,
        "safe_swallowed_high_risk_count": 1,
        "manual_escalation_noise_count": 1,
    }

    failures = gate_failures(args(), metrics)

    assert "false_positive_count:1>0" in failures
    assert "false_negative_count:2>0" in failures
    assert "safe_swallowed_high_risk_count:1>0" in failures
    assert "manual_escalation_noise_count:1>0" in failures
    assert "labeled_case_count:0<1" in failures
