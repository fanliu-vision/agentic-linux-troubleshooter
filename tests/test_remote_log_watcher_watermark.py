from __future__ import annotations

from types import SimpleNamespace

from monitors.log_watcher import RemoteLogWatcher
from tools.remote_ssh_executor import RemoteCommandResult, RemoteSSHProfile


def _profile() -> RemoteSSHProfile:
    return RemoteSSHProfile(user="lf", host="localhost", port=22)


def _result(
    stdout: str = "",
    return_code: int | None = 0,
    allowed: bool = True,
    command: str = "remote-test",
) -> RemoteCommandResult:
    return RemoteCommandResult(
        command=command,
        profile=_profile(),
        allowed=allowed,
        return_code=return_code,
        stdout=stdout,
        stderr="",
        reason="test",
    )


def _stat(inode: str, size: int, mtime: int) -> RemoteCommandResult:
    return _result(stdout=f"{inode} {size} {mtime}", command="stat")


class FakeRemoteExecutor:
    def __init__(
        self,
        stats: list[RemoteCommandResult] | None = None,
        tails: list[RemoteCommandResult] | None = None,
        ranges: list[RemoteCommandResult] | None = None,
    ) -> None:
        self.stats = list(stats or [])
        self.tails = list(tails or [])
        self.ranges = list(ranges or [])
        self.calls: list[tuple] = []

    def stat_remote_log(
        self,
        profile: RemoteSSHProfile,
        remote_path: str,
    ) -> RemoteCommandResult:
        self.calls.append(("stat", remote_path))
        return self.stats.pop(0)

    def read_remote_log_tail(
        self,
        profile: RemoteSSHProfile,
        remote_path: str,
        lines: int,
    ) -> RemoteCommandResult:
        self.calls.append(("tail", remote_path, lines))
        return self.tails.pop(0)

    def read_remote_log_range(
        self,
        profile: RemoteSSHProfile,
        remote_path: str,
        offset: int,
        max_bytes: int,
    ) -> RemoteCommandResult:
        self.calls.append(("range", remote_path, offset, max_bytes))
        return self.ranges.pop(0)


def _session(executor: FakeRemoteExecutor):
    return SimpleNamespace(
        remote_profile=_profile(),
        remote_executor=executor,
    )


def test_remote_log_watcher_bootstraps_with_tail_and_sets_offset_to_size() -> None:
    path = "/tmp/service.log"
    watermarks: dict[str, dict] = {}
    executor = FakeRemoteExecutor(
        stats=[_stat("12345", 100, 1780000000)],
        tails=[_result(stdout="tail bootstrap log", command="tail")],
    )

    watcher = RemoteLogWatcher(
        log_files=[path],
        session=_session(executor),
        tail_lines=50,
        remote_log_watermarks=watermarks,
    )

    chunks = watcher.poll()

    assert len(chunks) == 1
    assert "tail bootstrap log" in chunks[0].content
    assert watermarks[path]["inode"] == "12345"
    assert watermarks[path]["size"] == 100
    assert watermarks[path]["mtime"] == 1780000000
    assert watermarks[path]["offset"] == 100
    assert watermarks[path]["last_strategy"] == "tail_bootstrap"
    assert watermarks[path]["fallback_reason"] == ""
    assert executor.calls == [
        ("stat", path),
        ("tail", path, 50),
    ]


def test_remote_log_watcher_reads_incremental_range_for_same_inode() -> None:
    path = "/tmp/service.log"
    watermarks = {
        path: {
            "inode": "12345",
            "size": 100,
            "mtime": 1780000000,
            "offset": 100,
        }
    }
    executor = FakeRemoteExecutor(
        stats=[_stat("12345", 120, 1780000001)],
        ranges=[_result(stdout="new incremental log", command="range")],
    )

    watcher = RemoteLogWatcher(
        log_files=[path],
        session=_session(executor),
        max_bytes_per_poll=200,
        remote_log_watermarks=watermarks,
    )

    chunks = watcher.poll()

    assert len(chunks) == 1
    assert "new incremental log" in chunks[0].content
    assert watermarks[path]["offset"] == 120
    assert watermarks[path]["last_strategy"] == "incremental"
    assert watermarks[path]["fallback_reason"] == ""
    assert executor.calls == [
        ("stat", path),
        ("range", path, 100, 20),
    ]


