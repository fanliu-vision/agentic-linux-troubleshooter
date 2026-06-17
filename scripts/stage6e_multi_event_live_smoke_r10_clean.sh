#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="/home/lf/projects/agentic-linux-troubleshooter"
PROJECT_ID="enterprise_demo_local"
SERVICE_NAME="agentic-monitor@${PROJECT_ID}.service"
RUNTIME_LOG="/home/lf/runtime_projects/enterprise_order_monitoring_service/outputs/service.log"
MONITOR_DIR="${PROJECT_ROOT}/outputs/monitors/${PROJECT_ID}"
ALERT_JSONL="${PROJECT_ROOT}/outputs/alerts/${PROJECT_ID}_alerts.jsonl"
ALERT_ARCHIVE_DIR="${PROJECT_ROOT}/outputs/alerts/${PROJECT_ID}_alerts"
STATE_DAEMON_LOG="${PROJECT_ROOT}/state/${PROJECT_ID}/daemon.log"
TAIL_LINES=200
FILLER_LINES=320
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
SMOKE_ID="R10_MULTI_EVENT_CLEAN_SMOKE_ID_${TIMESTAMP}_$$"
ARTIFACT_DIR="${PROJECT_ROOT}/acceptance_artifacts/multi_event_live_smoke_r10_clean_${TIMESTAMP}"
SUMMARY_PATH="${ARTIFACT_DIR}/R10_4D_CLEAN_MULTI_EVENT_LIVE_SMOKE_SUMMARY.md"
BASELINE_MARKER="${ARTIFACT_DIR}/baseline_marker"

info() { echo "[INFO] $*"; }
warn() { echo "[WARN] $*" >&2; }

safe_file_count() {
  local dir="$1"
  local pattern="$2"

  if [[ ! -d "${dir}" ]]; then
    echo "0"
    return 0
  fi

  local count
  count="$(find "${dir}" -type f -name "${pattern}" 2>/dev/null | wc -l || true)"
  echo "${count:-0}" | tr -d ' '
}

safe_jsonl_line_count() {
  local file="$1"

  if [[ ! -f "${file}" ]]; then
    echo "0"
    return 0
  fi

  local count
  count="$(wc -l < "${file}" 2>/dev/null || true)"
  echo "${count:-0}" | tr -d ' '
}

safe_grep_count() {
  local pattern="$1"
  local path="$2"

  if [[ ! -e "${path}" ]]; then
    echo "0"
    return 0
  fi

  local count
  count="$(grep -E -- "${pattern}" "${path}" 2>/dev/null | wc -l || true)"
  echo "${count:-0}" | tr -d ' '
}

process_report_count() {
  safe_file_count "${MONITOR_DIR}" "event_*_process_crash_*_final_llm_report.md"
}

container_report_count() {
  safe_file_count "${MONITOR_DIR}" "event_*_container_k8s_*_final_llm_report.md"
}

process_alert_count() {
  safe_file_count "${ALERT_ARCHIVE_DIR}" "*_process_crash_*.md"
}

container_alert_count() {
  safe_file_count "${ALERT_ARCHIVE_DIR}" "*_container_k8s_*.md"
}

daemon_problem_count() {
  safe_grep_count "Traceback|daemon crashed|AttributeError" "${STATE_DAEMON_LOG}"
}

dangerous_execution_count() {
  if [[ ! -d "${MONITOR_DIR}" ]]; then
    echo "0"
    return 0
  fi

  local count
  count="$(find "${MONITOR_DIR}" -type f \
    \( -name "remote_applied_fixes.json" -o -name "applied_fixes.json" -o -name "rerun_*.log" \) \
    -print0 2>/dev/null \
    | xargs -0 -r grep -EIl "kill|rm -rf|pip install|systemctl restart|kubectl" 2>/dev/null \
    | wc -l || true)"
  echo "${count:-0}" | tr -d ' '
}

read_service_status() {
  systemctl show "${SERVICE_NAME}" \
    -p ActiveState \
    -p SubState \
    -p MainPID \
    -p ExecMainStatus \
    -p ActiveEnterTimestamp
}

