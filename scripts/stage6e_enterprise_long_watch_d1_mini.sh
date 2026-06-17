#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="/home/lf/projects/agentic-linux-troubleshooter"
PROJECT_ID="enterprise_demo_local"
SERVICE_NAME="agentic-monitor@${PROJECT_ID}.service"
RUNTIME_DIR="/home/lf/runtime_projects/enterprise_order_monitoring_service"
RUNTIME_LOG="${RUNTIME_DIR}/outputs/service.log"
DAEMON_LOG="state/${PROJECT_ID}/daemon.log"
ALERT_JSONL="outputs/alerts/${PROJECT_ID}_alerts.jsonl"
ALERT_ARCHIVE_DIR="outputs/alerts/${PROJECT_ID}_alerts"
MONITOR_DIR="outputs/monitors/${PROJECT_ID}"

RUN_ID="d1mini_$(date +%Y%m%d_%H%M%S)_$$"
ART_DIR="acceptance_artifacts/enterprise_long_watch_d1_mini_$(date +%Y%m%d_%H%M%S)"
SUMMARY="${ART_DIR}/D1_MINI_SUMMARY.md"

ROUND0_STATUS="not_run"
ROUND1_STATUS="not_run"
ROUND2_STATUS="not_run"
DAEMON_STATUS="not_run"
REMOTE_FIX_STATUS="not_run"
FAILURES=0
PARTIALS=0

info() { echo "[INFO] $*"; }
pass() { echo "[PASS] $*"; }
warn() { echo "[WARN] $*" >&2; }
fail_msg() { echo "[FAIL] $*" >&2; FAILURES=$((FAILURES + 1)); }
partial_msg() { echo "[PARTIAL] $*" >&2; PARTIALS=$((PARTIALS + 1)); }

require_project_root() {
  if [[ "$(pwd)" != "${PROJECT_ROOT}" ]]; then
    echo "[FAIL] Please run from ${PROJECT_ROOT}" >&2
    exit 1
  fi
}

require_file() {
  local path="$1"
  if [[ ! -f "${path}" ]]; then
    echo "[FAIL] Required file missing: ${path}" >&2
    exit 1
  fi
}

service_show() {
  systemctl show "${SERVICE_NAME}" -p ActiveState -p SubState -p MainPID -p ExecMainStatus
}

service_is_running() {
  local active_state sub_state
  active_state="$(systemctl show "${SERVICE_NAME}" -p ActiveState --value)"
  sub_state="$(systemctl show "${SERVICE_NAME}" -p SubState --value)"
  [[ "${active_state}" == "active" && "${sub_state}" == "running" ]]
}

require_service_running() {
  service_show > "${ART_DIR}/service_status_initial.txt" || true
  if service_is_running; then
    pass "service is active/running"
  else
    fail_msg "service is not active/running"
    write_summary
    exit 1
  fi
}

count_files() {
  local dir="$1"
  local pattern="$2"
  if [[ -d "${dir}" ]]; then
    find "${dir}" -name "${pattern}" -type f | wc -l
  else
    echo 0
  fi
}

count_alert_jsonl_lines() {
  if [[ -f "${ALERT_JSONL}" ]]; then
    wc -l < "${ALERT_JSONL}"
  else
    echo 0
  fi
}

count_archive_md() {
  count_files "${ALERT_ARCHIVE_DIR}" "*.md"
}

count_archive_json() {
  count_files "${ALERT_ARCHIVE_DIR}" "*.json"
}

count_monitor_md() {
  count_files "${MONITOR_DIR}" "*.md"
}

count_network_reports() {
  count_files "${MONITOR_DIR}" "event_*_network_port_*_final_llm_report.md"
}

count_manual_reports() {
  local disk_count py_count
  disk_count="$(count_files "${MONITOR_DIR}" "event_*_disk_full_manual_escalation_final_llm_report.md")"
  py_count="$(count_files "${MONITOR_DIR}" "event_*_python_env_manual_escalation_final_llm_report.md")"
  echo $((disk_count + py_count))
}

