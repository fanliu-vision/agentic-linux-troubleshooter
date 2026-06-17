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


def reset_enterprise_config() -> None:
    data = json.loads(ENTERPRISE_CONFIG.read_text(encoding="utf-8"))
    data["metrics_port"] = 9100
    data["simulate_disk_full"] = True
    data["simulate_python_env_mismatch"] = True
    ENTERPRISE_CONFIG.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def test_stage4e_project_context() -> None:
    reset_enterprise_config()

    session = TroubleshootingSession(
        agent_depth="balanced",
        report_mode="rule",
        project_dir=str(ENTERPRISE_PROJECT),
        run_command="python run_service.py --config config.json",
        rerun_timeout=60,
    )

    context_text = session.collect_project_context()
    print("=" * 100)
    print("PROJECT CONTEXT")
    print("=" * 100)
    print(context_text)

    assert "config.json" in context_text
    assert "metrics_port=9100" in context_text
    assert "simulate_disk_full=True" in context_text

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

    assert "config.json" in fix_plan
    assert "metrics_port=9100" in fix_plan
    assert "9101" in fix_plan

    apply_result = session.apply_fix("fix-network-1")
    print("=" * 100)
    print("APPLY")
    print("=" * 100)
    print(apply_result)

    assert "success: `True`" in apply_result

    second = session.rerun_project()
    print("=" * 100)
    print("SECOND RERUN")
    print("=" * 100)
    print(second)

    assert "重新运行结果：成功" in second

    rollback = session.rollback_latest_apply()
    print("=" * 100)
    print("ROLLBACK")
    print("=" * 100)
    print(rollback)


def main() -> None:
    test_stage4e_project_context()
    print("=" * 100)
    print("STAGE 4E PROJECT CONTEXT TEST PASSED")
    print("=" * 100)


if __name__ == "__main__":
    main()
