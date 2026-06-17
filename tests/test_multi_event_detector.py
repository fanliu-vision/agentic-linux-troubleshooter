from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from detectors import ErrorEventDetector


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "regression_logs"
EXPECTED_MULTI_EVENT_CASES_PATH = FIXTURE_DIR / "expected_multi_event_cases.json"


def read_fixture(name: str) -> str:
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")


def load_expected_multi_event_cases() -> list[dict[str, Any]]:
    return json.loads(EXPECTED_MULTI_EVENT_CASES_PATH.read_text(encoding="utf-8"))


def test_detect_all_returns_process_crash_and_container_k8s_for_combined_fixture() -> None:
    case = load_expected_multi_event_cases()[0]
    text = read_fixture(case["log_file"])

    events = ErrorEventDetector().detect_all(
        text,
        source=f"fixture:{case['log_file']}",
    )
    event_types = [event.event_type for event in events]

    assert len(events) > 1
    assert set(case["expected_event_types"]).issubset(event_types)
    assert event_types.count("process_crash") == 1
    assert event_types.count("container_k8s") == 1


def test_detect_all_keeps_raw_evidence_scoped_to_each_event_type() -> None:
    text = read_fixture("multi_event_process_crash_container_k8s.log")

    events = ErrorEventDetector().detect_all(text, source="fixture:multi")
    by_type = {event.event_type: event for event in events}

    assert "Segmentation fault" in by_type["process_crash"].raw_excerpt
    assert "CrashLoopBackOff" not in by_type["process_crash"].raw_excerpt
    assert "CrashLoopBackOff" in by_type["container_k8s"].raw_excerpt
    assert "Segmentation fault" not in by_type["container_k8s"].raw_excerpt


def test_detect_compatibility_keeps_legacy_first_event_order() -> None:
    text = read_fixture("multi_event_process_crash_container_k8s.log")
    detector = ErrorEventDetector()

    legacy_events = detector.detect(text, source="fixture:multi")
    multi_events = detector.detect_all(text, source="fixture:multi")

    assert legacy_events
    assert multi_events
    assert legacy_events[0].event_type == multi_events[0].event_type


def test_detect_all_returns_empty_list_for_benign_info() -> None:
    text = read_fixture("benign_info.log")

    events = ErrorEventDetector().detect_all(text, source="fixture:benign_info.log")

    assert events == []


@pytest.mark.parametrize(
    ("fixture_name", "expected_event_type"),
    [
        ("network_port_basic.log", "network_port"),
        ("gpu_oom_basic.log", "gpu_oom"),
        ("process_crash_basic.log", "process_crash"),
        ("container_k8s_basic.log", "container_k8s"),
    ],
)
def test_detect_all_returns_one_event_for_single_event_fixture(
    fixture_name: str,
    expected_event_type: str,
) -> None:
    text = read_fixture(fixture_name)

    events = ErrorEventDetector().detect_all(text, source=f"fixture:{fixture_name}")

    assert len(events) == 1
    assert events[0].event_type == expected_event_type
    assert events[0].raw_excerpt
    assert events[0].signature
    assert events[0].fingerprint


def test_detect_all_does_not_duplicate_same_event_type() -> None:
    text = """
2026-06-16 15:00:01 kubelet pod/orders-a: status=CrashLoopBackOff
2026-06-16 15:00:02 kubelet pod/orders-b: status=ImagePullBackOff
2026-06-16 15:00:03 kubelet pod/orders-c: ErrImagePull pulling image registry.internal/orders-c:bad
2026-06-16 15:00:04 kubelet pod/orders-a: Back-off restarting failed container
"""

    events = ErrorEventDetector().detect_all(text, source="inline:k8s_duplicates")
    event_types = [event.event_type for event in events]

    assert event_types == ["container_k8s"]
    assert events[0].raw_excerpt.count("kubelet pod/") == 4
