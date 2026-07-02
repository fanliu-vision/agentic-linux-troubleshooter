from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from monitors.report_index_store import (
    REPORT_TYPE_AUDIT_JSON,
    REPORT_TYPE_AUTO_RECOVERY,
    REPORT_TYPE_DIAGNOSTIC,
    REPORT_TYPE_ROLLBACK,
    ReportIndexStore,
)


def test_report_index_store_registers_and_opens_text_report() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state_dir = str(Path(tmp) / "state")
        store = ReportIndexStore(project_id="report_test", state_dir=state_dir)

        record = store.register_text_report(
            content="# 自动恢复报告\n\n恢复成功。",
            report_type=REPORT_TYPE_AUTO_RECOVERY,
            fingerprint="fp-1",
            event_type="network_port",
            job_id="job-1",
        )
        detail = store.detail(record["report_id"])

        assert record["project_id"] == "report_test"
        assert record["fingerprint"] == "fp-1"
        assert record["event_type"] == "network_port"
        assert record["job_id"] == "job-1"
        assert record["report_type"] == REPORT_TYPE_AUTO_RECOVERY
        assert detail["content_status"] == "ok"
        assert "恢复成功" in detail["content"]

        grouped = store.grouped_for_event("fp-1")
        assert grouped["event"][0]["report_id"] == record["report_id"]
        assert grouped["auto_recovery"][0]["report_id"] == record["report_id"]


def test_report_index_store_registers_audit_json_and_rollup_groups() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state_dir = str(Path(tmp) / "state")
        store = ReportIndexStore(project_id="report_test", state_dir=state_dir)

        audit = store.register_audit_json(
            audit_json={"execution_result": "executed_recovered"},
            fingerprint="fp-2",
            event_type="network_port",
            job_id="job-2",
        )
        rollback = store.register_text_report(
            content="rollback ok",
            report_type=REPORT_TYPE_ROLLBACK,
            fingerprint="fp-2",
            event_type="network_port",
            job_id="job-2",
        )
        store.register_text_report(
            content="cycle summary",
            report_type=REPORT_TYPE_DIAGNOSTIC,
        )

        grouped = store.grouped_for_event("fp-2")

        assert grouped["audit_json"][0]["report_id"] == audit["report_id"]
        assert grouped["rollback"][0]["report_id"] == rollback["report_id"]
        assert grouped["latest"][0]["report_type"] == REPORT_TYPE_DIAGNOSTIC

        detail = store.detail(audit["report_id"])
        parsed = json.loads(detail["content"])
        assert parsed["execution_result"] == "executed_recovered"


def test_report_index_store_blocks_opening_paths_outside_allowed_roots() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state_dir = str(Path(tmp) / "state")
        store = ReportIndexStore(project_id="report_test", state_dir=state_dir)
        record = store.register_report(
            path="/etc/passwd",
            report_type=REPORT_TYPE_DIAGNOSTIC,
        )

        detail = store.detail(record["report_id"])

        assert detail["content_status"] == "path_not_allowed"
        assert detail["content"] == ""
