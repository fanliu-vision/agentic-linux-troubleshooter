from __future__ import annotations

from types import SimpleNamespace

from detectors.error_event_detector import ErrorEventDetector
from tools import remote_ssh_executor
from tools.remote_ssh_executor import RemoteReadonlySSHExecutor, RemoteSSHProfile


def _profile() -> RemoteSSHProfile:
    return RemoteSSHProfile(user="lf", host="localhost", port=22)


def _patch_ssh_stdout(monkeypatch, stdout: str) -> None:
    def fake_run(*args, **kwargs):
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr(remote_ssh_executor.subprocess, "run", fake_run)


def _long_combined_tail_output() -> str:
    filler = [
        f"[filler:{idx:03d}] normal heartbeat line service healthy queue nominal "
        + ("x" * 120)
        for idx in range(1, 80)
    ]
    faults = [
        "[event_type=process_crash] systemd[1]: r10-combined-worker.service: "
        "Main process exited, code=dumped, status=11/SEGV",
        "[event_type=process_crash] systemd[1]: r10-combined-worker.service: "
        "Failed with result 'core-dump'",
        "[event_type=container_k8s] Warning BackOff pod/r10-combined-api "
        "Back-off restarting failed container r10-combined-api",
        "[event_type=container_k8s] Warning Failed pod/r10-combined-api "
        "Error: ImagePullBackOff",
        "[event_type=container_k8s] Last State: Terminated Reason: OOMKilled",
        "[event_type=container_k8s] Warning Failed pod/r10-combined-api "
        "CreateContainerConfigError",
    ]
    return "\n".join(filler + faults)


def test_regular_remote_command_keeps_existing_head_truncation(monkeypatch):
    output = "HEAD_MARKER " + ("x" * 200) + " TAIL_MARKER"
    _patch_ssh_stdout(monkeypatch, output)

    executor = RemoteReadonlySSHExecutor(max_output_chars=80)
    result = executor.run(_profile(), "hostname")

    assert result.return_code == 0
    assert result.stdout.startswith("HEAD_MARKER")
    assert "TAIL_MARKER" not in result.stdout
    assert result.stdout.endswith("[REMOTE_OUTPUT_TRUNCATED]")
    assert "REMOTE_OUTPUT_TRUNCATED_KEEP_TAIL" not in result.stdout


def test_remote_log_tail_truncation_preserves_tail(monkeypatch):
    output = _long_combined_tail_output()
    _patch_ssh_stdout(monkeypatch, output)

    executor = RemoteReadonlySSHExecutor(max_output_chars=900)
    result = executor.read_remote_log_tail(
        _profile(),
        remote_path="/tmp/service.log",
        lines=200,
    )

    assert result.return_code == 0
    assert result.stdout.startswith("[REMOTE_OUTPUT_TRUNCATED_KEEP_TAIL omitted ")
    assert "[filler:001]" not in result.stdout
    assert "status=11/SEGV" in result.stdout
    assert "core-dump" in result.stdout
    assert "OOMKilled" in result.stdout
    assert "ImagePullBackOff" in result.stdout


def test_remote_log_tail_truncation_marker_is_present(monkeypatch):
    output = _long_combined_tail_output()
    _patch_ssh_stdout(monkeypatch, output)

    executor = RemoteReadonlySSHExecutor(max_output_chars=900)
    result = executor.read_remote_log_tail(
        _profile(),
        remote_path="/tmp/service.log",
        lines=200,
    )

    assert "[REMOTE_OUTPUT_TRUNCATED_KEEP_TAIL omitted " in result.stdout
    assert "[REMOTE_OUTPUT_TRUNCATED]" not in result.stdout


def test_detect_all_identifies_faults_after_log_tail_truncation(monkeypatch):
    output = _long_combined_tail_output()
    _patch_ssh_stdout(monkeypatch, output)

    executor = RemoteReadonlySSHExecutor(max_output_chars=900)
    result = executor.read_remote_log_tail(
        _profile(),
        remote_path="/tmp/service.log",
        lines=200,
    )

    events = ErrorEventDetector().detect_all(
        result.to_evidence_text(),
        source="remote_log:/tmp/service.log",
    )
    event_types = [event.event_type for event in events]

    assert "process_crash" in event_types
    assert "container_k8s" in event_types


def test_stat_remote_log_uses_fixed_readonly_command(monkeypatch):
    calls = []

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout="12345 1048576 1780000000\n", stderr="")

    monkeypatch.setattr(remote_ssh_executor.subprocess, "run", fake_run)

    executor = RemoteReadonlySSHExecutor()
    result = executor.stat_remote_log(
        _profile(),
        remote_path="/tmp/service.log",
    )

    assert result.allowed is True
    assert result.return_code == 0
    assert result.stdout == "12345 1048576 1780000000"
    assert result.command == "stat -Lc '%i %s %Y' /tmp/service.log"
    assert calls[0][1]["text"] is True
    assert calls[0][0][0][-1] == "stat -Lc '%i %s %Y' /tmp/service.log"


def test_read_remote_log_range_uses_raw_fixed_readonly_command(monkeypatch):
    calls = []
    raw_stdout = b"\nnew bytes\nwith trailing spaces  "

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout=raw_stdout, stderr=b"")

    monkeypatch.setattr(remote_ssh_executor.subprocess, "run", fake_run)

    executor = RemoteReadonlySSHExecutor(max_output_chars=5)
    result = executor.read_remote_log_range(
        _profile(),
        remote_path="/tmp/service.log",
        offset=5,
        max_bytes=31,
    )

    assert result.allowed is True
    assert result.return_code == 0
    assert result.stdout == raw_stdout.decode("utf-8", errors="ignore")
    assert result.command == "tail -c +6 /tmp/service.log | head -c 31"
    assert calls[0][1]["text"] is False
    assert calls[0][0][0][-1] == "tail -c +6 /tmp/service.log | head -c 31"


def test_read_remote_log_range_decodes_invalid_bytes_with_ignore(monkeypatch):
    def fake_run(*args, **kwargs):
        return SimpleNamespace(returncode=0, stdout=b"ok\xff\n", stderr=b"")

    monkeypatch.setattr(remote_ssh_executor.subprocess, "run", fake_run)

    executor = RemoteReadonlySSHExecutor()
    result = executor.read_remote_log_range(
        _profile(),
        remote_path="/tmp/service.log",
        offset=0,
        max_bytes=4,
    )

    assert result.stdout == "ok\n"


def test_remote_log_range_pipe_is_not_allowed_through_generic_run(monkeypatch):
    def fake_run(*args, **kwargs):
        return SimpleNamespace(returncode=0, stdout=b"incremental", stderr=b"")

    monkeypatch.setattr(remote_ssh_executor.subprocess, "run", fake_run)

    executor = RemoteReadonlySSHExecutor()

    generic = executor.run(
        _profile(),
        "tail -c +1 /tmp/service.log | head -c 10",
    )
    fixed = executor.read_remote_log_range(
        _profile(),
        remote_path="/tmp/service.log",
        offset=0,
        max_bytes=10,
    )

    assert generic.allowed is False
    assert fixed.allowed is True


def test_remote_log_stat_and_range_reject_unsafe_path():
    executor = RemoteReadonlySSHExecutor()

    stat = executor.stat_remote_log(
        _profile(),
        remote_path="/tmp/service.log;rm",
    )
    ranged = executor.read_remote_log_range(
        _profile(),
        remote_path="/tmp/service.log|cat",
        offset=0,
        max_bytes=10,
    )

    assert stat.allowed is False
    assert ranged.allowed is False
