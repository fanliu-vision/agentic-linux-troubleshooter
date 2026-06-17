#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="/home/lf/projects/agentic-linux-troubleshooter"
PROJECT_ID="enterprise_demo_local"
SERVICE_NAME="agentic-monitor@${PROJECT_ID}.service"
RUNTIME_LOG="/home/lf/runtime_projects/enterprise_order_monitoring_service/outputs/service.log"
MONITOR_DIR="${PROJECT_ROOT}/outputs/monitors/${PROJECT_ID}"
ALERT_ARCHIVE_DIR="${PROJECT_ROOT}/outputs/alerts/${PROJECT_ID}_alerts"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
SMOKE_ID="R9_CONTAINER_K8S_ISOLATED_${TIMESTAMP}"
ARTIFACT_DIR="${PROJECT_ROOT}/acceptance_artifacts/container_k8s_isolated_smoke_r9_${TIMESTAMP}"
SUMMARY_PATH="${ARTIFACT_DIR}/R9_CONTAINER_K8S_ISOLATED_SUMMARY.md"

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
  echo "${count:-0}"
}

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

container_report_count() {
  safe_file_count "${MONITOR_DIR}" "event_*_container_k8s_*_final_llm_report.md"
}

container_alert_count() {
  safe_file_count "${ALERT_ARCHIVE_DIR}" "*_container_k8s_*.md"
}

container_any_evidence_count() {
  local monitor_count
  local alert_count
  monitor_count="$(safe_grep_count "container_k8s" "${MONITOR_DIR}")"
  alert_count="$(safe_grep_count "container_k8s" "${ALERT_ARCHIVE_DIR}")"
  echo "$((monitor_count + alert_count))"
}

daemon_problem_count() {
  safe_grep_count "Traceback\\|daemon crashed\\|AttributeError" "${PROJECT_ROOT}/state/${PROJECT_ID}/daemon.log"
}

dangerous_evidence_count() {
  if [[ ! -d "${MONITOR_DIR}" ]]; then
    echo "0"
    return 0
  fi

  local count
  count="$(find "${MONITOR_DIR}" -name "remote_applied_fixes.json" -type f -print0 2>/dev/null \
    | xargs -0 grep -EIl "rm -rf|pip install|systemctl restart|kubectl" 2>/dev/null \
    | wc -l || true)"
  echo "${count:-0}"
}

append_filler() {
  local i
  for i in $(seq 1 120); do
    printf '[%s][filler:%03d] normal heartbeat info line service healthy queue depth nominal\n' "${SMOKE_ID}" "${i}" >> "${RUNTIME_LOG}"
  done
}

append_container_k8s() {
  {
    printf '\n'
    printf '[%s][container_k8s]\n' "${SMOKE_ID}"
    printf 'Warning BackOff pod/acme-k8s-only-api Back-off restarting failed container acme-k8s-only-api\n'
    printf 'Warning Failed pod/acme-k8s-only-api Error: ImagePullBackOff\n'
    printf 'Last State: Terminated Reason: OOMKilled\n'
    printf 'Warning Failed pod/acme-k8s-only-api CreateContainerConfigError\n'
  } >> "${RUNTIME_LOG}"
}

cd "${PROJECT_ROOT}"
mkdir -p "${ARTIFACT_DIR}"
mkdir -p "$(dirname "${RUNTIME_LOG}")"

service_status="$(systemctl show "${SERVICE_NAME}" \
  -p ActiveState \
  -p SubState \
  -p MainPID \
  -p ExecMainStatus)"

active_state="$(printf '%s\n' "${service_status}" | awk -F= '$1 == "ActiveState" {print $2}')"
sub_state="$(printf '%s\n' "${service_status}" | awk -F= '$1 == "SubState" {print $2}')"
main_pid="$(printf '%s\n' "${service_status}" | awk -F= '$1 == "MainPID" {print $2}')"
exec_main_status="$(printf '%s\n' "${service_status}" | awk -F= '$1 == "ExecMainStatus" {print $2}')"

{
  printf '# R9 Container K8s Isolated Summary\n\n'
  printf '## Service Status\n\n'
  printf -- '- service: `%s`\n' "${SERVICE_NAME}"
  printf -- '- ActiveState: `%s`\n' "${active_state:-unknown}"
  printf -- '- SubState: `%s`\n' "${sub_state:-unknown}"
  printf -- '- MainPID: `%s`\n' "${main_pid:-unknown}"
  printf -- '- ExecMainStatus: `%s`\n\n' "${exec_main_status:-unknown}"
} > "${SUMMARY_PATH}"

if [[ "${active_state}" != "active" || "${sub_state}" != "running" ]]; then
  warn "Service is not active/running. Please handle service state manually."
  {
    printf '## Final Result\n\n'
    printf 'FAIL: service is not active/running.\n'
  } >> "${SUMMARY_PATH}"
  exit 1
fi

baseline_reports="$(container_report_count)"
baseline_alerts="$(container_alert_count)"
baseline_evidence="$(container_any_evidence_count)"

info "Appending benign filler for ${SMOKE_ID}"
append_filler
info "Waiting 20 seconds after filler"
sleep 20

info "Injecting isolated container_k8s for ${SMOKE_ID}"
append_container_k8s

status="PARTIAL"
for elapsed in 5 10 15 20 25 30 35 40 45 50 55 60 65 70 75 80 85 90 95 100 105 110 115 120; do
  sleep 5
  current_reports="$(container_report_count)"
  current_alerts="$(container_alert_count)"
  info "Waiting container_k8s isolated: ${elapsed}/120s reports=${current_reports} alerts=${current_alerts}"

  if (( current_reports > baseline_reports || current_alerts > baseline_alerts )); then
    status="PASS"
    break
  fi
done

final_reports="$(container_report_count)"
final_alerts="$(container_alert_count)"
final_evidence="$(container_any_evidence_count)"
daemon_problem_count_value="$(daemon_problem_count)"
dangerous_evidence_count_value="$(dangerous_evidence_count)"

if (( daemon_problem_count_value > 0 || dangerous_evidence_count_value > 0 )); then
  status="FAIL"
fi

{
  printf '## Container K8s Isolated Result\n\n'
  printf -- '- smoke_id: `%s`\n' "${SMOKE_ID}"
  printf -- '- baseline_reports: `%s`\n' "${baseline_reports}"
  printf -- '- final_reports: `%s`\n' "${final_reports}"
  printf -- '- baseline_alerts: `%s`\n' "${baseline_alerts}"
  printf -- '- final_alerts: `%s`\n' "${final_alerts}"
  printf -- '- baseline_any_evidence: `%s`\n' "${baseline_evidence}"
  printf -- '- final_any_evidence: `%s`\n' "${final_evidence}"
  printf -- '- daemon_problem_count: `%s`\n' "${daemon_problem_count_value}"
  printf -- '- dangerous_evidence_count: `%s`\n' "${dangerous_evidence_count_value}"
  printf -- '- final_status: `%s`\n\n' "${status}"
  printf '## Final Result\n\n'
  printf '%s\n' "${status}"
} >> "${SUMMARY_PATH}"

info "Summary written to ${SUMMARY_PATH}"
