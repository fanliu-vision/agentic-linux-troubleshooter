#!/usr/bin/env bash
set -euo pipefail

RUN_USER="${1:-${USER}}"
RUN_GROUP="${2:-${RUN_USER}}"

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-${PROJECT_ROOT}/.venv/bin/python}"
CONFIG_PATH="${CONFIG_PATH:-${PROJECT_ROOT}/configs/projects.yaml}"
STATE_DIR="${STATE_DIR:-${PROJECT_ROOT}/state}"
OUTPUT_ROOT="${OUTPUT_ROOT:-${PROJECT_ROOT}/outputs/monitors}"
TRACE_UI_HOST="${TRACE_UI_HOST:-127.0.0.1}"
TRACE_UI_PORT="${TRACE_UI_PORT:-8765}"
SESSION_TTL_SECONDS="${SESSION_TTL_SECONDS:-28800}"
WORKER_POLL_INTERVAL_SECONDS="${WORKER_POLL_INTERVAL_SECONDS:-1.5}"
TRACE_UI_EXTRA_ARGS="${TRACE_UI_EXTRA_ARGS:-}"

AGENTIC_TRACE_UI_TOKEN="${AGENTIC_TRACE_UI_TOKEN:-}"
AGENTIC_TRACE_UI_VIEWER_TOKEN="${AGENTIC_TRACE_UI_VIEWER_TOKEN:-}"
AGENTIC_TRACE_UI_OPERATOR_TOKEN="${AGENTIC_TRACE_UI_OPERATOR_TOKEN:-}"
AGENTIC_TRACE_UI_APPROVER_TOKEN="${AGENTIC_TRACE_UI_APPROVER_TOKEN:-}"
AGENTIC_TRACE_UI_ADMIN_TOKEN="${AGENTIC_TRACE_UI_ADMIN_TOKEN:-}"

SERVICE_TEMPLATE="${PROJECT_ROOT}/systemd/agentic-trace-ui.service"
SERVICE_TARGET="/etc/systemd/system/agentic-trace-ui.service"
ENV_DIR="/etc/agentic-linux-troubleshooter"
ENV_FILE="${ENV_DIR}/trace-ui.env"

if [[ ! -f "${SERVICE_TEMPLATE}" ]]; then
  echo "Missing service template: ${SERVICE_TEMPLATE}"
  exit 1
fi

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "Python binary not found or not executable: ${PYTHON_BIN}"
  echo "Run: source .venv/bin/activate && python -m pip install -r requirements.txt"
  exit 1
fi

if [[ -z "${AGENTIC_TRACE_UI_TOKEN}${AGENTIC_TRACE_UI_VIEWER_TOKEN}${AGENTIC_TRACE_UI_OPERATOR_TOKEN}${AGENTIC_TRACE_UI_APPROVER_TOKEN}${AGENTIC_TRACE_UI_ADMIN_TOKEN}" ]]; then
  echo "Set AGENTIC_TRACE_UI_TOKEN or one role token before installing Trace UI."
  exit 1
fi

if [[ "${SKIP_PREFLIGHT:-}" != "1" ]]; then
  "${PYTHON_BIN}" "${PROJECT_ROOT}/scripts/preflight_deploy.py" \
    --project-root "${PROJECT_ROOT}" \
    --python-bin "${PYTHON_BIN}" \
    --config "${CONFIG_PATH}" \
    --state-dir "${STATE_DIR}" \
    --output-root "${OUTPUT_ROOT}" \
    --host "${TRACE_UI_HOST}" \
    --port "${TRACE_UI_PORT}"
fi

echo "[Install Trace UI] project_root=${PROJECT_ROOT}"
echo "[Install Trace UI] python_bin=${PYTHON_BIN}"
echo "[Install Trace UI] listen=${TRACE_UI_HOST}:${TRACE_UI_PORT}"
echo "[Install Trace UI] run_user=${RUN_USER}"
echo "[Install Trace UI] run_group=${RUN_GROUP}"

sudo mkdir -p "${ENV_DIR}"
sudo mkdir -p "${STATE_DIR}" "${OUTPUT_ROOT}"
sudo chown -R "${RUN_USER}:${RUN_GROUP}" "${STATE_DIR}" "${OUTPUT_ROOT}"

sudo tee "${ENV_FILE}" >/dev/null <<EOF
PROJECT_ROOT=${PROJECT_ROOT}
PYTHON_BIN=${PYTHON_BIN}
CONFIG_PATH=${CONFIG_PATH}
STATE_DIR=${STATE_DIR}
OUTPUT_ROOT=${OUTPUT_ROOT}
TRACE_UI_HOST=${TRACE_UI_HOST}
TRACE_UI_PORT=${TRACE_UI_PORT}
SESSION_TTL_SECONDS=${SESSION_TTL_SECONDS}
WORKER_POLL_INTERVAL_SECONDS=${WORKER_POLL_INTERVAL_SECONDS}
TRACE_UI_EXTRA_ARGS=${TRACE_UI_EXTRA_ARGS}
AGENTIC_TRACE_UI_TOKEN=${AGENTIC_TRACE_UI_TOKEN}
AGENTIC_TRACE_UI_VIEWER_TOKEN=${AGENTIC_TRACE_UI_VIEWER_TOKEN}
AGENTIC_TRACE_UI_OPERATOR_TOKEN=${AGENTIC_TRACE_UI_OPERATOR_TOKEN}
AGENTIC_TRACE_UI_APPROVER_TOKEN=${AGENTIC_TRACE_UI_APPROVER_TOKEN}
AGENTIC_TRACE_UI_ADMIN_TOKEN=${AGENTIC_TRACE_UI_ADMIN_TOKEN}
EOF

TMP_SERVICE="$(mktemp)"
sed \
  -e "s|__RUN_USER__|${RUN_USER}|g" \
  -e "s|__RUN_GROUP__|${RUN_GROUP}|g" \
  "${SERVICE_TEMPLATE}" > "${TMP_SERVICE}"

sudo cp "${TMP_SERVICE}" "${SERVICE_TARGET}"
rm -f "${TMP_SERVICE}"

sudo systemctl daemon-reload
sudo systemctl enable agentic-trace-ui.service
sudo systemctl restart agentic-trace-ui.service

echo ""
echo "[Install Trace UI] done."
echo "Check status:"
echo "  systemctl status agentic-trace-ui.service"
echo ""
echo "Follow logs:"
echo "  journalctl -u agentic-trace-ui.service -f"
