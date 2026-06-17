from __future__ import annotations

import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from monitors.cycle_summary_reporter import CycleEventRecord, CycleSummaryReporter
from monitors.project_registry import ProjectConfig


def test_cycle_summary_partially_recovered() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        output_dir = Path(tmp) / "cycle_summary"
        report_root = Path(tmp) / "reports"
        project = ProjectConfig(
            project_id="enterprise_demo_local",
            name="Enterprise Demo",
            mode="remote",
            owner="lf",
        )

        reporter = CycleSummaryReporter(project)

        records = [
            CycleEventRecord(
                event_type="gpu_oom",
                issue_type="gpu",
                severity="high",
                summary="GPU OOM",
                source="test",
                fingerprint="gpu-1",
                action="auto_recover",
                fix_id="fix-gpu-1",
                apply_success=True,
                rerun_success=False,
                rollback_executed=True,
                recovered=False,
                notification_status="rollback_done",
                notification_channels=["console", "file"],
                notification_results=["[Notifier][Console] sent"],
                report_paths=[str(report_root / "gpu_report.md")],
            ),
            CycleEventRecord(
                event_type="network_port",
                issue_type="network_port",
                severity="medium",
                summary="port conflict",
                source="test",
                fingerprint="net-1",
                action="auto_recover",
                fix_id="fix-network-1",
                apply_success=True,
                rerun_success=True,
                rollback_executed=False,
                recovered=True,
                notification_status="recovered",
                notification_channels=["console", "file"],
                notification_results=["[Notifier][Console] sent"],
                report_paths=[str(report_root / "network_report.md")],
            ),
        ]

        assert reporter.compute_overall_status(records) == "partially_recovered"

        report_path = reporter.write_report(records, output_dir)
        text = Path(report_path).read_text(encoding="utf-8")

        assert "overall_status: `partially_recovered`" in text
        assert "`gpu_oom`" in text
        assert "`network_port`" in text
        assert "`rollback_done`" in text
        assert "`recovered`" in text
        assert "如果任一事件 `recovered=False`" in text


def test_cycle_summary_all_recovered() -> None:
    project = ProjectConfig(
        project_id="enterprise_demo_local",
        name="Enterprise Demo",
        mode="remote",
        owner="lf",
    )

    reporter = CycleSummaryReporter(project)

    records = [
        CycleEventRecord(
            event_type="network_port",
            issue_type="network_port",
            severity="medium",
            summary="port conflict",
            source="test",
            fingerprint="net-1",
            action="auto_recover",
            fix_id="fix-network-1",
            apply_success=True,
            rerun_success=True,
            rollback_executed=False,
            recovered=True,
        ),
    ]

    assert reporter.compute_overall_status(records) == "recovered"


def main() -> None:
    test_cycle_summary_partially_recovered()
    test_cycle_summary_all_recovered()

    print("=" * 100)
    print("STAGE 6 CYCLE SUMMARY REPORTER TEST PASSED")
    print("=" * 100)


if __name__ == "__main__":
    main()
