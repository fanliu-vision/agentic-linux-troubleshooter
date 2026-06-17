from __future__ import annotations

import json
import shutil
import getpass
from pathlib import Path
import subprocess
import sys

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from sessions import TroubleshootingSession

pytestmark = pytest.mark.integration

# 模拟远程项目路径
REMOTE_PROJECT = Path("/tmp/agent_stage5b_remote_demo")
CONFIG_PATH = REMOTE_PROJECT / "config.json"
RUN_SCRIPT = REMOTE_PROJECT / "run_service.py"

def check_localhost_ssh() -> bool:
    """确认 localhost SSH 可用"""
    user = getpass.getuser()
    target = f"{user}@localhost"
    result = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", target, "echo STAGE5B_SSH_OK"],
        capture_output=True, text=True, timeout=10, check=False
    )
    if result.returncode == 0 and "STAGE5B_SSH_OK" in result.stdout:
        return True
    print("SSH localhost 不可用，请先配置 SSH 免密登录。")
    return False

def write_demo_project(port: int = 9100) -> None:
    """创建远程项目模拟目录和配置"""
    if REMOTE_PROJECT.exists():
        shutil.rmtree(REMOTE_PROJECT)
    REMOTE_PROJECT.mkdir(parents=True, exist_ok=True)

    config = {
        "service_name": "stage5b-remote-order-service",
        "metrics_host": "127.0.0.1",
        "metrics_port": port,
        "simulate_port_conflict": True,
        "output_dir": "outputs"
    }
    CONFIG_PATH.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")

    RUN_SCRIPT.write_text(r"""
from __future__ import annotations
import argparse, json, socket, sys
from pathlib import Path

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.json")
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    host = config.get("metrics_host", "127.0.0.1")
    port = int(config.get("metrics_port", 9100))
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
        return 0
    except OSError as exc:
        print("Traceback (most recent call last):", file=sys.stderr)
        print(f"OSError: [Errno {exc.errno}] {exc.strerror}", file=sys.stderr)
        return 1
    finally:
        server_socket.close()
        if conflict_socket is not None:
            conflict_socket.close()

if __name__ == "__main__":
    raise SystemExit(main())
""".strip() + "\n", encoding="utf-8")

def set_metrics_port(port: int) -> None:
    data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    data["metrics_port"] = port
    CONFIG_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

def test_stage5b_remote_rerun_localhost() -> None:
    if not check_localhost_ssh():
        print("STAGE 5B TEST SKIPPED")
        return

    user = getpass.getuser()
    # 初始化失败状态
    write_demo_project(port=9100)

    session = TroubleshootingSession(
        agent_depth="balanced",
        report_mode="rule",
        run_command="python3 run_service.py --config config.json",
        rerun_timeout=60
    )

    session.set_remote_profile(user=user, host="localhost", port=22)

    # 第一次 rerun（应失败）
    first = session.rerun_remote_project(str(REMOTE_PROJECT))
    print("="*80, "\nFIRST REMOTE RERUN\n", "="*80)
    print(first)
    assert session.latest_remote_rerun_success is False
    assert session.latest_remote_rerun_return_code != 0
    assert session.route.get("primary_issue_type") == "network_port"

    # 生成修复计划
    fix_plan = session.generate_fix_plan()
    print("="*80, "\nFIX PLAN\n", "="*80)
    print(fix_plan)
    assert "network" in fix_plan.lower()

    # 模拟用户手动修改远程配置
    set_metrics_port(9101)

    # 第二次 rerun（应成功）
    second = session.rerun_remote_project(str(REMOTE_PROJECT))
    print("="*80, "\nSECOND REMOTE RERUN\n", "="*80)
    print(second)
    assert session.latest_remote_rerun_success is True
    assert session.latest_remote_rerun_return_code == 0

    # 生成报告
    report, save_path, source = session.generate_report()
    print("="*80, "\nREPORT\n", "="*80)
    print(f"source={source}")
    print(f"save_path={save_path}")
    print(report)

    assert "多 Agent Linux 排障报告" in report
    assert "远程" in report or "remote" in report.lower()
    assert session.latest_remote_rerun_success is True

    print("="*80)
    print("STAGE 5B REMOTE RERUN LOCALHOST TEST PASSED")
    print("="*80)

def main() -> None:
    test_stage5b_remote_rerun_localhost()

if __name__ == "__main__":
    main()