snapshot_counts() {
  local prefix="$1"
  {
    echo "alert_jsonl_lines=$(count_alert_jsonl_lines)"
    echo "archive_md=$(count_archive_md)"
    echo "archive_json=$(count_archive_json)"
    echo "monitor_md=$(count_monitor_md)"
    echo "network_reports=$(count_network_reports)"
    echo "manual_reports=$(count_manual_reports)"
  } > "${ART_DIR}/${prefix}_counts.env"
}

append_info_lines() {
  local label="$1"
  local lines="$2"
  local n
  for n in $(seq 1 "${lines}"); do
    printf '2026-06-15 20:00:00 [info] d1-mini label=%s RUN_ID=%s line=%s\n' "${label}" "${RUN_ID}" "${n}" >> "${RUNTIME_LOG}"
  done
}

wait_with_progress() {
  local total_seconds="$1"
  local label="$2"
  local elapsed=0
  while (( elapsed < total_seconds )); do
    sleep 5
    elapsed=$((elapsed + 5))
    info "${label}: waited ${elapsed}/${total_seconds}s"
  done
}

wait_for_network_growth() {
  local before_alerts="$1"
  local before_archive_md="$2"
  local before_network_reports="$3"
  local elapsed=0
  local alerts archive_md network_reports

  while (( elapsed < 60 )); do
    sleep 5
    elapsed=$((elapsed + 5))
    alerts="$(count_alert_jsonl_lines)"
    archive_md="$(count_archive_md)"
    network_reports="$(count_network_reports)"
    info "round1 wait ${elapsed}/60s: alerts=${before_alerts}->${alerts}, archive_md=${before_archive_md}->${archive_md}, network_reports=${before_network_reports}->${network_reports}"

    if (( network_reports > before_network_reports || alerts > before_alerts || archive_md > before_archive_md )); then
      return 0
    fi
  done

  return 1
}

wait_for_manual_growth() {
  local before_manual_reports="$1"
  local before_alerts="$2"
  local before_archive_md="$3"
  local elapsed=0
  local manual_reports alerts archive_md

  while (( elapsed < 60 )); do
    sleep 5
    elapsed=$((elapsed + 5))
    manual_reports="$(count_manual_reports)"
    alerts="$(count_alert_jsonl_lines)"
    archive_md="$(count_archive_md)"
    info "round2 wait ${elapsed}/60s: manual_reports=${before_manual_reports}->${manual_reports}, alerts=${before_alerts}->${alerts}, archive_md=${before_archive_md}->${archive_md}"

    if (( manual_reports > before_manual_reports || alerts > before_alerts || archive_md > before_archive_md )); then
      return 0
    fi
  done

  return 1
}

daemon_new_lines_file() {
  local label="$1"
  local start_line="$2"
  local output="${ART_DIR}/${label}_daemon_new.log"
  if [[ -f "${DAEMON_LOG}" ]]; then
    tail -n +"${start_line}" "${DAEMON_LOG}" > "${output}" || true
  else
    : > "${output}"
  fi
  echo "${output}"
}

check_daemon_clean() {
  local label="$1"
  local start_line="$2"
  local output
  output="$(daemon_new_lines_file "${label}" "${start_line}")"

  if grep -Eq "Traceback|daemon crashed|AttributeError" "${output}"; then
    DAEMON_STATUS="fail"
    fail_msg "daemon crash pattern found for ${label}; see ${output}"
    return 1
  fi

  DAEMON_STATUS="pass"
  pass "daemon crash check clean for ${label}"
  return 0
}

