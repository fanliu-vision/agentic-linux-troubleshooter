from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from detectors import ErrorEventDetector


def test_network_port_traceback_suppresses_generic_traceback() -> None:
    text = """
2026-06-10 10:00:01 [service] starting
Traceback (most recent call last):
  File "/srv/order-service/run_service.py", line 132, in start_metrics_exporter
    server_socket.bind(("127.0.0.1", 9100))
OSError: [Errno 98] Address already in use
[summary]
primary_failure=Address already in use
"""

    events = ErrorEventDetector().detect(text, source="local_log:service.log")

    assert len(events) == 1
    assert events[0].event_type == "network_port"
    assert events[0].issue_type == "network_port"
    assert events[0].severity == "medium"
    assert "address already in use" in events[0].signature


def test_same_error_has_stable_fingerprint_across_timestamps() -> None:
    detector = ErrorEventDetector()

    text1 = """
2026-06-10 10:00:01 ERROR OSError: [Errno 98] Address already in use
"""

    text2 = """
2026-06-10 10:05:55 ERROR OSError: [Errno 98] Address already in use
"""

    event1 = detector.detect(text1, source="local_log:service.log")[0]
    event2 = detector.detect(text2, source="local_log:service.log")[0]

    assert event1.fingerprint == event2.fingerprint


def test_mixed_log_detects_multiple_specific_events() -> None:
    text = """
RuntimeError: HIP out of memory. Tried to allocate 1024.00 MiB.
OSError: [Errno 28] No space left on device: "/tmp/cache"
ModuleNotFoundError: No module named 'yaml'
slurmstepd: error: Detected 1 oom-kill event(s)
"""

    events = ErrorEventDetector().detect(text, source="local_log:mixed.log")
    event_types = {event.event_type for event in events}
    issue_types = {event.issue_type for event in events}

    assert "gpu_oom" in event_types
    assert "disk_full" in event_types
    assert "python_env" in event_types
    assert "slurm" in event_types

    assert "gpu" in issue_types
    assert "disk" in issue_types
    assert "python_env" in issue_types
    assert "slurm" in issue_types


def test_error_event_can_be_converted_to_evidence_text() -> None:
    text = "OSError: [Errno 28] No space left on device"
    event = ErrorEventDetector().detect(text, source="local_log:service.log")[0]

    evidence = event.to_evidence_text()

    assert "[ERROR_EVENT]" in evidence
    assert "event_type: disk_full" in evidence
    assert "issue_type: disk" in evidence
    assert "fingerprint:" in evidence
    assert "[RAW_EXCERPT]" in evidence


def main() -> None:
    test_network_port_traceback_suppresses_generic_traceback()
    test_same_error_has_stable_fingerprint_across_timestamps()
    test_mixed_log_detects_multiple_specific_events()
    test_error_event_can_be_converted_to_evidence_text()
    print("=" * 100)
    print("STAGE 6B ERROR EVENT DETECTOR TEST PASSED")
    print("=" * 100)


if __name__ == "__main__":
    main()