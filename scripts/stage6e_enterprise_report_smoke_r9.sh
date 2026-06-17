#!/usr/bin/env bash
set -euo pipefail
trap 'echo "[ERROR] line=${LINENO} command failed: ${BASH_COMMAND}" >&2' ERR

PROJECT_ROOT="/home/lf/projects/agentic-linux-troubleshooter"
PROJECT_ID="enterprise_demo_local"
SERVICE_NAME="agentic-monitor@${PROJECT_ID}.service"
RUNTIME_LOG="/home/lf/runtime_projects/enterprise_order_monitoring_service/outputs/service.log"
ALERT_JSONL="${PROJECT_ROOT}/outputs/alerts/${PROJECT_ID}_alerts.jsonl"
ALERT_ARCHIVE_DIR="${PROJECT_ROOT}/outputs/alerts/${PROJECT_ID}_alerts"
MONITOR_DIR="${PROJECT_ROOT}/outputs/monitors/${PROJECT_ID}"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
ARTIFACT_DIR="${PROJECT_ROOT}/acceptance_artifacts/enterprise_report_smoke_r9_${TIMESTAMP}"
SUMMARY_PATH="${ARTIFACT_DIR}/R9_SMOKE_SUMMARY.md"
PRECHECK_ONLY=0
COUNT_ONLY=0

if [[ "${1:-}" == "--precheck-only" ]]; then
  PRECHECK_ONLY=1
elif [[ "${1:-}" == "--count-only" ]]; then
  COUNT_ONLY=1
fi

info() { echo "[INFO] $*"; }
warn() { echo "[WARN] $*" >&2; }

safe_grep_count() {
  local pattern="$1"
  local path="$2"

  if [[ ! -e "${path}" ]]; then
    echo "0"
    return 0
  fi

  local count
  count="$(grep -RIl -- "${pattern}" "${path}" 2>/dev/null | wc -l || true)"
  echo "${count:-0}"
}

safe_file_count() {
  local dir="$1"
  local name_pattern="$2"

  if [[ ! -d "${dir}" ]]; then
    echo "0"
    return 0
  fi

  local count
  count="$(find "${dir}" -type f -name "${name_pattern}" 2>/dev/null | wc -l || true)"
  echo "${count:-0}"
}

safe_jsonl_line_count() {
  local file="$1"

  if [[ ! -f "${file}" ]]; then
    echo "0"
    return 0
  fi

  local count
  count="$(wc -l < "${file}" 2>/dev/null || true)"
  echo "${count:-0}"
}

count_alerts() {
  local jsonl_count
  local md_count
  local json_count

  jsonl_count="$(safe_jsonl_line_count "${ALERT_JSONL}")"
  md_count="$(safe_file_count "${ALERT_ARCHIVE_DIR}" "*.md")"
  json_count="$(safe_file_count "${ALERT_ARCHIVE_DIR}" "*.json")"

  echo "$((jsonl_count + md_count + json_count))"
}

count_reports_for_event() {
  local event_type="$1"
  safe_grep_count "${event_type}" "${MONITOR_DIR}"
}

count_monitor_md() {
  safe_file_count "${MONITOR_DIR}" "*.md"
}

append_process_crash() {
  local smoke_id="$1"
  {
    printf '\n'
    printf '[R9_SMOKE_ID=%s][process_crash]\n' "${smoke_id}"
    printf 'systemd[1]: acme-order-worker.service: Main process exited, code=dumped, status=11/SEGV\n'
    printf 'systemd[1]: acme-order-worker.service: Failed with result '\''core-dump'\''\n'
  } >> "${RUNTIME_LOG}"
}

append_container_k8s() {
  local smoke_id="$1"
  {
    printf '\n'
    printf '[R9_SMOKE_ID=%s][container_k8s]\n' "${smoke_id}"
    printf 'pod/acme-order-api-7f9c CrashLoopBackOff\n'
    printf 'Back-off restarting failed container acme-order-api\n'
    printf 'Warning Failed pod/acme-order-api Error: ImagePullBackOff\n'
    printf 'Last State: Terminated Reason: OOMKilled\n'
  } >> "${RUNTIME_LOG}"
}

