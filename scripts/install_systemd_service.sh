#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${1:-}"
RUN_USER="${2:-${USER}}"
RUN_GROUP="${3:-${RUN_USER}}"

if [[ -z "${PROJECT_ID}" ]]; then
  echo "Usage: $0 <project_id> [run_user] [run_group]"
  echo "Example: $0 enterprise_demo_local lf lf"
  exit 1
fi

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-${PROJECT_ROOT}/.venv/bin/python}"
CONFIG_PATH="${CONFIG_PATH:-${PROJECT_ROOT}/configs/projects.yaml}"
STATE_DIR="${STATE_DIR:-${PROJECT_ROOT}/state}"
DAEMON_LOG="${DAEMON_LOG:-${STATE_DIR}/${PROJECT_ID}/daemon.log}"
AGENT_DEPTH="${AGENT_DEPTH:-balanced}"
REPORT_MODE="${REPORT_MODE:-llm}"
HEARTBEAT_INTERVAL="${HEARTBEAT_INTERVAL:-60}"
HEALTH_CHECK_INTERVAL="${HEALTH_CHECK_INTERVAL:-300}"

SERVICE_TEMPLATE="${PROJECT_ROOT}/systemd/agentic-monitor@.service"
SERVICE_TARGET="/etc/systemd/system/agentic-monitor@.service"
ENV_DIR="/etc/agentic-linux-troubleshooter"
ENV_FILE="${ENV_DIR}/${PROJECT_ID}.env"

if [[ ! -f "${SERVICE_TEMPLATE}" ]]; then
  echo "Missing service template: ${SERVICE_TEMPLATE}"
  exit 1
fi

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "Python binary not found or not executable: ${PYTHON_BIN}"
  echo "Run: source .venv/bin/activate && python -m pip install -r requirements.txt"
  exit 1
fi

echo "[Install] project_id=${PROJECT_ID}"
echo "[Install] project_root=${PROJECT_ROOT}"
echo "[Install] python_bin=${PYTHON_BIN}"
echo "[Install] run_user=${RUN_USER}"
echo "[Install] run_group=${RUN_GROUP}"

sudo mkdir -p "${ENV_DIR}"

sudo tee "${ENV_FILE}" >/dev/null <<EOF
PROJECT_ROOT=${PROJECT_ROOT}
PYTHON_BIN=${PYTHON_BIN}
CONFIG_PATH=${CONFIG_PATH}
AGENT_DEPTH=${AGENT_DEPTH}
REPORT_MODE=${REPORT_MODE}
STATE_DIR=${STATE_DIR}
DAEMON_LOG=${DAEMON_LOG}
HEARTBEAT_INTERVAL=${HEARTBEAT_INTERVAL}
HEALTH_CHECK_INTERVAL=${HEALTH_CHECK_INTERVAL}
EOF

TMP_SERVICE="$(mktemp)"
sed \
  -e "s|__RUN_USER__|${RUN_USER}|g" \
  -e "s|__RUN_GROUP__|${RUN_GROUP}|g" \
  "${SERVICE_TEMPLATE}" > "${TMP_SERVICE}"

sudo cp "${TMP_SERVICE}" "${SERVICE_TARGET}"
rm -f "${TMP_SERVICE}"

sudo systemctl daemon-reload
sudo systemctl enable "agentic-monitor@${PROJECT_ID}.service"
sudo systemctl restart "agentic-monitor@${PROJECT_ID}.service"

echo ""
echo "[Install] done."
echo "Check status:"
echo "  systemctl status agentic-monitor@${PROJECT_ID}.service"
echo ""
echo "Follow logs:"
echo "  journalctl -u agentic-monitor@${PROJECT_ID}.service -f"