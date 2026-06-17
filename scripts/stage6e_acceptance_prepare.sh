#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="/home/lf/projects/agentic-linux-troubleshooter"
PROJECT_ID="enterprise_demo_local"
SERVICE_NAME="agentic-monitor@${PROJECT_ID}.service"
PYTHON_BIN="${PROJECT_ROOT}/.venv/bin/python"
RUNTIME_CONFIG="/home/lf/runtime_projects/enterprise_order_monitoring_service/config.json"
RUNTIME_LOG="/home/lf/runtime_projects/enterprise_order_monitoring_service/outputs/service.log"

info() { echo "[INFO] $*"; }
pass() { echo "[PASS] $*"; }
fail() { echo "[FAIL] $*" >&2; exit 1; }

[[ "$(pwd)" == "${PROJECT_ROOT}" ]] || fail "Please run from ${PROJECT_ROOT}"
[[ -x "${PYTHON_BIN}" ]] || fail "Python binary not found: ${PYTHON_BIN}"

if ! mapfile -t service_state < <(systemctl show "${SERVICE_NAME}" -p ActiveState -p SubState --value); then
  fail "Unable to read systemd state for ${SERVICE_NAME}"
fi

active_state="${service_state[0]:-unknown}"
sub_state="${service_state[1]:-unknown}"
info "systemd state: ActiveState=${active_state}, SubState=${sub_state}"

if [[ "${active_state}" == "active" || "${sub_state}" == "running" ]]; then
  echo "[FAIL] ${SERVICE_NAME} is still running."
  echo "Please run manually:"
  echo "  sudo systemctl stop ${SERVICE_NAME}"
  exit 1
fi

info "Cleaning Stage 6E-2 acceptance state and outputs"
rm -rf "state/${PROJECT_ID}"
rm -rf "outputs/monitors/${PROJECT_ID}"
rm -f "outputs/alerts/${PROJECT_ID}_alerts.jsonl"
rm -f "outputs/alerts/${PROJECT_ID}_latest_alert.md"
rm -rf "outputs/alerts/${PROJECT_ID}_alerts"
mkdir -p "outputs/alerts"
pass "Project acceptance outputs cleaned"

[[ -f "${RUNTIME_CONFIG}" ]] || fail "Runtime config not found: ${RUNTIME_CONFIG}"
info "Resetting runtime config: metrics_port=9100, batch_size=128"
"${PYTHON_BIN}" - "${RUNTIME_CONFIG}" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
data = json.loads(path.read_text(encoding="utf-8"))
data["metrics_port"] = 9100
data["batch_size"] = 128
path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
pass "Runtime config reset"

mkdir -p "$(dirname "${RUNTIME_LOG}")"
: > "${RUNTIME_LOG}"
pass "Runtime log cleared: ${RUNTIME_LOG}"

echo ""
echo "[INFO] Prepare completed."
echo "Please run manually:"
echo "  sudo systemctl restart ${SERVICE_NAME}"
