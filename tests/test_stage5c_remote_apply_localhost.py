from __future__ import annotations

import getpass
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from sessions import TroubleshootingSession

pytestmark = pytest.mark.integration


REMOTE_PROJECT = Path("/tmp/agent_stage5c_remote_apply_demo")
CONFIG_PATH = REMOTE_PROJECT / "config.json"
RUN_SCRIPT = REMOTE_PROJECT / "run_service.py"


def check_localhost_ssh() -> bool:
    user = getpass.getuser()
    target = f"{user}@localhost"

    result = subprocess.run(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=5",
            target,
            "echo STAGE5C_SSH_OK",
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    if result.returncode == 0 and "STAGE5C_SSH_OK" in result.stdout:
        return True

    print("SSH localhost 不可用，请先配置免密 SSH。")
    print(result.stderr)
    return False


def write_demo_project(port: int = 9100) -> None:
    if REMOTE_PROJECT.exists():
        shutil.rmtree(REMOTE_PROJECT)

    REMOTE_PROJECT.mkdir(parents=True, exist_ok=True)

    config = {
        "service_name": "stage5c-remote-order-service",
        "metrics_host": "127.0.0.1",
        "metrics_port": port,
        "simulate_port_conflict": True,
        "output_dir": "outputs",
    }

    CONFIG_PATH.write_text(
        json.dumps(config, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    RUN_SCRIPT.write_text(
        r'''
from __future__ import annotations

import argparse
import json
import socket
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.json")
    args = parser.parse_args()

    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    host = config.get("metrics_host", "127.0.0.1")
    port = int(config.get("metrics_port", 9100))

    print(f"[service] starting {config.get('service_name')}")
    print(f"[service] metrics_port={port}")

    conflict_socket = None

    if config.get("simulate_port_conflict", False) and port == 9100:
        conflict_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        conflict_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        conflict_socket.bind((host, port))
        conflict_socket.listen(1)

    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    try:
        server_socket.bind((host, port))
        server_socket.listen(1)
        print(f"[metrics] exporter started successfully on {host}:{port}")
        print("[service] health=OK")
        return 0
    except OSError as exc:
        print("Traceback (most recent call last):", file=sys.stderr)
        print(f"OSError: [Errno {exc.errno}] {exc.strerror}", file=sys.stderr)
        print("[summary]", file=sys.stderr)
        print("primary_failure=Address already in use", file=sys.stderr)
        return 1
    finally:
        server_socket.close()
        if conflict_socket is not None:
            conflict_socket.close()


if __name__ == "__main__":
    raise SystemExit(main())
'''.strip()
        + "\n",
        encoding="utf-8",
    )


def test_stage5c_remote_apply_localhost() -> None:
    if not check_localhost_ssh():
        print("STAGE 5C TEST SKIPPED")
        return

    user = getpass.getuser()
    write_demo_project(port=9100)

    session = TroubleshootingSession(
        agent_depth="balanced",
        report_mode="rule",
        run_command="python3 run_service.py --config config.json",
        rerun_timeout=60,
    )

    session.set_remote_profile(
        user=user,
        host="localhost",
        port=22,
    )

    first = session.rerun_remote_project(str(REMOTE_PROJECT))
    print("=" * 100)
    print("FIRST REMOTE RERUN")
    print("=" * 100)
    print(first)

    assert session.latest_remote_rerun_success is False
    assert session.latest_remote_rerun_return_code != 0
    assert session.route.get("primary_issue_type") == "network_port"

    apply_result = session.remote_apply_fix(
        fix_id="fix-network-1",
        remote_project_dir=str(REMOTE_PROJECT),
    )
    print("=" * 100)
    print("REMOTE APPLY")
    print("=" * 100)
    print(apply_result)

    assert session.latest_remote_apply_success is True
    assert session.latest_remote_apply_fix_id == "fix-network-1"

    data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    assert data["metrics_port"] == 9101

    diff = session.show_latest_remote_diff()
    print("=" * 100)
    print("REMOTE DIFF")
    print("=" * 100)
    print(diff)

    assert "9100" in diff
    assert "9101" in diff

    second = session.rerun_remote_project(str(REMOTE_PROJECT))
    print("=" * 100)
    print("SECOND REMOTE RERUN")
    print("=" * 100)
    print(second)

    assert session.latest_remote_rerun_success is True
    assert session.latest_remote_rerun_return_code == 0

    report, save_path, source = session.generate_report()
    print("=" * 100)
    print("REPORT")
    print("=" * 100)
    print(f"source={source}")
    print(f"save_path={save_path}")
    print(report)

    assert "多 Agent Linux 排障报告" in report
    assert "远程" in report or "remote" in report.lower()

    rollback = session.remote_rollback_latest_apply()
    print("=" * 100)
    print("REMOTE ROLLBACK")
    print("=" * 100)
    print(rollback)

    data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    assert data["metrics_port"] == 9100

    print("=" * 100)
    print("STAGE 5C REMOTE APPLY LOCALHOST TEST PASSED")
    print("=" * 100)


def main() -> None:
    test_stage5c_remote_apply_localhost()


if __name__ == "__main__":
    main()