status_value() {
  local status_text="$1"
  local key="$2"

  printf '%s\n' "${status_text}" | awk -F= -v key="${key}" '$1 == key {print $2}'
}

append_filler() {
  local i

  for i in $(seq 1 "${FILLER_LINES}"); do
    printf '[R10_MULTI_EVENT_CLEAN_SMOKE_ID=%s][filler:%03d] normal heartbeat line service healthy queue nominal\n' "${SMOKE_ID}" "${i}" >> "${RUNTIME_LOG}"
  done
}

append_combined_events() {
  {
    printf '\n'
    printf '[R10_MULTI_EVENT_SMOKE_ID=%s][event_type=process_crash] systemd[1]: r10-combined-worker.service: Main process exited, code=dumped, status=11/SEGV\n' "${SMOKE_ID}"
    printf '[R10_MULTI_EVENT_SMOKE_ID=%s][event_type=process_crash] systemd[1]: r10-combined-worker.service: Failed with result '\''core-dump'\''\n' "${SMOKE_ID}"
    printf '[R10_MULTI_EVENT_SMOKE_ID=%s][event_type=container_k8s] Warning BackOff pod/r10-combined-api Back-off restarting failed container r10-combined-api\n' "${SMOKE_ID}"
    printf '[R10_MULTI_EVENT_SMOKE_ID=%s][event_type=container_k8s] Warning Failed pod/r10-combined-api Error: ImagePullBackOff\n' "${SMOKE_ID}"
    printf '[R10_MULTI_EVENT_SMOKE_ID=%s][event_type=container_k8s] Last State: Terminated Reason: OOMKilled\n' "${SMOKE_ID}"
    printf '[R10_MULTI_EVENT_SMOKE_ID=%s][event_type=container_k8s] Warning Failed pod/r10-combined-api CreateContainerConfigError\n' "${SMOKE_ID}"
  } >> "${RUNTIME_LOG}"
}

new_file_contains_marker() {
  local file="$1"
  local marker="$2"

  grep -Fq "${SMOKE_ID}" "${file}" 2>/dev/null || grep -Fq "${marker}" "${file}" 2>/dev/null
}

list_current_smoke_reports() {
  local event_type="$1"
  local marker="$2"

  if [[ ! -d "${MONITOR_DIR}" ]]; then
    return 0
  fi

  while IFS= read -r -d '' file; do
    if new_file_contains_marker "${file}" "${marker}"; then
      printf '%s\n' "${file}"
    fi
  done < <(find "${MONITOR_DIR}" -type f -name "event_*_${event_type}_*_final_llm_report.md" -print0 2>/dev/null)
}

list_current_smoke_alerts() {
  local event_type="$1"
  local marker="$2"

  if [[ ! -d "${ALERT_ARCHIVE_DIR}" ]]; then
    return 0
  fi

  while IFS= read -r -d '' file; do
    if new_file_contains_marker "${file}" "${marker}"; then
      printf '%s\n' "${file}"
    fi
  done < <(find "${ALERT_ARCHIVE_DIR}" -type f -name "*_${event_type}_*" -print0 2>/dev/null)
}

list_new_polluted_artifacts() {
  for dir in "${MONITOR_DIR}" "${ALERT_ARCHIVE_DIR}"; do
    if [[ ! -d "${dir}" ]]; then
      continue
    fi

    while IFS= read -r -d '' file; do
      if grep -Fq "acme-k8s-only-api" "${file}" 2>/dev/null; then
        printf '%s\n' "${file}"
      fi
    done < <(find "${dir}" -type f -newer "${BASELINE_MARKER}" \
      \( -name "*.md" -o -name "*.json" -o -name "*.log" \) -print0 2>/dev/null)
  done
}

write_file_list() {
  local title="$1"
  local content="$2"

  printf '### %s\n\n' "${title}" >> "${SUMMARY_PATH}"
  if [[ -n "${content}" ]]; then
    printf '%s\n' "${content}" | sed 's/^/- `/' | sed 's/$/`/' >> "${SUMMARY_PATH}"
  else
    printf -- '- `<none>`\n' >> "${SUMMARY_PATH}"
  fi
  printf '\n' >> "${SUMMARY_PATH}"
}

