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
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
SMOKE_ID="R10_MULTI_EVENT_SMOKE_ID_${TIMESTAMP}_$$"
ARTIFACT_DIR="${PROJECT_ROOT}/acceptance_artifacts/multi_event_live_smoke_r10_${TIMESTAMP}"
SUMMARY_PATH="${ARTIFACT_DIR}/R10_MULTI_EVENT_LIVE_SMOKE_SUMMARY.md"
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

safe_grep_file_count() {
  local pattern="$1"
  local dir="$2"

  if [[ ! -d "${dir}" ]]; then
    echo "0"
    return 0
  fi

  local count
  count="$(grep -RIl -- "${pattern}" "${dir}" 2>/dev/null | wc -l || true)"
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

new_process_report_files() {
  if [[ ! -d "${MONITOR_DIR}" ]]; then
    return 0
  fi

  find "${MONITOR_DIR}" -type f -name "event_*_process_crash_*_final_llm_report.md" -newer "${BASELINE_MARKER}" 2>/dev/null | sort
}

new_container_report_files() {
  if [[ ! -d "${MONITOR_DIR}" ]]; then
    return 0
  fi

  find "${MONITOR_DIR}" -type f -name "event_*_container_k8s_*_final_llm_report.md" -newer "${BASELINE_MARKER}" 2>/dev/null | sort
}

new_process_alert_files() {
  if [[ ! -d "${ALERT_ARCHIVE_DIR}" ]]; then
    return 0
  fi

  find "${ALERT_ARCHIVE_DIR}" -type f -name "*_process_crash_*.md" -newer "${BASELINE_MARKER}" 2>/dev/null | sort
}

new_container_alert_files() {
  if [[ ! -d "${ALERT_ARCHIVE_DIR}" ]]; then
    return 0
  fi

  find "${ALERT_ARCHIVE_DIR}" -type f -name "*_container_k8s_*.md" -newer "${BASELINE_MARKER}" 2>/dev/null | sort
}

smoke_mentions_for_process() {
  local monitor_count
  local alert_count

  monitor_count="$(safe_grep_file_count "${SMOKE_ID}" "${MONITOR_DIR}")"
  alert_count="$(safe_grep_file_count "${SMOKE_ID}" "${ALERT_ARCHIVE_DIR}")"

  echo "$((monitor_count + alert_count))"
}

smoke_mentions_for_container() {
  local monitor_count
  local alert_count

  monitor_count="$(safe_grep_file_count "${SMOKE_ID}" "${MONITOR_DIR}")"
  alert_count="$(safe_grep_file_count "${SMOKE_ID}" "${ALERT_ARCHIVE_DIR}")"

  echo "$((monitor_count + alert_count))"
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

  for i in $(seq 1 120); do
    printf '[R10_MULTI_EVENT_SMOKE_ID=%s][filler:%03d] normal heartbeat line service healthy queue nominal\n' "${SMOKE_ID}" "${i}" >> "${RUNTIME_LOG}"
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

bool_text() {
  if [[ "$1" == "1" ]]; then
    echo "yes"
  else
    echo "no"
  fi
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

cd "${PROJECT_ROOT}"
mkdir -p "${ARTIFACT_DIR}"
mkdir -p "$(dirname "${RUNTIME_LOG}")"

if ! initial_service_status="$(read_service_status 2>&1)"; then
  {
    printf '# R10 Multi-Event Live Smoke Summary\n\n'
    printf '## Service Status\n\n'
    printf -- '- service: `%s`\n' "${SERVICE_NAME}"
    printf -- '- systemctl_show_error: `%s`\n\n' "${initial_service_status//$'\n'/; }"
    printf '## Final Result\n\n'
    printf 'FAIL: unable to read service status. Please check the daemon manually; do not rerun this script automatically.\n'
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
  printf '# R10 Multi-Event Live Smoke Summary\n\n'
  printf '## Service Status\n\n'
  printf -- '- service: `%s`\n' "${SERVICE_NAME}"
  printf -- '- initial_ActiveState: `%s`\n' "${initial_active_state:-unknown}"
  printf -- '- initial_SubState: `%s`\n' "${initial_sub_state:-unknown}"
  printf -- '- initial_MainPID: `%s`\n' "${initial_main_pid:-unknown}"
  printf -- '- initial_ExecMainStatus: `%s`\n' "${initial_exec_main_status:-unknown}"
  printf -- '- initial_ActiveEnterTimestamp: `%s`\n\n' "${initial_active_enter_timestamp:-unknown}"
  printf '## Smoke ID\n\n'
  printf -- '- smoke_id: `%s`\n\n' "${SMOKE_ID}"
} > "${SUMMARY_PATH}"

info "Service status: ActiveState=${initial_active_state:-unknown} SubState=${initial_sub_state:-unknown} MainPID=${initial_main_pid:-unknown}"

if [[ "${initial_active_state}" != "active" || "${initial_sub_state}" != "running" ]]; then
  warn "Service is not active/running. Please manually restart agentic-monitor@enterprise_demo_local.service and rerun later."
  {
    printf '## Final Result\n\n'
    printf 'FAIL: service is not active/running. Manual action required; this script did not run sudo or restart systemd.\n'
  } >> "${SUMMARY_PATH}"
  exit 1
fi

baseline_daemon_problem_count="$(daemon_problem_count)"
baseline_dangerous_execution_count="$(dangerous_execution_count)"

info "Appending benign filler for ${SMOKE_ID}"
append_filler
info "Waiting 20 seconds after benign filler"
sleep 20

touch "${BASELINE_MARKER}"

baseline_process_reports="$(process_report_count)"
baseline_container_reports="$(container_report_count)"
baseline_process_alerts="$(process_alert_count)"
baseline_container_alerts="$(container_alert_count)"
baseline_alert_jsonl_lines="$(safe_jsonl_line_count "${ALERT_JSONL}")"

{
  printf '## Baseline Counts\n\n'
  printf -- '- process_crash_reports: `%s`\n' "${baseline_process_reports}"
  printf -- '- container_k8s_reports: `%s`\n' "${baseline_container_reports}"
  printf -- '- process_crash_alerts: `%s`\n' "${baseline_process_alerts}"
  printf -- '- container_k8s_alerts: `%s`\n' "${baseline_container_alerts}"
  printf -- '- alert_jsonl_lines: `%s`\n' "${baseline_alert_jsonl_lines}"
  printf -- '- daemon_problem_count: `%s`\n' "${baseline_daemon_problem_count}"
  printf -- '- dangerous_execution_count: `%s`\n\n' "${baseline_dangerous_execution_count}"
} >> "${SUMMARY_PATH}"

info "Injecting combined process_crash + container_k8s window for ${SMOKE_ID}"
append_combined_events

process_pass=0
container_pass=0

for elapsed in $(seq 5 5 150); do
  sleep 5

  current_process_reports="$(process_report_count)"
  current_container_reports="$(container_report_count)"
  current_process_alerts="$(process_alert_count)"
  current_container_alerts="$(container_alert_count)"

  if (( current_process_reports > baseline_process_reports || current_process_alerts > baseline_process_alerts )); then
    process_pass=1
  fi
  if (( current_container_reports > baseline_container_reports || current_container_alerts > baseline_container_alerts )); then
    container_pass=1
  fi

  info "Waiting R10 multi-event: ${elapsed}/150s process_reports=${current_process_reports} process_alerts=${current_process_alerts} container_reports=${current_container_reports} container_alerts=${current_container_alerts}"

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

new_process_reports="$(new_process_report_files || true)"
new_container_reports="$(new_container_report_files || true)"
new_process_alerts="$(new_process_alert_files || true)"
new_container_alerts="$(new_container_alert_files || true)"

process_mentions="$(smoke_mentions_for_process)"
container_mentions="$(smoke_mentions_for_container)"
process_only_raw="no"
container_only_raw="no"

if (( process_pass == 0 && process_mentions > 0 )); then
  process_only_raw="yes"
fi
if (( container_pass == 0 && container_mentions > 0 )); then
  container_only_raw="yes"
fi

daemon_stable="yes"
if (( final_daemon_problem_count > baseline_daemon_problem_count )); then
  daemon_stable="no"
fi
if [[ "${final_active_state}" != "active" || "${final_sub_state}" != "running" ]]; then
  daemon_stable="no"
fi

dangerous_operation="no"
if (( final_dangerous_execution_count > baseline_dangerous_execution_count )); then
  dangerous_operation="yes"
fi

final_result="PARTIAL"
if [[ "${daemon_stable}" == "no" || "${dangerous_operation}" == "yes" ]]; then
  final_result="FAIL"
elif (( process_pass == 1 && container_pass == 1 )); then
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

  printf '## Event Results\n\n'
  printf -- '- process_crash_independent_report_or_alert: `%s`\n' "$(bool_text "${process_pass}")"
  printf -- '- container_k8s_independent_report_or_alert: `%s`\n' "$(bool_text "${container_pass}")"
  printf -- '- process_crash_only_seen_in_raw_evidence: `%s`\n' "${process_only_raw}"
  printf -- '- container_k8s_only_seen_in_raw_evidence: `%s`\n\n' "${container_only_raw}"

  printf '## Final Service Status\n\n'
  printf -- '- final_ActiveState: `%s`\n' "${final_active_state:-unknown}"
  printf -- '- final_SubState: `%s`\n' "${final_sub_state:-unknown}"
  printf -- '- final_MainPID: `%s`\n' "${final_main_pid:-unknown}"
  printf -- '- final_ExecMainStatus: `%s`\n' "${final_exec_main_status:-unknown}"
  printf -- '- final_ActiveEnterTimestamp: `%s`\n\n' "${final_active_enter_timestamp:-unknown}"

  printf '## Safety Checks\n\n'
  printf -- '- daemon_stable: `%s`\n' "${daemon_stable}"
  printf -- '- dangerous_auto_operation_detected: `%s`\n\n' "${dangerous_operation}"
} >> "${SUMMARY_PATH}"

write_file_list "New process_crash reports" "${new_process_reports}"
write_file_list "New container_k8s reports" "${new_container_reports}"
write_file_list "New process_crash alerts" "${new_process_alerts}"
write_file_list "New container_k8s alerts" "${new_container_alerts}"

{
  printf '## Final Result\n\n'
  printf '%s\n' "${final_result}"
} >> "${SUMMARY_PATH}"

info "Summary written to ${SUMMARY_PATH}"
info "R10-4 result: ${final_result}"

if [[ "${final_result}" == "FAIL" ]]; then
  exit 1
fi
