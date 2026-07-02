#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="agentic-trace-ui.service"
ENV_FILE="/etc/agentic-linux-troubleshooter/trace-ui.env"
SERVICE_TARGET="/etc/systemd/system/${SERVICE_NAME}"

echo "[Uninstall Trace UI] stopping ${SERVICE_NAME}"

sudo systemctl stop "${SERVICE_NAME}" || true
sudo systemctl disable "${SERVICE_NAME}" || true

echo "[Uninstall Trace UI] removing env file: ${ENV_FILE}"
sudo rm -f "${ENV_FILE}"

echo "[Uninstall Trace UI] removing service file: ${SERVICE_TARGET}"
sudo rm -f "${SERVICE_TARGET}"

sudo systemctl daemon-reload

echo ""
echo "[Uninstall Trace UI] done."
