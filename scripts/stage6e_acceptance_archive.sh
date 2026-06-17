#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="/home/lf/projects/agentic-linux-troubleshooter"
PROJECT_ID="enterprise_demo_local"
SERVICE_NAME="agentic-monitor@${PROJECT_ID}.service"
RUNTIME_CONFIG="/home/lf/runtime_projects/enterprise_order_monitoring_service/config.json"

info() { echo "[INFO] $*"; }
pass() { echo "[PASS] $*"; }
fail() { echo "[FAIL] $*" >&2; exit 1; }

[[ "$(pwd)" == "${PROJECT_ROOT}" ]] || fail "Please run from ${PROJECT_ROOT}"

timestamp="$(date +%Y%m%d_%H%M%S)"
artifact_dir="acceptance_artifacts/stage6e2_${timestamp}"
mkdir -p "${artifact_dir}"
info "Archiving Stage 6E-2 evidence to ${artifact_dir}"

systemctl status "${SERVICE_NAME}" --no-pager -l > "${artifact_dir}/systemd_status.txt"
systemctl show "${SERVICE_NAME}" \
  -p ActiveState \
  -p SubState \
  -p MainPID \
  -p ExecMainStatus \
  -p FragmentPath > "${artifact_dir}/systemd_show.txt"
journalctl -u "${SERVICE_NAME}" -n 500 --no-pager -o cat > "${artifact_dir}/journal_last_500.txt"
pass "systemd status, show, and journal archived"

if [[ -d "state/${PROJECT_ID}" ]]; then
  cp -r "state/${PROJECT_ID}" "${artifact_dir}/state_enterprise_demo_local"
  pass "daemon state archived"
else
  echo "[INFO] state/${PROJECT_ID} does not exist; skipping state copy"
fi

if [[ -d "outputs/alerts" ]]; then
  cp -r "outputs/alerts" "${artifact_dir}/alerts"
  find "outputs/alerts" -type f | sort > "${artifact_dir}/alert_file_list.txt"
  pass "alerts archived"
else
  echo "[INFO] outputs/alerts does not exist; writing empty alert file list"
  : > "${artifact_dir}/alert_file_list.txt"
fi

if [[ -d "outputs/monitors/${PROJECT_ID}" ]]; then
  cp -r "outputs/monitors/${PROJECT_ID}" "${artifact_dir}/monitors_enterprise_demo_local"
  find "outputs/monitors/${PROJECT_ID}" -type f | sort > "${artifact_dir}/monitor_file_list.txt"
  pass "monitor reports archived"
else
  echo "[INFO] outputs/monitors/${PROJECT_ID} does not exist; writing empty monitor file list"
  : > "${artifact_dir}/monitor_file_list.txt"
fi

if [[ -f "${RUNTIME_CONFIG}" ]]; then
  cp "${RUNTIME_CONFIG}" "${artifact_dir}/runtime_config.json"
  pass "runtime config archived"
else
  echo "[INFO] runtime config missing; skipping: ${RUNTIME_CONFIG}"
fi

acceptance_time="$(date '+%Y-%m-%d %H:%M:%S %Z')"
cat > "${artifact_dir}/ACCEPTANCE_SUMMARY.md" <<EOF
# Stage 6E-2 Acceptance Evidence

- acceptance_time: \`${acceptance_time}\`
- project_id: \`${PROJECT_ID}\`
- artifact_dir: \`${artifact_dir}\`

## Summary

Stage 6E-2 acceptance evidence archived.

## Files

- \`systemd_status.txt\`
- \`systemd_show.txt\`
- \`journal_last_500.txt\`
- \`state_enterprise_demo_local/\` when available
- \`alerts/\` when available
- \`monitors_enterprise_demo_local/\` when available
- \`runtime_config.json\` when available
- \`monitor_file_list.txt\`
- \`alert_file_list.txt\`

## Next Step

Run \`scripts/stage6e_acceptance_check.sh\` to verify the current acceptance state.
EOF
pass "ACCEPTANCE_SUMMARY.md generated"

echo ""
echo "[PASS] Stage 6E-2 evidence archived: ${artifact_dir}"
