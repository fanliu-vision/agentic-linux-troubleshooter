from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from monitors.state_store import MonitorStateStore, ProjectMonitorState


def test_stage6e_state_store_save_and_load() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state_dir = Path(tmp) / "state"

        store = MonitorStateStore(
            project_id="test_project",
            state_dir=str(state_dir),
        )

        state = ProjectMonitorState(project_id="test_project")
        state.status = "running"
        state.run_count = 3
        state.events_detected_total = 2
        state.last_event_type = "network_port"
        state.seen_fingerprints = ["abc", "def"]
        state.runtime_health = {
            "health_status": "ok",
            "last_cycle_started_at": "2026-06-18 10:00:00",
            "last_cycle_finished_at": "2026-06-18 10:00:01",
            "last_events_detected": 1,
        }
        state.remote_log_watermarks = {
            "/var/log/service.log": {
                "inode": "12345",
                "size": 1048576,
                "mtime": 1780000000,
                "offset": 1048576,
                "last_read_at": "2026-06-30 12:00:00",
                "last_strategy": "tail_bootstrap",
                "fallback_reason": "",
            }
        }

        store.save(state)

        loaded = store.load()

        assert loaded.project_id == "test_project"
        assert loaded.status == "running"
        assert loaded.run_count == 3
        assert loaded.events_detected_total == 2
        assert loaded.last_event_type == "network_port"
        assert set(loaded.seen_fingerprints) == {"abc", "def"}
        assert loaded.runtime_health["health_status"] == "ok"
        assert loaded.runtime_health["last_events_detected"] == 1
        assert loaded.remote_log_watermarks["/var/log/service.log"] == {
            "inode": "12345",
            "size": 1048576,
            "mtime": 1780000000,
            "offset": 1048576,
            "last_read_at": "2026-06-30 12:00:00",
            "last_strategy": "tail_bootstrap",
            "fallback_reason": "",
        }

        assert store.status_path.exists()

        raw_status = json.loads(store.status_path.read_text(encoding="utf-8"))
        assert raw_status["status"] == "running"
        assert raw_status["run_count"] == 3
        assert raw_status["runtime_health"]["health_status"] == "ok"
        assert raw_status["remote_log_watermarks"]["/var/log/service.log"]["inode"] == "12345"


def test_stage6e_state_store_loads_old_state_without_remote_log_watermarks() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state_dir = Path(tmp) / "state"

        store = MonitorStateStore(
            project_id="test_project",
            state_dir=str(state_dir),
        )

        store.status_path.write_text(
            json.dumps(
                {
                    "project_id": "test_project",
                    "status": "running",
                    "seen_fingerprints": ["abc"],
                    "runtime_health": {"health_status": "ok"},
                }
            ),
            encoding="utf-8",
        )

        loaded = store.load()

        assert loaded.status == "running"
        assert loaded.seen_fingerprints == ["abc"]
        assert loaded.remote_log_watermarks == {}


def test_stage6e_state_store_ignores_invalid_remote_log_watermarks() -> None:
    loaded = ProjectMonitorState.from_dict(
        {
            "project_id": "test_project",
            "remote_log_watermarks": {
                "/var/log/service.log": {
                    "inode": "12345",
                    "size": 1048576,
                },
                "/var/log/bad.log": "invalid",
            },
        }
    )

    assert loaded.remote_log_watermarks == {
        "/var/log/service.log": {
            "inode": "12345",
            "size": 1048576,
        }
    }


def test_stage6e_mark_seen() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state_dir = Path(tmp) / "state"

        store = MonitorStateStore(
            project_id="test_project",
            state_dir=str(state_dir),
        )

        store.mark_seen("fingerprint-1")
        store.mark_seen("fingerprint-1")
        store.mark_seen("fingerprint-2")

        seen = store.seen_fingerprints()

        assert seen == {"fingerprint-1", "fingerprint-2"}

        loaded = store.load()
        assert loaded.seen_fingerprints.count("fingerprint-1") == 1


def main() -> None:
    test_stage6e_state_store_save_and_load()
    test_stage6e_state_store_loads_old_state_without_remote_log_watermarks()
    test_stage6e_state_store_ignores_invalid_remote_log_watermarks()
    test_stage6e_mark_seen()

    print("=" * 100)
    print("STAGE 6E STATE STORE TEST PASSED")
    print("=" * 100)


if __name__ == "__main__":
    main()
