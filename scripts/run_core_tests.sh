#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PYTHON_BIN="${PROJECT_ROOT}/.venv/bin/python"

cd "${PROJECT_ROOT}"

"${PYTHON_BIN}" -m py_compile main_monitor.py
"${PYTHON_BIN}" -m py_compile monitors/monitor_loop.py
"${PYTHON_BIN}" -m py_compile notifiers/file_notifier.py
"${PYTHON_BIN}" -m py_compile notifiers/notification_manager.py
"${PYTHON_BIN}" -m py_compile recovery/auto_recovery_runner.py
"${PYTHON_BIN}" -m py_compile safe_recovery/registry_governance.py
"${PYTHON_BIN}" -m py_compile monitors/cycle_summary_reporter.py

"${PYTHON_BIN}" -m pytest tests -q

echo "CORE TEST BASELINE PASSED"
