from __future__ import annotations

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

        store.save(state)

        loaded = store.load()

        assert loaded.project_id == "test_project"
        assert loaded.status == "running"
        assert loaded.run_count == 3
        assert loaded.events_detected_total == 2
        assert loaded.last_event_type == "network_port"
        assert set(loaded.seen_fingerprints) == {"abc", "def"}

        assert store.status_path.exists()


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
    test_stage6e_mark_seen()

    print("=" * 100)
    print("STAGE 6E STATE STORE TEST PASSED")
    print("=" * 100)


if __name__ == "__main__":
    main()
