from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from detectors import ErrorEvent
from monitors.project_registry import NotificationConfig, PolicyConfig, ProjectConfig
from monitors.rate_limit_tracker import RateLimitTracker
from notifiers import NotificationManager
from notifiers.file_notifier import FileNotifier
from notifiers.notification_manager import NotificationMessage
from policies import RemediationDecision


class FakeRecoveryResult:
    def __init__(self) -> None:
        self.decision = RemediationDecision(
            action="auto_recover",
            fix_id="fix-network-1",
            reason="test",
        )
        self.apply_success = True
        self.rerun_success = True
        self.rollback_executed = False
        self.report_paths = ["outputs/test/final_rule_report.md"]

    @property
    def recovered(self) -> bool:
        return self.apply_success and self.rerun_success


def make_project(alerts_dir: str) -> ProjectConfig:
    return ProjectConfig(
        project_id="test_project",
        name="Test Project",
        mode="local",
        owner="tester",
        owner_contact="console",
        project_dir=".",
        run_command="python app.py",
        policy=PolicyConfig(
            auto_recover=True,
            allow_auto_apply=["fix-network-1"],
            escalation_required=["disk", "slurm"],
        ),
        notification=NotificationConfig(
            enabled=True,
            channels=["file"],
            alerts_dir=alerts_dir,
        ),
    )


def test_stage6d_file_notification() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        alerts_dir = str(Path(tmp) / "alerts")
        project = make_project(alerts_dir)

        event = ErrorEvent(
            event_type="network_port",
            issue_type="network_port",
            severity="medium",
            summary="端口占用或服务绑定失败",
            source="test",
            raw_excerpt="OSError: [Errno 98] Address already in use",
            signature="oserror: [errno 98] address already in use",
        )

        manager = NotificationManager(project)
        results = manager.notify_recovery(event, FakeRecoveryResult())

        assert results
        assert any("Notifier" in item for item in results)

        jsonl_path = Path(alerts_dir) / "test_project_alerts.jsonl"
        md_path = Path(alerts_dir) / "test_project_latest_alert.md"

        assert jsonl_path.exists()
        assert md_path.exists()

        latest = md_path.read_text(encoding="utf-8")
        assert "Stage 6D 通知消息" in latest
        assert "test_project" in latest
        assert "fix-network-1" in latest
        assert "recovered" in latest


def test_notification_manager_rate_limits_duplicate_fingerprint() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        alerts_dir = str(Path(tmp) / "alerts")
        project = make_project(alerts_dir)
        tracker = RateLimitTracker()

        event = ErrorEvent(
            event_type="network_port",
            issue_type="network_port",
            severity="medium",
            summary="端口占用或服务绑定失败",
            source="test",
            raw_excerpt="OSError: [Errno 98] Address already in use",
            signature="same duplicate alert fingerprint",
        )

        manager = NotificationManager(project, rate_limit_tracker=tracker)

        first = manager.notify_recovery(event, FakeRecoveryResult())
        second = manager.notify_recovery(event, FakeRecoveryResult())

        assert any("[Notifier][File]" in item for item in first)
        assert second == [
            (
                "[Notifier][RateLimit] alert suppressed because fingerprint was "
                f"already alerted: event_type=network_port, fingerprint={event.fingerprint}"
            )
        ]

        jsonl_path = Path(alerts_dir) / "test_project_alerts.jsonl"
        archive_dir = Path(alerts_dir) / "test_project_alerts"

        assert len(jsonl_path.read_text(encoding="utf-8").splitlines()) == 1
        assert len(list(archive_dir.glob("*.md"))) == 1
        assert len(list(archive_dir.glob("*.json"))) == 1


def test_file_notifier_alert_archive_append_and_safe_names() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        alerts_dir = Path(tmp) / "alerts"

        notifier = FileNotifier(alerts_dir=str(alerts_dir))
        first = NotificationMessage(
            project_id="Unsafe Project/One!",
            project_name="Archive Test",
            owner="tester",
            owner_contact="console",
            event_type="network port/conflict!",
            issue_type="network_port",
            severity="medium",
            event_summary="first event",
            action="auto_recover",
            fix_id="fix-network-1",
            apply_success=True,
            rerun_success=True,
            recovered=True,
            status="recovered ok!",
            message="first notification",
        )
        second = NotificationMessage(
            project_id="Unsafe Project/One!",
            project_name="Archive Test",
            owner="tester",
            owner_contact="console",
            event_type="gpu oom/again!",
            issue_type="gpu",
            severity="high",
            event_summary="second event",
            action="auto_recover",
            fix_id="fix-gpu-1",
            apply_success=True,
            rerun_success=True,
            recovered=True,
            status="recovered ok!",
            message="second notification",
        )

        send_result = notifier.send(first)
        notify_result = notifier.notify(second)

        assert "[Notifier][File]" in send_result
        assert "[Notifier][File]" in notify_result

        safe_project_id = "unsafe_project_one"
        jsonl_path = alerts_dir / f"{safe_project_id}_alerts.jsonl"
        latest_md_path = alerts_dir / f"{safe_project_id}_latest_alert.md"
        archive_dir = alerts_dir / f"{safe_project_id}_alerts"

        assert jsonl_path.exists()
        assert latest_md_path.exists()
        assert archive_dir.exists()

        jsonl_lines = jsonl_path.read_text(encoding="utf-8").splitlines()
        assert len(jsonl_lines) == 2
        assert json.loads(jsonl_lines[0])["message"] == "first notification"
        assert json.loads(jsonl_lines[1])["message"] == "second notification"

        latest = latest_md_path.read_text(encoding="utf-8")
        assert "second notification" in latest
        assert "first notification" not in latest

        archive_md_paths = sorted(archive_dir.glob("*.md"))
        archive_json_paths = sorted(archive_dir.glob("*.json"))
        assert len(archive_md_paths) == 2
        assert len(archive_json_paths) == 2

        safe_filename = re.compile(r"^[A-Za-z0-9_-]+\.(md|json)$")
        for path in [*archive_md_paths, *archive_json_paths]:
            assert safe_filename.match(path.name), path.name
            assert " " not in path.name
            assert "/" not in path.name

        archive_payloads = [
            json.loads(path.read_text(encoding="utf-8")) for path in archive_json_paths
        ]
        assert {payload["message"] for payload in archive_payloads} == {
            "first notification",
            "second notification",
        }


def test_notification_manager_reports_unsupported_channels() -> None:
    project = make_project("unused")
    project.notification.channels = ["unknown-channel", "another channel"]
    manager = NotificationManager(project)
    message = NotificationMessage(
        project_id="test_project",
        project_name="Test Project",
        owner="tester",
        owner_contact="console",
        event_type="network_port",
        issue_type="network_port",
        severity="medium",
        event_summary="event",
        action="report_only",
    )

    results = manager._dispatch(message)

    assert results == [
        "[Notifier][unknown-channel][WARN] unsupported notification channel.",
        "[Notifier][another channel][WARN] unsupported notification channel.",
    ]


def main() -> None:
    test_stage6d_file_notification()
    test_notification_manager_rate_limits_duplicate_fingerprint()
    test_file_notifier_alert_archive_append_and_safe_names()
    test_notification_manager_reports_unsupported_channels()
    print("=" * 100)
    print("STAGE 6D NOTIFICATION TEST PASSED")
    print("=" * 100)


if __name__ == "__main__":
    main()