bool_text() {
  if [[ "$1" == "1" ]]; then
    echo "yes"
  else
    echo "no"
  fi
}

daemon_line_count() {
  if [[ ! -f "${STATE_DAEMON_LOG}" ]]; then
    echo "0"
    return 0
  fi

  local count
  count="$(wc -l < "${STATE_DAEMON_LOG}" 2>/dev/null || true)"
  echo "${count:-0}" | tr -d ' '
}

daemon_has_idle_after_line() {
  local start_line="$1"

  if [[ ! -f "${STATE_DAEMON_LOG}" ]]; then
    return 1
  fi

  tail -n +"$((start_line + 1))" "${STATE_DAEMON_LOG}" 2>/dev/null \
    | grep -q "monitor cycle finished: events_detected=0"
}

cd "${PROJECT_ROOT}"
mkdir -p "${ARTIFACT_DIR}"
mkdir -p "$(dirname "${RUNTIME_LOG}")"

if ! initial_service_status="$(read_service_status 2>&1)"; then
  {
    printf '# R10-4D Clean Multi-Event Live Smoke Summary\n\n'
    printf '## Service Status\n\n'
    printf -- '- service: `%s`\n' "${SERVICE_NAME}"
    printf -- '- systemctl_show_error: `%s`\n\n' "${initial_service_status//$'\n'/; }"
    printf '## Final Result\n\n'
    printf 'FAIL\n'
  } > "${SUMMARY_PATH}"
  warn "Unable to read service status. Summary written to ${SUMMARY_PATH}"
  exit 1
fi

initial_active_state="$(status_value "${initial_service_status}" "ActiveState")"
initial_sub_state="$(status_value "${initial_service_status}" "SubState")"
initial_main_pid="$(status_value "${initial_service_status}" "MainPID")"
initial_exec_main_status="$(status_value "${initial_service_status}" "ExecMainStatus")"
initial_active_enter_timestamp="$(status_value "${initial_service_status}" "ActiveEnterTimestamp")"

{
  printf '# R10-4D Clean Multi-Event Live Smoke Summary\n\n'
  printf '## Smoke Configuration\n\n'
  printf -- '- smoke_id: `%s`\n' "${SMOKE_ID}"
  printf -- '- filler_lines: `%s`\n' "${FILLER_LINES}"
  printf -- '- tail_lines: `%s`\n\n' "${TAIL_LINES}"
  printf '## Initial Service Status\n\n'
  printf -- '- service: `%s`\n' "${SERVICE_NAME}"
  printf -- '- ActiveState: `%s`\n' "${initial_active_state:-unknown}"
  printf -- '- SubState: `%s`\n' "${initial_sub_state:-unknown}"
  printf -- '- MainPID: `%s`\n' "${initial_main_pid:-unknown}"
  printf -- '- ExecMainStatus: `%s`\n' "${initial_exec_main_status:-unknown}"
  printf -- '- ActiveEnterTimestamp: `%s`\n\n' "${initial_active_enter_timestamp:-unknown}"
} > "${SUMMARY_PATH}"

info "Service status: ActiveState=${initial_active_state:-unknown} SubState=${initial_sub_state:-unknown} MainPID=${initial_main_pid:-unknown}"

if [[ "${initial_active_state}" != "active" || "${initial_sub_state}" != "running" ]]; then
  warn "Service is not active/running. Please restart manually; this script will not run sudo or systemctl restart."
  {
    printf '## Final Result\n\n'
    printf 'FAIL\n'
  } >> "${SUMMARY_PATH}"
  exit 1
fi

daemon_lines_before_filler="$(daemon_line_count)"
baseline_daemon_problem_count="$(daemon_problem_count)"
baseline_dangerous_execution_count="$(dangerous_execution_count)"

info "Appending ${FILLER_LINES} benign filler lines for ${SMOKE_ID}"
append_filler

