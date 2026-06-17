#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="/home/lf/projects/agentic-linux-troubleshooter"
PYTHON_BIN="${PROJECT_ROOT}/.venv/bin/python"
RUNTIME_CONFIG="/home/lf/runtime_projects/enterprise_order_monitoring_service/config.json"
RUNTIME_LOG="/home/lf/runtime_projects/enterprise_order_monitoring_service/outputs/service.log"

info() { echo "[INFO] $*"; }
pass() { echo "[PASS] $*"; }
fail() { echo "[FAIL] $*" >&2; exit 1; }

[[ "$(pwd)" == "${PROJECT_ROOT}" ]] || fail "Please run from ${PROJECT_ROOT}"
[[ -x "${PYTHON_BIN}" ]] || fail "Python binary not found: ${PYTHON_BIN}"
[[ -f "${RUNTIME_CONFIG}" ]] || fail "Runtime config not found: ${RUNTIME_CONFIG}"
mkdir -p "$(dirname "${RUNTIME_LOG}")"

info "Resetting runtime batch_size to 128 before gpu_oom injection"
"${PYTHON_BIN}" - "${RUNTIME_CONFIG}" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
data = json.loads(path.read_text(encoding="utf-8"))
data["batch_size"] = 128
path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
pass "batch_size reset to 128"

info "Appending gpu_oom error to ${RUNTIME_LOG}"
printf '%s\n' \
  '' \
  '[stage6e-acceptance][gpu_oom] Traceback (most recent call last):' \
  '  File "/srv/order-service/train.py", line 88, in run_batch' \
  '    loss.backward()' \
  'RuntimeError: CUDA out of memory. Tried to allocate 2.00 GiB. GPU 0 has insufficient free memory.' \
  '[summary] primary_failure=CUDA out of memory batch_size too large' \
  >> "${RUNTIME_LOG}"

pass "gpu_oom error appended"
echo "[INFO] Wait 30-60 seconds, then run:"
echo "  scripts/stage6e_acceptance_check.sh"
