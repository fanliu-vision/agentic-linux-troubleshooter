from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.r17_real_log_shadow_evaluate import (
    evaluate_shadow_cases,
    load_manifest_cases,
    render_markdown,
)


FIXTURE_MANIFEST = (
    PROJECT_ROOT / "tests" / "fixtures" / "r17_real_log_shadow" / "manifest.json"
)


def write_case(tmp_path: Path, name: str, text: str) -> str:
    path = tmp_path / f"{name}.log"
    path.write_text(text.strip() + "\n", encoding="utf-8")
    return path.name


def test_r17_fixture_manifest_has_no_fp_fn_or_manual_noise() -> None:
    cases = load_manifest_cases(FIXTURE_MANIFEST)
    summary = evaluate_shadow_cases(cases)
    metrics = summary["metrics"]
    rows_by_id = {row["case_id"]: row for row in summary["rows"]}

    assert summary["conclusion"] == "PASS"
    assert metrics["case_count"] == 13
    assert metrics["expected_event_count"] == 15
    assert metrics["true_positive_count"] == 15
    assert metrics["false_positive_count"] == 0
    assert metrics["false_negative_count"] == 0
    assert metrics["safe_swallowed_high_risk_count"] == 0
    assert metrics["safe_high_risk_overlap_count"] == 3
    assert metrics["manual_escalation_noise_count"] == 0
    assert metrics["cross_domain_case_count"] == 4

    cross_domain = rows_by_id["prod_like_worker_queue_cross_domain"]
    assert set(cross_domain["detected_event_types"]) == {
        "queue_backpressure",
        "worker_overload",
    }
    assert cross_domain["cross_domain"] is True
    assert (
        cross_domain["cross_domain_flags"][0]["reason"]
        == "worker_queue_domain_overlap"
    )

    cache_disk = rows_by_id["prod_like_cache_disk_mixed_window"]
    assert set(cache_disk["detected_event_types"]) == {
        "cache_write_failed",
        "disk_full",
    }
    assert cache_disk["safe_high_risk_overlap"] is True
    assert cache_disk["safe_swallowed_high_risk"] is False

    markdown = render_markdown(summary)
    assert "# R17 真实日志 Shadow 汇总" in markdown
    assert "## 统计指标" in markdown
    assert "## 安全说明" in markdown
    assert "This evaluator is read-only" not in markdown


def test_r17_metrics_count_fp_fn_safe_swallow_and_manual_noise(tmp_path: Path) -> None:
    manifest = {
        "cases": [
            {
                "case_id": "false_positive_safe",
                "log_file": write_case(
                    tmp_path,
                    "false_positive_safe",
                    "OSError: [Errno 98] Address already in use",
                ),
                "expected_event_types": [],
            },
            {
                "case_id": "false_negative_disk",
                "log_file": write_case(
                    tmp_path,
                    "false_negative_disk",
                    "INFO order service heartbeat ok",
                ),
                "expected_event_types": ["disk_full"],
            },
            {
                "case_id": "safe_swallowed_high_risk",
                "log_file": write_case(
                    tmp_path,
                    "safe_swallowed_high_risk",
                    """
[queue] queue backpressure detected for local consumer pipeline
[queue] prefetch_count=64 is too high; max_inflight limit exhausted
""",
                ),
                "expected_event_types": ["dependency_service"],
            },
            {
                "case_id": "manual_noise_on_safe_case",
                "log_file": write_case(
                    tmp_path,
                    "manual_noise_on_safe_case",
                    """
[queue] queue backpressure detected for local consumer pipeline
[security] permission denied opening optional local metrics file
""",
                ),
                "expected_event_types": ["queue_backpressure"],
            },
            {
                "case_id": "worker_queue_cross_domain",
                "log_file": write_case(
                    tmp_path,
                    "worker_queue_cross_domain",
                    """
[worker] worker overload caused by queue backpressure
[worker] worker pool exhausted; concurrency too high
""",
                ),
                "expected_event_types": ["worker_overload", "queue_backpressure"],
            },
        ]
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    summary = evaluate_shadow_cases(load_manifest_cases(manifest_path))
    metrics = summary["metrics"]

    assert summary["conclusion"] == "REVIEW"
    assert metrics["false_positive_count"] >= 1
    assert metrics["false_negative_count"] >= 1
    assert metrics["safe_swallowed_high_risk_count"] == 1
    assert metrics["manual_escalation_noise_count"] >= 1
    assert metrics["cross_domain_case_count"] >= 1

    rows_by_id = {row["case_id"]: row for row in summary["rows"]}
    assert rows_by_id["safe_swallowed_high_risk"]["safe_swallowed_high_risk"]
    assert rows_by_id["manual_noise_on_safe_case"]["manual_escalation_noise"]