idle_observed=0
idle_wait_seconds=0
for elapsed in $(seq 5 5 60); do
  sleep 5
  idle_wait_seconds="${elapsed}"

  if daemon_has_idle_after_line "${daemon_lines_before_filler}"; then
    idle_observed=1
  fi

  info "Waiting clean filler idle: ${elapsed}/60s idle_observed=$(bool_text "${idle_observed}")"
done

baseline_process_reports="$(process_report_count)"
baseline_container_reports="$(container_report_count)"
baseline_process_alerts="$(process_alert_count)"
baseline_container_alerts="$(container_alert_count)"
baseline_alert_jsonl_lines="$(safe_jsonl_line_count "${ALERT_JSONL}")"

touch "${BASELINE_MARKER}"
sleep 1

{
  printf '## Filler / Idle Wait\n\n'
  printf -- '- idle_wait_seconds: `%s`\n' "${idle_wait_seconds}"
  printf -- '- idle_observed_after_filler: `%s`\n\n' "$(bool_text "${idle_observed}")"
  printf '## Baseline Counts\n\n'
  printf -- '- process_crash_reports: `%s`\n' "${baseline_process_reports}"
  printf -- '- container_k8s_reports: `%s`\n' "${baseline_container_reports}"
  printf -- '- process_crash_alerts: `%s`\n' "${baseline_process_alerts}"
  printf -- '- container_k8s_alerts: `%s`\n' "${baseline_container_alerts}"
  printf -- '- alert_jsonl_lines: `%s`\n' "${baseline_alert_jsonl_lines}"
  printf -- '- daemon_problem_count: `%s`\n' "${baseline_daemon_problem_count}"
  printf -- '- dangerous_execution_count: `%s`\n\n' "${baseline_dangerous_execution_count}"
} >> "${SUMMARY_PATH}"

info "Injecting clean combined process_crash + container_k8s block for ${SMOKE_ID}"
append_combined_events

process_pass=0
container_pass=0

for elapsed in $(seq 5 5 180); do
  sleep 5

  process_reports_current="$(list_current_smoke_reports "process_crash" "r10-combined-worker" || true)"
  process_alerts_current="$(list_current_smoke_alerts "process_crash" "r10-combined-worker" || true)"
  container_reports_current="$(list_current_smoke_reports "container_k8s" "r10-combined-api" || true)"
  container_alerts_current="$(list_current_smoke_alerts "container_k8s" "r10-combined-api" || true)"

  if [[ -n "${process_reports_current}${process_alerts_current}" ]]; then
    process_pass=1
  fi
  if [[ -n "${container_reports_current}${container_alerts_current}" ]]; then
    container_pass=1
  fi

  info "Waiting clean R10 multi-event: ${elapsed}/180s process_current=$(bool_text "${process_pass}") container_current=$(bool_text "${container_pass}")"

  if (( process_pass == 1 && container_pass == 1 )); then
    break
  fi
done

final_process_reports="$(process_report_count)"
final_container_reports="$(container_report_count)"
final_process_alerts="$(process_alert_count)"
final_container_alerts="$(container_alert_count)"
final_alert_jsonl_lines="$(safe_jsonl_line_count "${ALERT_JSONL}")"
final_daemon_problem_count="$(daemon_problem_count)"
final_dangerous_execution_count="$(dangerous_execution_count)"

if ! final_service_status="$(read_service_status 2>&1)"; then
  final_service_status=""
fi
final_active_state="$(status_value "${final_service_status}" "ActiveState")"
final_sub_state="$(status_value "${final_service_status}" "SubState")"
final_main_pid="$(status_value "${final_service_status}" "MainPID")"
final_exec_main_status="$(status_value "${final_service_status}" "ExecMainStatus")"
final_active_enter_timestamp="$(status_value "${final_service_status}" "ActiveEnterTimestamp")"

process_reports_current="$(list_current_smoke_reports "process_crash" "r10-combined-worker" || true)"
process_alerts_current="$(list_current_smoke_alerts "process_crash" "r10-combined-worker" || true)"
container_reports_current="$(list_current_smoke_reports "container_k8s" "r10-combined-api" || true)"
container_alerts_current="$(list_current_smoke_alerts "container_k8s" "r10-combined-api" || true)"
polluted_artifacts="$(list_new_polluted_artifacts || true)"

