#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="/home/lf/projects/agentic-linux-troubleshooter"
RUNTIME_LOG="/home/lf/runtime_projects/enterprise_order_monitoring_service/outputs/service.log"

info() { echo "[INFO] $*"; }
pass() { echo "[PASS] $*"; }
fail() { echo "[FAIL] $*" >&2; exit 1; }

[[ "$(pwd)" == "${PROJECT_ROOT}" ]] || fail "Please run from ${PROJECT_ROOT}"
mkdir -p "$(dirname "${RUNTIME_LOG}")"

info "Appending network_port error to ${RUNTIME_LOG}"
printf '%s\n' \
  '' \
  '[stage6e-acceptance][network_port] Traceback (most recent call last):' \
  '  File "/srv/order-service/run_service.py", line 132, in start_metrics_exporter' \
  '    server_socket.bind(("127.0.0.1", 9100))' \
  'OSError: [Errno 98] Address already in use' \
  '[summary] primary_failure=Address already in use metrics port conflict' \
  >> "${RUNTIME_LOG}"

pass "network_port error appended"
echo "[INFO] Wait 30-60 seconds, then run:"
echo "  scripts/stage6e_acceptance_check.sh"
