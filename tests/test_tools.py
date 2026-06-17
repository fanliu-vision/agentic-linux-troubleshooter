import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from routers import classify_issue_dict, format_route_context
from tools.log_tool import (
    analyze_log_text,
    read_log_file,
    diagnose_log_file,
    diagnose_mixed_log_file,
)
from tools.disk_tool import check_disk_usage
from tools.gpu_tool import check_gpu_status
from tools.shell_tool import run_shell_command
from tools.slurm_tool import diagnose_slurm_text, check_slurm_queue, check_slurm_nodes
from tools.python_env_tool import (
    diagnose_python_error_text,
    check_python_environment,
    check_python_package,
)


def test_router():
    cases = [
        "ssh 登录时报 No space left on device，帮我定位",
        "我的训练任务报 CUDA out of memory，应该怎么查",
        "9100 端口不通，帮我排查",
        "我的 Slurm 作业一直 PD，不运行",
        "运行时报 ModuleNotFoundError: No module named 'yaml'",
        "帮我分析 examples/logs/complex_mixed_linux_failure.log",
    ]

    for case in cases:
        print("=" * 80)
        print(f"TEST ROUTER: {case}")
        print("=" * 80)
        route = classify_issue_dict(case)
        print(format_route_context(route))


def main():
    print("=" * 80)
    print("TEST: router")
    print("=" * 80)
    test_router()

    print("=" * 80)
    print("TEST: diagnose_mixed_log_file")
    print("=" * 80)
    print(diagnose_mixed_log_file("examples/logs/complex_mixed_linux_failure.log"))

    print("=" * 80)
    print("TEST: diagnose_log_file")
    print("=" * 80)
    print(diagnose_log_file("examples/logs/oom_example.log"))

    print("=" * 80)
    print("TEST: check_disk_usage")
    print("=" * 80)
    print(check_disk_usage("~"))

    print("=" * 80)
    print("TEST: check_gpu_status")
    print("=" * 80)
    print(check_gpu_status())

    print("=" * 80)
    print("TEST: diagnose_slurm_text")
    print("=" * 80)
    print(
        diagnose_slurm_text(
            """
JOBID PARTITION     NAME     USER ST       TIME  NODES NODELIST(REASON)
12345 gpu           train    lf   PD       0:00      1 (Resources)
12346 gpu           test     lf   PD       0:00      1 (ReqNodeNotAvail)
"""
        )
    )

    print("=" * 80)
    print("TEST: diagnose_python_error_text")
    print("=" * 80)
    print(
        diagnose_python_error_text(
            "ModuleNotFoundError: No module named 'yaml'"
        )
    )

    print("=" * 80)
    print("TEST: check_python_environment")
    print("=" * 80)
    print(check_python_environment())

    print("=" * 80)
    print("TEST: check_python_package")
    print("=" * 80)
    print(check_python_package("yaml"))

    print("=" * 80)
    print("TEST: run_shell_command")
    print("=" * 80)
    print(run_shell_command("df -h"))


if __name__ == "__main__":
    main()