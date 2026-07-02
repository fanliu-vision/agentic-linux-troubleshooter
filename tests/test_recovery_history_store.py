from __future__ import annotations

import tempfile
from pathlib import Path

from monitors.recovery_history_store import (
    ROLLBACK_STATUS_SUCCEEDED,
    RecoveryHistoryStore,
)


def test_recovery_history_store_keeps_rolled_back_record_without_scan() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = RecoveryHistoryStore(project_id="history_test", state_dir=tmp)
        applied = store.register_applied(
            fix_id="fix-network-1",
            edits=[
                {
                    "field_path": "metrics_port",
                    "old_value": 9000,
                    "new_value": 9101,
                    "config_path": "/srv/app/config.json",
                    "backup_path": "/srv/app/.agent_backups/config.bak",
                    "diff_path": "/srv/app/.agent_patches/config.diff",
                }
            ],
            record_path="/tmp/applied_fixes.json",
            record_index=0,
            fingerprint="fp-1",
            event_type="network_port",
            job_id="job-1",
            mode="local",
        )

        store.record_rollback_started(
            target=applied,
            job_id="rollback-job",
            operator="tester",
        )
        store.record_rollback_finished(
            target=applied,
            job_id="rollback-job",
            operator="tester",
            success=True,
            rollback_edits=[{"config_path": "/srv/app/config.json", "success": True}],
            report_id="report-1",
            audit_json={"rollback_success": True},
        )

        rows = store.merged_records([])

        assert rows[0]["fix_id"] == "fix-network-1"
        assert rows[0]["fingerprint"] == "fp-1"
        assert rows[0]["rollback_status"] == ROLLBACK_STATUS_SUCCEEDED
        assert rows[0]["rollback_available"] is False
        assert rows[0]["rollback_report_id"] == "report-1"
        assert rows[0]["edits"][0]["old_value"] == 9000
        assert Path(tmp, "history_test", "recovery_history.jsonl").exists()