run_case() {
  local event_type="$1"
  local smoke_id="R9_${event_type}_${TIMESTAMP}"
  local baseline_alerts
  local baseline_reports
  local final_alerts
  local final_reports
  local status="PARTIAL"

  baseline_alerts="$(count_alerts)"
  baseline_reports="$(count_reports_for_event "${event_type}")"

  info "Injecting ${event_type} with ${smoke_id}"
  if [[ "${event_type}" == "process_crash" ]]; then
    append_process_crash "${smoke_id}"
  elif [[ "${event_type}" == "container_k8s" ]]; then
    append_container_k8s "${smoke_id}"
  else
    warn "Unsupported case: ${event_type}"
    return
  fi

  for elapsed in 5 10 15 20 25 30 35 40 45 50 55 60; do
    sleep 5
    final_alerts="$(count_alerts)"
    final_reports="$(count_reports_for_event "${event_type}")"
    info "Waiting ${event_type}: ${elapsed}/60s alerts=${final_alerts} reports=${final_reports}"

    if (( final_alerts > baseline_alerts || final_reports > baseline_reports )); then
      status="PASS"
      break
    fi
  done

  final_alerts="$(count_alerts)"
  final_reports="$(count_reports_for_event "${event_type}")"

  {
    printf '## %s\n\n' "${event_type}"
    printf -- '- smoke_id: `%s`\n' "${smoke_id}"
    printf -- '- injected: `yes`\n'
    printf -- '- baseline_alerts: `%s`\n' "${baseline_alerts}"
    printf -- '- final_alerts: `%s`\n' "${final_alerts}"
    printf -- '- baseline_reports: `%s`\n' "${baseline_reports}"
    printf -- '- final_reports: `%s`\n' "${final_reports}"
    printf -- '- final_status: `%s`\n\n' "${status}"
  } >> "${SUMMARY_PATH}"
}

cd "${PROJECT_ROOT}"
mkdir -p "${ARTIFACT_DIR}"
mkdir -p "$(dirname "${RUNTIME_LOG}")"

active_state="$(systemctl show "${SERVICE_NAME}" -p ActiveState --value | tr -d '\r')"
sub_state="$(systemctl show "${SERVICE_NAME}" -p SubState --value | tr -d '\r')"
main_pid="$(systemctl show "${SERVICE_NAME}" -p MainPID --value | tr -d '\r')"
exec_main_status="$(systemctl show "${SERVICE_NAME}" -p ExecMainStatus --value | tr -d '\r')"

{
  printf '# R9 Smoke Summary\n\n'
  printf '## Service Status\n\n'
  printf -- '- service: `%s`\n' "${SERVICE_NAME}"
  printf -- '- ActiveState: `%s`\n' "${active_state}"
  printf -- '- SubState: `%s`\n' "${sub_state}"
  printf -- '- MainPID: `%s`\n' "${main_pid}"
  printf -- '- ExecMainStatus: `%s`\n\n' "${exec_main_status}"
} > "${SUMMARY_PATH}"

info "Service status: ActiveState=${active_state} SubState=${sub_state} MainPID=${main_pid} ExecMainStatus=${exec_main_status}"

if [[ "${active_state}" != "active" || "${sub_state}" != "running" ]]; then
  warn "Service is not active/running."
  warn "Please run manually: sudo systemctl restart agentic-monitor@enterprise_demo_local.service"
  {
    printf '## Final Result\n\n'
    printf 'R9 FAIL: service is not active/running.\n'
  } >> "${SUMMARY_PATH}"
  exit 1
fi

if (( PRECHECK_ONLY == 1 )); then
  {
    printf '## Final Result\n\n'
    printf 'R9 PRECHECK PASS: service is active/running.\n'
  } >> "${SUMMARY_PATH}"
  info "Precheck-only passed. Summary written to ${SUMMARY_PATH}"
  exit 0
fi

if (( COUNT_ONLY == 1 )); then
  alerts_count="$(count_alerts)"
  monitor_md_count="$(count_monitor_md)"
  process_crash_reports="$(count_reports_for_event "process_crash")"
  container_k8s_reports="$(count_reports_for_event "container_k8s")"
  {
    printf '## Count Only\n\n'
    printf -- '- alerts_count: `%s`\n' "${alerts_count}"
    printf -- '- monitor_md_count: `%s`\n' "${monitor_md_count}"
    printf -- '- process_crash_reports: `%s`\n' "${process_crash_reports}"
    printf -- '- container_k8s_reports: `%s`\n' "${container_k8s_reports}"
    printf '\n## Final Result\n\n'
    printf 'R9 COUNT ONLY PASS.\n'
  } >> "${SUMMARY_PATH}"
  info "Count-only: alerts=${alerts_count} monitor_md=${monitor_md_count} process_crash_reports=${process_crash_reports} container_k8s_reports=${container_k8s_reports}"
  info "Count-only passed. Summary written to ${SUMMARY_PATH}"
  exit 0
fi

run_case "process_crash"
run_case "container_k8s"

{
  printf '## Final Result\n\n'
  printf 'R9 smoke script completed. Treat any case without PASS as PARTIAL until manually reviewed.\n'
} >> "${SUMMARY_PATH}"

info "Summary written to ${SUMMARY_PATH}"
