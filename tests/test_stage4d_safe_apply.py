from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from sessions import TroubleshootingSession

pytestmark = pytest.mark.integration


ENTERPRISE_PROJECT = PROJECT_ROOT / "examples/live_projects/enterprise_order_monitoring_service"
ENTERPRISE_CONFIG = ENTERPRISE_PROJECT / "config.json"


def set_enterprise_port(port: int) -> None:
    data = json.loads(ENTERPRISE_CONFIG.read_text(encoding="utf-8"))
    data["metrics_port"] = port
    data["simulate_disk_full"] = True
    data["simulate_python_env_mismatch"] = True
    ENTERPRISE_CONFIG.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def test_stage4d_enterprise_safe_apply() -> None:
    set_enterprise_port(9100)

    session = TroubleshootingSession(
        agent_depth="balanced",
        report_mode="rule",
        project_dir=str(ENTERPRISE_PROJECT),
        run_command="python run_service.py --config config.json",
        rerun_timeout=60,
    )

    first = session.rerun_project()
    print("=" * 100)
    print("FIRST RERUN")
    print("=" * 100)
    print(first)
    assert "重新运行结果：失败" in first

    fix_plan = session.generate_fix_plan()
    print("=" * 100)
    print("FIX PLAN")
    print("=" * 100)
    print(fix_plan)
    assert "fix-network-1" in fix_plan

    apply_result = session.apply_fix("fix-network-1")
    print("=" * 100)
    print("APPLY RESULT")
    print("=" * 100)
    print(apply_result)
    assert "success: `True`" in apply_result

    data = json.loads(ENTERPRISE_CONFIG.read_text(encoding="utf-8"))
    assert data["metrics_port"] == 9101

    diff_text = session.show_latest_diff()
    print("=" * 100)
    print("DIFF")
    print("=" * 100)
    print(diff_text)
    assert "9100" in diff_text
    assert "9101" in diff_text

    second = session.rerun_project()
    print("=" * 100)
    print("SECOND RERUN")
    print("=" * 100)
    print(second)
    assert "重新运行结果：成功" in second

    # rollback，方便下次继续演示
    rollback_result = session.rollback_latest_apply()
    print("=" * 100)
    print("ROLLBACK")
    print("=" * 100)
    print(rollback_result)

    data = json.loads(ENTERPRISE_CONFIG.read_text(encoding="utf-8"))
    assert data["metrics_port"] == 9100


def main() -> None:
    test_stage4d_enterprise_safe_apply()
    print("=" * 100)
    print("STAGE 4D SAFE APPLY TEST PASSED")
    print("=" * 100)


if __name__ == "__main__":
    main()
