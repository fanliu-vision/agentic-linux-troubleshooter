import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from routers import classify_issue_dict, format_route_context
from tools.log_tool import diagnose_mixed_log_file


REGRESSION_CASES = [
    {
        "file": "examples/logs/regression/01_gpu_hip_oom.log",
        "expected_primary": "gpu",
    },
    {
        "file": "examples/logs/regression/02_cuda_oom.log",
        "expected_primary": "gpu",
    },
    {
        "file": "examples/logs/regression/03_disk_full.log",
        "expected_primary": "disk",
    },
    {
        "file": "examples/logs/regression/04_port_conflict.log",
        "expected_primary": "network_port",
    },
    {
        "file": "examples/logs/regression/05_python_env_mismatch.log",
        "expected_primary": "python_env",
    },
    {
        "file": "examples/logs/regression/06_slurm_pending_resources.log",
        "expected_primary": "slurm",
    },
    {
        "file": "examples/logs/regression/07_slurm_node_down_drain.log",
        "expected_primary": "slurm",
    },
    {
        "file": "examples/logs/regression/08_complex_mixed_failure.log",
        "expected_primary": "gpu",
    },
]


def extract_field(text: str, field_name: str) -> str:
    prefix = f"{field_name}:"
    for line in text.splitlines():
        if line.startswith(prefix):
            return line.split(":", 1)[1].strip()
    return ""


def main():
    passed = 0
    failed = 0

    for case in REGRESSION_CASES:
        file_path = case["file"]
        expected_primary = case["expected_primary"]
        question = f"帮我分析 {file_path}"

        print("=" * 100)
        print(f"CASE: {file_path}")
        print(f"EXPECTED PRIMARY: {expected_primary}")
        print("=" * 100)

        route = classify_issue_dict(question)
        print("[ROUTE]")
        print(format_route_context(route))

        route_primary = route.get("primary_issue_type")

        diagnosis = diagnose_mixed_log_file(file_path)
        print("\n[DIAGNOSIS]")
        print(diagnosis)

        diagnosis_primary = extract_field(diagnosis, "primary_issue_type")

        route_ok = route_primary == expected_primary
        diagnosis_ok = diagnosis_primary == expected_primary

        if route_ok and diagnosis_ok:
            print("\nRESULT: PASS")
            passed += 1
        else:
            print("\nRESULT: FAIL")
            print(f"route_primary={route_primary}, diagnosis_primary={diagnosis_primary}")
            failed += 1

    print("=" * 100)
    print(f"SUMMARY: passed={passed}, failed={failed}")
    print("=" * 100)

    if failed > 0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()