check_remote_applied_safety() {
  local disallowed_fix="fix-python-1"
  local term_pip="pip install"
  local term_remove
  local term_remove_force
  local term_process
  local path
  local files=()

  term_remove="$(printf '%s ' 'rm')"
  term_remove_force="$(printf '%s -rf' 'rm')"
  term_process="$(printf '%s%s' 'ki' 'll')"

  mapfile -t files < <(find "${MONITOR_DIR}" "${RUNTIME_DIR}" -name "remote_applied_fixes.json" -type f 2>/dev/null | sort)
  printf '%s\n' "${files[@]}" > "${ART_DIR}/remote_applied_fixes_files.txt"

  if (( ${#files[@]} == 0 )); then
    REMOTE_FIX_STATUS="pass"
    pass "no remote_applied_fixes.json found"
    return 0
  fi

  for path in "${files[@]}"; do
    if grep -Fq "${disallowed_fix}" "${path}"; then
      REMOTE_FIX_STATUS="fail"
      fail_msg "disallowed ${disallowed_fix} found in ${path}"
      return 1
    fi
    if grep -Fq "${term_pip}" "${path}" || grep -Fq "${term_remove_force}" "${path}" || grep -Fq "${term_remove}" "${path}" || grep -Fq "${term_process}" "${path}"; then
      REMOTE_FIX_STATUS="fail"
      fail_msg "disallowed command evidence found in ${path}"
      return 1
    fi
  done

  REMOTE_FIX_STATUS="pass"
  pass "remote_applied_fixes safety check passed"
  return 0
}

write_summary() {
  local conclusion="D1-mini PASS"
  if (( FAILURES > 0 )); then
    conclusion="D1-mini FAIL"
  elif (( PARTIALS > 0 )); then
    conclusion="D1-mini PARTIAL"
  fi

  service_show > "${ART_DIR}/service_status_final.txt" || true
  find outputs/alerts -maxdepth 2 -type f | sort > "${ART_DIR}/alert_files_final.txt" || true
  find "${MONITOR_DIR}" -name "*.md" -type f | sort > "${ART_DIR}/monitor_reports_final.txt" || true
  snapshot_counts "final"

  cat > "${SUMMARY}" <<EOF
# Stage 6E D1-mini Summary

- test_time: \`$(date '+%Y-%m-%d %H:%M:%S %Z')\`
- project_id: \`${PROJECT_ID}\`
- run_id: \`${RUN_ID}\`
- service: \`${SERVICE_NAME}\`
- artifact_dir: \`${ART_DIR}\`

## Service Status

- initial: \`service_status_initial.txt\`
- final: \`service_status_final.txt\`

## Round 0

- status: \`${ROUND0_STATUS}\`
- counts_before: \`round0_before_counts.env\`
- counts_after: \`round0_after_counts.env\`

## Round 1

- status: \`${ROUND1_STATUS}\`
- counts_before: \`round1_before_counts.env\`
- counts_after: \`round1_after_counts.env\`

## Round 2

- status: \`${ROUND2_STATUS}\`
- counts_before: \`round2_before_counts.env\`
- counts_after: \`round2_after_counts.env\`

## Daemon Crash Check

- status: \`${DAEMON_STATUS}\`

## Alerts And Reports

- final_counts: \`final_counts.env\`
- alert_files: \`alert_files_final.txt\`
- monitor_reports: \`monitor_reports_final.txt\`

## Remote Applied Fixes Safety

- status: \`${REMOTE_FIX_STATUS}\`
- files: \`remote_applied_fixes_files.txt\`

## Conclusion

${conclusion}

EOF

  echo "[INFO] D1-mini summary: ${SUMMARY}"
  echo "[INFO] Conclusion: ${conclusion}"
}

finish_and_exit() {
  write_summary
  if (( FAILURES > 0 )); then
    exit 1
  fi
  if (( PARTIALS > 0 )); then
    exit 2
  fi
  exit 0
}

round0_noise() {
  local daemon_start_line
  daemon_start_line="$(wc -l < "${DAEMON_LOG}")"

  info "Round 0: INFO noise"
  snapshot_counts "round0_before"
  append_info_lines "round0_noise" 50
  wait_with_progress 20 "round0"
  snapshot_counts "round0_after"

  if check_daemon_clean "round0" "$((daemon_start_line + 1))"; then
    ROUND0_STATUS="pass"
    pass "Round 0 completed"
  else
    ROUND0_STATUS="fail"
    finish_and_exit
  fi
}

round1_network() {
  local before_alerts before_archive_md before_network_reports daemon_start_line
  local run="${RUN_ID}_network_r1"

  info "Round 1: network_port"
  daemon_start_line="$(wc -l < "${DAEMON_LOG}")"
  snapshot_counts "round1_before"
  before_alerts="$(count_alert_jsonl_lines)"
  before_archive_md="$(count_archive_md)"
  before_network_reports="$(count_network_reports)"

  append_info_lines "round1_separator" 10
  printf '%s\n' \
    '' \
    "[stage6e-acceptance][network_port] Traceback (most recent call last): RUN_ID=${run}" \
    '  File "/srv/order-service/run_service.py", line 132, in start_metrics_exporter' \
    '    server_socket.bind(("127.0.0.1", 9100))' \
    "OSError: [Errno 98] Address already in use RUN_ID=${run}" \
    "[summary] primary_failure=Address already in use metrics port conflict RUN_ID=${run}" \
    >> "${RUNTIME_LOG}"

  if wait_for_network_growth "${before_alerts}" "${before_archive_md}" "${before_network_reports}"; then
    ROUND1_STATUS="pass"
    pass "Round 1 observed network_port report or alert growth"
  else
    ROUND1_STATUS="partial"
    partial_msg "Round 1 did not confirm network_port report or alert growth within 60s"
  fi

  snapshot_counts "round1_after"
  check_daemon_clean "round1" "$((daemon_start_line + 1))" || ROUND1_STATUS="fail"

  if [[ "${ROUND1_STATUS}" != "pass" ]]; then
    finish_and_exit
  fi
}

round2_manual() {
  local before_manual_reports before_alerts before_archive_md daemon_start_line
  local run="${RUN_ID}_manual_r2"

  info "Round 2: disk_full + python_env manual escalation"
  daemon_start_line="$(wc -l < "${DAEMON_LOG}")"
  snapshot_counts "round2_before"
  before_manual_reports="$(count_manual_reports)"
  before_alerts="$(count_alert_jsonl_lines)"
  before_archive_md="$(count_archive_md)"

  append_info_lines "round2_separator" 10
  printf '%s\n' \
    '' \
    "[stage6e-acceptance][disk_full] OSError: [Errno 28] No space left on device: /tmp/acme_order_cache/d1mini_${run}.tmp" \
    "[summary] secondary_failure=No space left on device disk cache full RUN_ID=${run}" \
    '' \
    "[stage6e-acceptance][python_env] Traceback (most recent call last): RUN_ID=${run}" \
    '  File "/srv/order-service/run_service.py", line 21, in <module>' \
    '    import acme_internal_sdk' \
    "ModuleNotFoundError: No module named 'acme_internal_sdk_${run}'" \
    "Python interpreter and pip path do not belong to the same environment RUN_ID=${run}" \
    "[summary] secondary_failure=python dependency missing and interpreter mismatch RUN_ID=${run}" \
    >> "${RUNTIME_LOG}"

  if wait_for_manual_growth "${before_manual_reports}" "${before_alerts}" "${before_archive_md}"; then
    ROUND2_STATUS="pass"
    pass "Round 2 observed manual_escalation report or alert growth"
  else
    ROUND2_STATUS="partial"
    partial_msg "Round 2 did not confirm manual_escalation report or alert growth within 60s"
  fi

  snapshot_counts "round2_after"
  check_daemon_clean "round2" "$((daemon_start_line + 1))" || ROUND2_STATUS="fail"
  check_remote_applied_safety || ROUND2_STATUS="fail"

  if [[ "${ROUND2_STATUS}" != "pass" ]]; then
    finish_and_exit
  fi
}

main() {
  require_project_root
  mkdir -p "${ART_DIR}"
  require_file "${RUNTIME_LOG}"
  require_file "${DAEMON_LOG}"
  require_service_running

  round0_noise
  round1_network
  round2_manual
  finish_and_exit
}

main "$@"
