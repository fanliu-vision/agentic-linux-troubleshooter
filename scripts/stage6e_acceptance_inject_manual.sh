#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="/home/lf/projects/agentic-linux-troubleshooter"
RUNTIME_LOG="/home/lf/runtime_projects/enterprise_order_monitoring_service/outputs/service.log"

info() { echo "[INFO] $*"; }
pass() { echo "[PASS] $*"; }
fail() { echo "[FAIL] $*" >&2; exit 1; }

[[ "$(pwd)" == "${PROJECT_ROOT}" ]] || fail "Please run from ${PROJECT_ROOT}"
mkdir -p "$(dirname "${RUNTIME_LOG}")"

info "Appending disk_full and python_env errors to ${RUNTIME_LOG}"
printf '%s\n' \
  '' \
  '[stage6e-acceptance][disk_full] OSError: [Errno 28] No space left on device: /tmp/acme_order_cache/batch.tmp' \
  '[summary] secondary_failure=No space left on device disk cache full' \
  '' \
  '[stage6e-acceptance][python_env] Traceback (most recent call last):' \
  '  File "/srv/order-service/run_service.py", line 21, in <module>' \
  '    import acme_internal_sdk' \
  "ModuleNotFoundError: No module named 'acme_internal_sdk'" \
  'Python interpreter and pip path do not belong to the same environment' \
  '[summary] secondary_failure=python dependency missing and interpreter mismatch' \
  >> "${RUNTIME_LOG}"

pass "manual escalation errors appended"
echo "[INFO] Wait 30-60 seconds, then run:"
echo "  scripts/stage6e_acceptance_check.sh"