def test_remote_log_watcher_resets_on_copytruncate() -> None:
    path = "/tmp/service.log"
    watermarks = {
        path: {
            "inode": "12345",
            "size": 100,
            "mtime": 1780000000,
            "offset": 100,
        }
    }
    executor = FakeRemoteExecutor(
        stats=[_stat("12345", 20, 1780000001)],
        ranges=[_result(stdout="new file prefix", command="range")],
    )

    watcher = RemoteLogWatcher(
        log_files=[path],
        session=_session(executor),
        max_bytes_per_poll=200,
        remote_log_watermarks=watermarks,
    )

    chunks = watcher.poll()

    assert len(chunks) == 1
    assert "new file prefix" in chunks[0].content
    assert watermarks[path]["offset"] == 20
    assert watermarks[path]["last_strategy"] == "rotation_reset"
    assert watermarks[path]["fallback_reason"] == "size_decreased"
    assert executor.calls == [
        ("stat", path),
        ("range", path, 0, 20),
    ]


def test_remote_log_watcher_resets_on_inode_change() -> None:
    path = "/tmp/service.log"
    watermarks = {
        path: {
            "inode": "12345",
            "size": 100,
            "mtime": 1780000000,
            "offset": 100,
        }
    }
    executor = FakeRemoteExecutor(
        stats=[_stat("67890", 18, 1780000001)],
        ranges=[_result(stdout="rotated file prefix", command="range")],
    )

    watcher = RemoteLogWatcher(
        log_files=[path],
        session=_session(executor),
        max_bytes_per_poll=200,
        remote_log_watermarks=watermarks,
    )

    chunks = watcher.poll()

    assert len(chunks) == 1
    assert "rotated file prefix" in chunks[0].content
    assert watermarks[path]["inode"] == "67890"
    assert watermarks[path]["offset"] == 18
    assert watermarks[path]["last_strategy"] == "rotation_reset"
    assert watermarks[path]["fallback_reason"] == "inode_changed"
    assert executor.calls == [
        ("stat", path),
        ("range", path, 0, 18),
    ]


def test_remote_log_watcher_stat_failure_falls_back_without_advancing_watermark() -> None:
    path = "/tmp/service.log"
    watermarks = {
        path: {
            "inode": "12345",
            "size": 100,
            "mtime": 1780000000,
            "offset": 100,
        }
    }
    original = dict(watermarks[path])
    executor = FakeRemoteExecutor(
        stats=[_result(return_code=1, command="stat")],
        tails=[_result(stdout="tail fallback after stat failure", command="tail")],
    )

    watcher = RemoteLogWatcher(
        log_files=[path],
        session=_session(executor),
        remote_log_watermarks=watermarks,
    )

    chunks = watcher.poll()

    assert len(chunks) == 1
    assert "tail fallback after stat failure" in chunks[0].content
    assert watermarks[path] == original
    assert executor.calls == [
        ("stat", path),
        ("tail", path, 200),
    ]
    assert watcher.last_poll_metrics["remote_log_fallback_count"] == 1
    assert watcher.last_poll_metrics["remote_log_watermark_errors"] == [
        {
            "path": path,
            "reason": "stat_failed",
            "return_code": 1,
            "command": "stat",
        }
    ]


def test_remote_log_watcher_range_failure_falls_back_without_advancing_watermark() -> None:
    path = "/tmp/service.log"
    watermarks = {
        path: {
            "inode": "12345",
            "size": 100,
            "mtime": 1780000000,
            "offset": 100,
        }
    }
    original = dict(watermarks[path])
    executor = FakeRemoteExecutor(
        stats=[_stat("12345", 120, 1780000001)],
        ranges=[_result(return_code=1, command="range")],
        tails=[_result(stdout="tail fallback after range failure", command="tail")],
    )

    watcher = RemoteLogWatcher(
        log_files=[path],
        session=_session(executor),
        remote_log_watermarks=watermarks,
    )

    chunks = watcher.poll()

    assert len(chunks) == 1
    assert "tail fallback after range failure" in chunks[0].content
    assert watermarks[path] == original
    assert executor.calls == [
        ("stat", path),
        ("range", path, 100, 20),
        ("tail", path, 200),
    ]


