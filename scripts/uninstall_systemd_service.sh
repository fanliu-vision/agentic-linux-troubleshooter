#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${1:-}"

if [[ -z "${PROJECT_ID}" ]]; then
  echo "Usage: $0 <project_id>"
  echo "Example: $0 enterprise_demo_local"
  exit 1
fi

SERVICE_NAME="agentic-monitor@${PROJECT_ID}.service"
ENV_FILE="/etc/agentic-linux-troubleshooter/${PROJECT_ID}.env"

echo "[Uninstall] stopping ${SERVICE_NAME}"

sudo systemctl stop "${SERVICE_NAME}" || true
sudo systemctl disable "${SERVICE_NAME}" || true

echo "[Uninstall] removing env file: ${ENV_FILE}"
sudo rm -f "${ENV_FILE}"

sudo systemctl daemon-reload

echo ""
echo "[Uninstall] done."
echo "Service template /etc/systemd/system/agentic-monitor@.service is kept for other projects."