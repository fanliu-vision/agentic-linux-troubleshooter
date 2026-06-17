from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from sessions import TroubleshootingSession

pytestmark = pytest.mark.integration


LIVE_PROJECT = PROJECT_ROOT / "examples/live_projects/enterprise_order_monitoring_service"
CONFIG_PATH = LIVE_PROJECT / "config.json"


def write_metrics_port(port: int) -> None:
    data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    data["metrics_port"] = port
    CONFIG_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def test_stage4c_enterprise_service() -> None:
    # 恢复到初始失败状态
    write_metrics_port(9100)

    session = TroubleshootingSession(
        agent_depth="balanced",
        report_mode="rule",
        project_dir=str(LIVE_PROJECT),
        run_command="python run_service.py --config config.json",
        rerun_timeout=60,
    )

    first_result = session.rerun_project()
    print("=" * 100)
    print("FIRST RERUN RESULT")
    print("=" * 100)
    print(first_result)

    assert "重新运行结果：失败" in first_result

    # 主问题应为端口/网络类
    assert session.route.get("primary_issue_type") == "network_port"

    fix_plan = session.generate_fix_plan()
    print("=" * 100)
    print("FIX PLAN")
    print("=" * 100)
    print(fix_plan)

    assert "更换冲突端口" in fix_plan

    # 模拟用户根据修复计划手动修复
    write_metrics_port(9101)

    second_result = session.rerun_project()
    print("=" * 100)
    print("SECOND RERUN RESULT")
    print("=" * 100)
    print(second_result)

    assert "重新运行结果：成功" in second_result

    # 测试结束后恢复初始失败状态，方便下次演示
    write_metrics_port(9100)


def main() -> None:
    test_stage4c_enterprise_service()
    print("=" * 100)
    print("STAGE 4C ENTERPRISE SERVICE TEST PASSED")
    print("=" * 100)


if __name__ == "__main__":
    main()