def test_remote_log_watcher_large_delta_falls_back_and_records_skipped_bytes() -> None:
    path = "/tmp/service.log"
    watermarks = {
        path: {
            "inode": "12345",
            "size": 0,
            "mtime": 1780000000,
            "offset": 0,
        }
    }
    executor = FakeRemoteExecutor(
        stats=[_stat("12345", 1000, 1780000001)],
        tails=[_result(stdout="latest tail for large delta", command="tail")],
    )

    watcher = RemoteLogWatcher(
        log_files=[path],
        session=_session(executor),
        max_bytes_per_poll=100,
        remote_log_watermarks=watermarks,
    )

    chunks = watcher.poll()

    assert len(chunks) == 1
    assert "latest tail for large delta" in chunks[0].content
    assert watermarks[path]["offset"] == 1000
    assert watermarks[path]["last_strategy"] == "tail_fallback"
    assert watermarks[path]["fallback_reason"] == "delta_exceeds_max_bytes_per_poll"
    assert watermarks[path]["skipped_bytes"] == 1000
    assert executor.calls == [
        ("stat", path),
        ("tail", path, 200),
    ]
    assert watcher.last_poll_metrics["remote_log_strategy_counts"] == {
        "tail_fallback": 1
    }
    assert watcher.last_poll_metrics["remote_log_fallback_count"] == 1
    assert watcher.last_poll_metrics["remote_log_bytes_read"] == len(
        "latest tail for large delta".encode("utf-8")
    )
    assert watcher.last_poll_notices == [
        {
            "kind": "tail_fallback",
            "path": path,
            "reason": "delta_exceeds_max_bytes_per_poll",
            "strategy": "tail_fallback",
        }
    ]


def test_remote_log_watcher_shadow_mode_updates_watermark_but_outputs_tail() -> None:
    path = "/tmp/service.log"
    watermarks = {
        path: {
            "inode": "12345",
            "size": 100,
            "mtime": 1780000000,
            "offset": 100,
        }
    }
    executor = FakeRemoteExecutor(
        stats=[_stat("12345", 120, 1780000001)],
        ranges=[_result(stdout="range-only line", command="range")],
        tails=[_result(stdout="tail shadow output", command="tail")],
    )

    watcher = RemoteLogWatcher(
        log_files=[path],
        session=_session(executor),
        max_bytes_per_poll=200,
        remote_log_watermarks=watermarks,
        shadow_mode=True,
    )

    chunks = watcher.poll()

    assert len(chunks) == 1
    assert "tail shadow output" in chunks[0].content
    assert "range-only line" not in chunks[0].content
    assert watermarks[path]["offset"] == 120
    assert watermarks[path]["last_strategy"] == "incremental"
    assert executor.calls == [
        ("stat", path),
        ("range", path, 100, 20),
        ("tail", path, 200),
    ]
    assert watcher.last_poll_metrics["remote_log_strategy_counts"] == {
        "incremental": 1,
        "tail_shadow_output": 1,
    }


def test_remote_log_watcher_disabled_mode_uses_tail_without_watermark() -> None:
    path = "/tmp/service.log"
    watermarks: dict[str, dict] = {}
    executor = FakeRemoteExecutor(
        tails=[_result(stdout="tail disabled output", command="tail")],
    )

    watcher = RemoteLogWatcher(
        log_files=[path],
        session=_session(executor),
        remote_log_watermarks=watermarks,
        watermark_enabled=False,
    )

    chunks = watcher.poll()

    assert len(chunks) == 1
    assert "tail disabled output" in chunks[0].content
    assert watermarks == {}
    assert executor.calls == [
        ("tail", path, 200),
    ]
    assert watcher.last_poll_metrics["remote_log_strategy_counts"] == {
        "tail_disabled": 1
    }