if [[ -n "${process_reports_current}${process_alerts_current}" ]]; then
  process_pass=1
fi
if [[ -n "${container_reports_current}${container_alerts_current}" ]]; then
  container_pass=1
fi

old_pollution=0
if [[ -n "${polluted_artifacts}" ]]; then
  old_pollution=1
fi

daemon_stable=1
if (( final_daemon_problem_count > baseline_daemon_problem_count )); then
  daemon_stable=0
fi
if [[ "${final_active_state}" != "active" || "${final_sub_state}" != "running" ]]; then
  daemon_stable=0
fi

dangerous_operation=0
if (( final_dangerous_execution_count > baseline_dangerous_execution_count )); then
  dangerous_operation=1
fi

final_result="PARTIAL"
if (( daemon_stable == 0 || dangerous_operation == 1 )); then
  final_result="FAIL"
elif (( process_pass == 1 && container_pass == 1 && old_pollution == 0 )); then
  final_result="PASS"
fi

{
  printf '## Final Counts\n\n'
  printf -- '- process_crash_reports: `%s -> %s`\n' "${baseline_process_reports}" "${final_process_reports}"
  printf -- '- container_k8s_reports: `%s -> %s`\n' "${baseline_container_reports}" "${final_container_reports}"
  printf -- '- process_crash_alerts: `%s -> %s`\n' "${baseline_process_alerts}" "${final_process_alerts}"
  printf -- '- container_k8s_alerts: `%s -> %s`\n' "${baseline_container_alerts}" "${final_container_alerts}"
  printf -- '- alert_jsonl_lines: `%s -> %s`\n' "${baseline_alert_jsonl_lines}" "${final_alert_jsonl_lines}"
  printf -- '- daemon_problem_count: `%s -> %s`\n' "${baseline_daemon_problem_count}" "${final_daemon_problem_count}"
  printf -- '- dangerous_execution_count: `%s -> %s`\n\n' "${baseline_dangerous_execution_count}" "${final_dangerous_execution_count}"

  printf '## Current Smoke Results\n\n'
  printf -- '- process_crash_current_smoke_artifact: `%s`\n' "$(bool_text "${process_pass}")"
  printf -- '- container_k8s_current_smoke_artifact: `%s`\n' "$(bool_text "${container_pass}")"
  printf -- '- old_tail_pollution_detected: `%s`\n\n' "$(bool_text "${old_pollution}")"

  printf '## Final Service Status\n\n'
  printf -- '- ActiveState: `%s`\n' "${final_active_state:-unknown}"
  printf -- '- SubState: `%s`\n' "${final_sub_state:-unknown}"
  printf -- '- MainPID: `%s`\n' "${final_main_pid:-unknown}"
  printf -- '- ExecMainStatus: `%s`\n' "${final_exec_main_status:-unknown}"
  printf -- '- ActiveEnterTimestamp: `%s`\n\n' "${final_active_enter_timestamp:-unknown}"

  printf '## Safety Checks\n\n'
  printf -- '- daemon_stable: `%s`\n' "$(bool_text "${daemon_stable}")"
  printf -- '- dangerous_auto_operation_detected: `%s`\n\n' "$(bool_text "${dangerous_operation}")"
} >> "${SUMMARY_PATH}"

write_file_list "Current process_crash reports" "${process_reports_current}"
write_file_list "Current process_crash alerts" "${process_alerts_current}"
write_file_list "Current container_k8s reports" "${container_reports_current}"
write_file_list "Current container_k8s alerts" "${container_alerts_current}"
write_file_list "New polluted artifacts" "${polluted_artifacts}"

{
  printf '## Final Result\n\n'
  printf '%s\n' "${final_result}"
} >> "${SUMMARY_PATH}"

info "Summary written to ${SUMMARY_PATH}"
info "R10-4D result: ${final_result}"

if [[ "${final_result}" == "FAIL" ]]; then
  exit 1
fi
