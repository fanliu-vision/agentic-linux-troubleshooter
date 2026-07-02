from __future__ import annotations

import tempfile
import threading
from pathlib import Path

from monitors.jsonl_store import append_jsonl, read_jsonl
from monitors.report_index_store import REPORT_TYPE_DIAGNOSTIC, ReportIndexStore
from monitors.trace_store import TRACE_STAGE_DETECTED, TraceStore
from web_ui.runtime_control import JobStore


def test_jsonl_reader_skips_corrupt_half_lines_and_supports_paging() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "records.jsonl"
        path.write_text(
            '{"index": 0}\n'
            '{"index": 1}\n'
            '{"index": \n'
            '["not-object"]\n'
            '{"index": 2}\n',
            encoding="utf-8",
        )

        assert [item["index"] for item in read_jsonl(path)] == [0, 1, 2]
        assert [item["index"] for item in read_jsonl(path, offset=1, limit=1)] == [1]
        assert [item["index"] for item in read_jsonl(path, reverse=True, limit=2)] == [2, 1]


def test_jsonl_append_uses_lock_safe_records_under_parallel_writes() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "parallel.jsonl"

        def write_batch(batch: int) -> None:
            for index in range(25):
                append_jsonl(path, {"batch": batch, "index": index})

        threads = [threading.Thread(target=write_batch, args=(batch,)) for batch in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        records = read_jsonl(path)

        assert len(records) == 100
        assert {
            (item["batch"], item["index"])
            for item in records
        } == {
            (batch, index)
            for batch in range(4)
            for index in range(25)
        }


def test_trace_store_compact_removes_bad_lines_and_can_keep_latest_records() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = TraceStore(project_id="jsonl_trace", state_dir=tmp)
        for index in range(5):
            store.append(
                TRACE_STAGE_DETECTED,
                event_type="network_port",
                fingerprint=f"fp-{index}",
                payload={"index": index},
            )
        with store.trace_events_path.open("a", encoding="utf-8") as f:
            f.write('{"broken":\n')

        compacted = store.compact(keep_latest=3)

        assert [item["fingerprint"] for item in compacted] == ["fp-2", "fp-3", "fp-4"]
        assert len(read_jsonl(store.trace_events_path)) == 3


def test_job_store_paginates_and_compacts_latest_job_updates() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = JobStore(project_id="jsonl_jobs", state_dir=tmp)
        first = store.create(action="generate_report", operator="tester")
        second = store.create(action="dry_run_recovery", operator="tester")
        store.mark_running(first["job_id"], runtime_status="service_running", summary="running")

        page = store.jobs(offset=1, limit=1)
        compacted = store.compact()

        assert page[0]["job_id"] in {first["job_id"], second["job_id"]}
        assert len(compacted) == 2
        assert len(store.read_all()) == 2


def test_report_index_store_paginates_and_compacts_latest_records() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = ReportIndexStore(project_id="jsonl_reports", state_dir=tmp)
        report_a = store.register_text_report(
            content="A",
            report_type=REPORT_TYPE_DIAGNOSTIC,
            title="A",
        )
        store.register_report(
            path=report_a["path"],
            report_type=REPORT_TYPE_DIAGNOSTIC,
            title="A updated",
        )
        store.register_text_report(
            content="B",
            report_type=REPORT_TYPE_DIAGNOSTIC,
            title="B",
        )

        assert len(store.reports(offset=1, limit=1)) == 1
        compacted = store.compact()

        assert len(compacted) == 2
        assert len(store.read_all()) == 2
