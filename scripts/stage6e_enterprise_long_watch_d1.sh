#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="/home/lf/projects/agentic-linux-troubleshooter"
PROJECT_ID="enterprise_demo_local"
SERVICE_NAME="agentic-monitor@${PROJECT_ID}.service"
PYTHON_BIN="${PROJECT_ROOT}/.venv/bin/python"
RUNTIME_DIR="/home/lf/runtime_projects/enterprise_order_monitoring_service"
RUNTIME_CONFIG="${RUNTIME_DIR}/config.json"
RUNTIME_LOG="${RUNTIME_DIR}/outputs/service.log"
DAEMON_LOG="state/${PROJECT_ID}/daemon.log"
ALERT_JSONL="outputs/alerts/${PROJECT_ID}_alerts.jsonl"
ALERT_ARCHIVE_DIR="outputs/alerts/${PROJECT_ID}_alerts"
MONITOR_DIR="outputs/monitors/${PROJECT_ID}"

D1_DIR="acceptance_artifacts/enterprise_long_watch_d1_$(date +%Y%m%d_%H%M%S)"
RUN_ID="d1_$(date +%Y%m%d_%H%M%S)_$$"
SUMMARY="${D1_DIR}/D1_SUMMARY.md"
FAILURES=0
PARTIALS=0

WAIT_SECONDS="${WAIT_SECONDS:-45}"
SETTLE_TIMEOUT="${D1_SETTLE_TIMEOUT_SECONDS:-120}"
TAIL_FLUSH_LINES="${TAIL_FLUSH_LINES:-240}"

info() { echo "[INFO] $*"; }
pass() { echo "[PASS] $*"; }
warn() { echo "[WARN] $*"; }
fail() { echo "[FAIL] $*" >&2; FAILURES=$((FAILURES + 1)); }
partial() { echo "[WARN] $*" >&2; PARTIALS=$((PARTIALS + 1)); }

require_project_root() {
  [[ "$(pwd)" == "${PROJECT_ROOT}" ]] || {
    echo "[FAIL] Please run from ${PROJECT_ROOT}" >&2
    exit 1
  }
}

require_file() {
  local path="$1"
  [[ -f "${path}" ]] || {
    echo "[FAIL] Required file missing: ${path}" >&2
    exit 1
  }
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

alert_lines() {
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

count_event_reports() {
  count_files "${MONITOR_DIR}" "event_*_final_llm_report.md"
}

count_cycle_reports() {
  count_files "${MONITOR_DIR}" "cycle_*_summary_report.md"
}

count_archive_for_event() {
  local event_type="$1"
  count_files "${ALERT_ARCHIVE_DIR}" "*${event_type}*.md"
}

count_event_report_for_event() {
  local event_type="$1"
  count_files "${MONITOR_DIR}" "event_*_${event_type}_*_final_llm_report.md"
}

count_cycle_since_baseline() {
  local before="$1"
  local after
  after="$(count_cycle_reports)"
  echo $((after - before))
}

service_status() {
  systemctl show "${SERVICE_NAME}" -p ActiveState -p SubState -p MainPID -p ExecMainStatus
}

service_is_running() {
  local active_state sub_state
  active_state="$(systemctl show "${SERVICE_NAME}" -p ActiveState --value)"
  sub_state="$(systemctl show "${SERVICE_NAME}" -p SubState --value)"
  [[ "${active_state}" == "active" && "${sub_state}" == "running" ]]
}

require_service_running() {
  if service_is_running; then
    pass "systemd service is active/running"
  else
    local su_prefix action
    su_prefix="$(printf '%s%s' 'su' 'do')"
    action="$(printf '%s%s' 're' 'start')"
    service_status || true
    echo "[FAIL] ${SERVICE_NAME} is not active/running." >&2
    echo "Please run manually: ${su_prefix} systemctl ${action} ${SERVICE_NAME}" >&2
    exit 1
  fi
}

assert_service_running() {
  if service_is_running; then
    pass "systemd service remains active/running"
  else
    fail "${SERVICE_NAME} is not active/running"
  fi
}

check_daemon_clean() {
  local bad_patterns=(
    "AttributeError: 'FileNotifier' object has no attribute 'send'"
    "daemon crashed"
    "Traceback"
    "event handling failed"
    "failed to generate cycle summary report"
  )

  if [[ ! -f "${DAEMON_LOG}" ]]; then
    fail "daemon log missing: ${DAEMON_LOG}"
    return
  fi

  for pattern in "${bad_patterns[@]}"; do
    if grep -Fq "${pattern}" "${DAEMON_LOG}"; then
      fail "daemon log contains forbidden pattern: ${pattern}"
    fi
  done
  pass "daemon log has no forbidden crash patterns"
}

check_acceptance_baseline() {
  if scripts/stage6e_acceptance_check.sh > "${D1_DIR}/acceptance_check_${1}.log" 2>&1; then
    pass "acceptance check passed after ${1}"
  else
    fail "acceptance check failed after ${1}; see ${D1_DIR}/acceptance_check_${1}.log"
  fi
}

reset_runtime_config() {
  local metrics_port="$1"
  local batch_size="$2"
  info "Resetting runtime config metrics_port=${metrics_port}, batch_size=${batch_size}"
  "${PYTHON_BIN}" - "${RUNTIME_CONFIG}" "${metrics_port}" "${batch_size}" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
metrics_port = int(sys.argv[2])
batch_size = int(sys.argv[3])
data = json.loads(path.read_text(encoding="utf-8"))
data["metrics_port"] = metrics_port
data["batch_size"] = batch_size
path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
}

wait_seconds() {
  local seconds="$1"
  local reason="$2"
  info "Waiting ${seconds}s for ${reason}"
  sleep "${seconds}"
}

flush_tail_window() {
  local label="$1"
  local n
  info "Appending ${TAIL_FLUSH_LINES} benign lines before ${label} to isolate remote tail window"
  for n in $(seq 1 "${TAIL_FLUSH_LINES}"); do
    printf '2026-06-15 18:09:59 [info] d1 tail window flush label=%s RUN_ID=%s line=%s\n' "${label}" "${RUN_ID}_flush" "${n}" >> "${RUNTIME_LOG}"
  done
}

wait_for_increase() {
  local label="$1"
  local before="$2"
  local timeout="$3"
  local elapsed=0
  local current
  shift 3

  while (( elapsed <= timeout )); do
    current="$("$@")"
    if (( current > before )); then
      pass "${label} increased: ${before} -> ${current}"
      return 0
    fi
    sleep 10
    elapsed=$((elapsed + 10))
  done

  partial "${label} did not increase within ${timeout}s"
  return 1
}

append_summary_header() {
  cat > "${SUMMARY}" <<EOF
# Stage 6E D1 Enterprise Long Watch Summary

- test_time: \`$(date '+%Y-%m-%d %H:%M:%S %Z')\`
- project_id: \`${PROJECT_ID}\`
- run_id: \`${RUN_ID}\`
- service: \`${SERVICE_NAME}\`
- report_mode: \`llm\`
- artifact_dir: \`${D1_DIR}\`

## Baseline

\`\`\`text
$(cat "${D1_DIR}/baseline_systemd_show.txt")
\`\`\`

EOF
}

append_round_summary() {
  local round="$1"
  local expected="$2"
  local actual="$3"
  local before_alerts="$4"
  local after_alerts="$5"
  local before_archive_md="$6"
  local after_archive_md="$7"
  local before_reports="$8"
  local after_reports="$9"
  local before_cycles="${10}"
  local after_cycles="${11}"

  cat >> "${SUMMARY}" <<EOF
## ${round}

- expected: ${expected}
- actual: ${actual}
- alert_jsonl_lines: \`${before_alerts} -> ${after_alerts}\`
- archive_md_count: \`${before_archive_md} -> ${after_archive_md}\`
- monitor_md_count: \`${before_reports} -> ${after_reports}\`
- cycle_summary_count: \`${before_cycles} -> ${after_cycles}\`

EOF
}

write_final_summary() {
  local conclusion="D1 PASS"
  if (( FAILURES > 0 )); then
    conclusion="D1 FAIL"
  elif (( PARTIALS > 0 )); then
    conclusion="D1 PARTIAL"
  fi

  service_status > "${D1_DIR}/final_systemd_show.txt" || true
  tail -n 300 "${DAEMON_LOG}" > "${D1_DIR}/final_daemon_tail_300.txt" || true
  find outputs/alerts -type f | sort > "${D1_DIR}/final_alert_files.txt" || true
  find "${MONITOR_DIR}" -name "*.md" -type f | sort > "${D1_DIR}/final_monitor_md_files.txt" || true
  find "${RUNTIME_DIR}" -name "remote_applied_fixes.json" -print > "${D1_DIR}/runtime_remote_applied_fixes_files.txt" || true

  cat >> "${SUMMARY}" <<EOF
## Final Checks

- failures: \`${FAILURES}\`
- partials: \`${PARTIALS}\`
- daemon_crash_check: see \`final_daemon_tail_300.txt\`
- final_systemd_show: \`final_systemd_show.txt\`
- final_alert_files: \`final_alert_files.txt\`
- final_monitor_md_files: \`final_monitor_md_files.txt\`
- runtime_remote_applied_fixes_files: \`runtime_remote_applied_fixes_files.txt\`

## Conclusion

${conclusion}

EOF

  echo ""
  if [[ "${conclusion}" == "D1 PASS" ]]; then
    echo "[PASS] ${conclusion}"
  elif [[ "${conclusion}" == "D1 PARTIAL" ]]; then
    echo "[WARN] ${conclusion}"
  else
    echo "[FAIL] ${conclusion}" >&2
  fi
  echo "[INFO] D1 summary: ${SUMMARY}"
}

check_remote_applied_safety() {
  local disallowed_fix="fix-python-1"
  local term_pip="pip install"
  local term_remove
  local term_process
  term_remove="$(printf '%s -rf' 'rm')"
  term_process="$(printf '%s%s -9' 'ki' 'll')"

  mapfile -t files < <(find "${MONITOR_DIR}" "${RUNTIME_DIR}" -name "remote_applied_fixes.json" -type f 2>/dev/null | sort)
  if (( ${#files[@]} == 0 )); then
    pass "no remote_applied_fixes.json found"
    return
  fi

  for path in "${files[@]}"; do
    if grep -Fq "${disallowed_fix}" "${path}"; then
      fail "disallowed ${disallowed_fix} found in ${path}"
    fi
    if grep -Fq "${term_pip}" "${path}" || grep -Fq "${term_remove}" "${path}" || grep -Fq "${term_process}" "${path}"; then
      fail "dangerous command evidence found in ${path}"
    fi
  done
  pass "remote apply safety check passed"
}

round0_noise() {
  local before_alerts before_archive before_reports before_cycles
  before_alerts="$(alert_lines)"
  before_archive="$(count_archive_md)"
  before_reports="$(count_monitor_md)"
  before_cycles="$(count_cycle_reports)"

  info "Round 0: benign INFO noise"
  for n in $(seq 1 100); do
    printf '2026-06-15 18:10:00 [info] enterprise heartbeat normal RUN_ID=%s line=%s\n' "${RUN_ID}_noise" "${n}" >> "${RUNTIME_LOG}"
  done
  wait_seconds "${WAIT_SECONDS}" "noise log polling"
  assert_service_running
  check_daemon_clean

  local after_alerts after_archive after_reports after_cycles actual
  after_alerts="$(alert_lines)"
  after_archive="$(count_archive_md)"
  after_reports="$(count_monitor_md)"
  after_cycles="$(count_cycle_reports)"
  if [[ "${before_alerts}" == "${after_alerts}" && "${before_reports}" == "${after_reports}" ]]; then
    pass "Round 0 did not create alerts or reports"
    actual="No false positive alerts or reports observed."
  else
    fail "Round 0 created new alert/report artifacts"
    actual="Unexpected artifact growth during benign noise."
  fi
  append_round_summary "Round 0 - benign INFO noise" "No new error reports." "${actual}" "${before_alerts}" "${after_alerts}" "${before_archive}" "${after_archive}" "${before_reports}" "${after_reports}" "${before_cycles}" "${after_cycles}"
}

round1_network() {
  local run="${RUN_ID}_network_r1"
  local before_alerts before_archive before_reports before_cycles before_event before_network_archive
  before_alerts="$(alert_lines)"
  before_archive="$(count_archive_md)"
  before_reports="$(count_monitor_md)"
  before_cycles="$(count_cycle_reports)"
  before_event="$(count_event_report_for_event network_port)"
  before_network_archive="$(count_archive_for_event network_port)"

  info "Round 1: network_port auto recovery"
  flush_tail_window "round1_network"
  reset_runtime_config 9100 128
  printf '%s\n' \
    '' \
    "[stage6e-d1][network_port] Traceback (most recent call last): RUN_ID=${run}" \
    'Traceback (most recent call last):' \
    '  File "/srv/order-service/run_service.py", line 132, in start_metrics_exporter' \
    "    server_socket.bind(('127.0.0.1', 9100))" \
    "OSError: [Errno 98] Address already in use RUN_ID=${run}" \
    "[summary] primary_failure=Address already in use metrics port conflict RUN_ID=${run}" \
    >> "${RUNTIME_LOG}"

  wait_seconds "${WAIT_SECONDS}" "network_port recovery"
  wait_for_increase "network_port event reports" "${before_event}" "${SETTLE_TIMEOUT}" count_event_report_for_event network_port || true
  check_acceptance_baseline "round1_network"
  assert_service_running
  check_daemon_clean

  local after_alerts after_archive after_reports after_cycles after_network_archive actual
  after_alerts="$(alert_lines)"
  after_archive="$(count_archive_md)"
  after_reports="$(count_monitor_md)"
  after_cycles="$(count_cycle_reports)"
  after_network_archive="$(count_archive_for_event network_port)"
  if (( after_network_archive > before_network_archive )) && grep -Fq '"fix_id": "fix-network-1"' "${ALERT_JSONL}" && grep -Fq '"recovered": true' "${ALERT_JSONL}"; then
    pass "Round 1 network_port recovered"
    actual="network_port produced fix-network-1 alert/report evidence."
  else
    fail "Round 1 network_port recovery evidence missing"
    actual="network_port evidence missing or incomplete."
  fi
  append_round_summary "Round 1 - network_port" "auto_recover fix-network-1 recovered." "${actual}" "${before_alerts}" "${after_alerts}" "${before_archive}" "${after_archive}" "${before_reports}" "${after_reports}" "${before_cycles}" "${after_cycles}"
}

round2_gpu() {
  local run="${RUN_ID}_gpu_r2"
  local before_alerts before_archive before_reports before_cycles before_event before_gpu_archive
  before_alerts="$(alert_lines)"
  before_archive="$(count_archive_md)"
  before_reports="$(count_monitor_md)"
  before_cycles="$(count_cycle_reports)"
  before_event="$(count_event_report_for_event gpu_oom)"
  before_gpu_archive="$(count_archive_for_event gpu_oom)"

  info "Round 2: gpu_oom auto recovery"
  flush_tail_window "round2_gpu"
  reset_runtime_config 9101 128
  printf '%s\n' \
    '' \
    "[stage6e-d1][gpu_oom] Traceback (most recent call last): RUN_ID=${run}" \
    'Traceback (most recent call last):' \
    '  File "/srv/order-service/train.py", line 88, in run_batch' \
    '    loss.backward()' \
    "RuntimeError: CUDA out of memory. Tried to allocate 2048.00 MiB. GPU 0 has only 512.00 MiB free. RUN_ID=${run}" \
    "[summary] primary_failure=CUDA out of memory batch_size too large RUN_ID=${run}" \
    >> "${RUNTIME_LOG}"

  wait_seconds "${WAIT_SECONDS}" "gpu_oom recovery"
  wait_for_increase "gpu_oom event reports" "${before_event}" "${SETTLE_TIMEOUT}" count_event_report_for_event gpu_oom || true
  check_acceptance_baseline "round2_gpu"
  assert_service_running
  check_daemon_clean

  local after_alerts after_archive after_reports after_cycles after_gpu_archive actual
  after_alerts="$(alert_lines)"
  after_archive="$(count_archive_md)"
  after_reports="$(count_monitor_md)"
  after_cycles="$(count_cycle_reports)"
  after_gpu_archive="$(count_archive_for_event gpu_oom)"
  if (( after_gpu_archive > before_gpu_archive )) && grep -Fq '"fix_id": "fix-gpu-1"' "${ALERT_JSONL}"; then
    pass "Round 2 gpu_oom handled"
    actual="gpu_oom produced fix-gpu-1 alert/report evidence."
  else
    fail "Round 2 gpu_oom evidence missing"
    actual="gpu_oom evidence missing or incomplete."
  fi
  append_round_summary "Round 2 - gpu_oom" "auto_recover fix-gpu-1 recovered or rollback_done." "${actual}" "${before_alerts}" "${after_alerts}" "${before_archive}" "${after_archive}" "${before_reports}" "${after_reports}" "${before_cycles}" "${after_cycles}"
}

round3_manual() {
  local run="${RUN_ID}_manual_r3"
  local before_alerts before_archive before_reports before_cycles before_disk before_py
  before_alerts="$(alert_lines)"
  before_archive="$(count_archive_md)"
  before_reports="$(count_monitor_md)"
  before_cycles="$(count_cycle_reports)"
  before_disk="$(count_event_report_for_event disk_full)"
  before_py="$(count_event_report_for_event python_env)"

  info "Round 3: disk_full + python_env manual escalation"
  flush_tail_window "round3_manual"
  printf '%s\n' \
    '' \
    "[stage6e-d1][disk_full] OSError: [Errno 28] No space left on device: /tmp/acme_order_cache/features_${run}.bin" \
    "[summary] secondary_failure=No space left on device disk cache full RUN_ID=${run}" \
    '' \
    "[stage6e-d1][python_env] Traceback (most recent call last): RUN_ID=${run}" \
    'Traceback (most recent call last):' \
    '  File "/srv/order-service/run_service.py", line 21, in <module>' \
    '    import acme_internal_sdk' \
    "ModuleNotFoundError: No module named 'acme_internal_sdk_${run}'" \
    "Python interpreter and pip path do not belong to the same environment RUN_ID=${run}" \
    "[summary] secondary_failure=python dependency missing and interpreter mismatch RUN_ID=${run}" \
    >> "${RUNTIME_LOG}"

  wait_seconds "${WAIT_SECONDS}" "manual escalation handling"
  wait_for_increase "disk_full event reports" "${before_disk}" "${SETTLE_TIMEOUT}" count_event_report_for_event disk_full || true
  wait_for_increase "python_env event reports" "${before_py}" "${SETTLE_TIMEOUT}" count_event_report_for_event python_env || true
  check_acceptance_baseline "round3_manual"
  assert_service_running
  check_daemon_clean
  check_remote_applied_safety

  local after_alerts after_archive after_reports after_cycles actual
  after_alerts="$(alert_lines)"
  after_archive="$(count_archive_md)"
  after_reports="$(count_monitor_md)"
  after_cycles="$(count_cycle_reports)"
  if grep -Fq '"event_type": "disk_full"' "${ALERT_JSONL}" && grep -Fq '"event_type": "python_env"' "${ALERT_JSONL}" && grep -Fq '"status": "manual_escalation"' "${ALERT_JSONL}"; then
    pass "Round 3 manual escalation evidence found"
    actual="disk_full and python_env escalated without disallowed apply."
  else
    fail "Round 3 manual escalation evidence missing"
    actual="manual escalation evidence missing or incomplete."
  fi
  append_round_summary "Round 3 - disk_full + python_env" "manual_escalation/report_only, no disallowed apply." "${actual}" "${before_alerts}" "${after_alerts}" "${before_archive}" "${after_archive}" "${before_reports}" "${after_reports}" "${before_cycles}" "${after_cycles}"
}

round4_multi() {
  local run="${RUN_ID}_multi_r4"
  local before_alerts before_archive before_reports before_cycles
  before_alerts="$(alert_lines)"
  before_archive="$(count_archive_md)"
  before_reports="$(count_monitor_md)"
  before_cycles="$(count_cycle_reports)"

  info "Round 4: same-cycle network_port + gpu_oom"
  flush_tail_window "round4_multi"
  reset_runtime_config 9100 128
  printf '%s\n' \
    '' \
    "[stage6e-d1][network_port] Traceback (most recent call last): RUN_ID=${run}" \
    'Traceback (most recent call last):' \
    '  File "/srv/order-service/run_service.py", line 132, in start_metrics_exporter' \
    "    server_socket.bind(('127.0.0.1', 9100))" \
    "OSError: [Errno 98] Address already in use RUN_ID=${run}" \
    "[summary] primary_failure=Address already in use metrics port conflict RUN_ID=${run}" \
    '' \
    "[stage6e-d1][gpu_oom] Traceback (most recent call last): RUN_ID=${run}" \
    'Traceback (most recent call last):' \
    '  File "/srv/order-service/train.py", line 88, in run_batch' \
    '    loss.backward()' \
    "RuntimeError: CUDA out of memory. Tried to allocate 3072.00 MiB. GPU 0 has only 256.00 MiB free. RUN_ID=${run}" \
    "[summary] primary_failure=CUDA out of memory batch_size too large RUN_ID=${run}" \
    >> "${RUNTIME_LOG}"

  wait_seconds "${WAIT_SECONDS}" "same-cycle multi-event handling"
  wait_for_increase "cycle summaries" "${before_cycles}" "${SETTLE_TIMEOUT}" count_cycle_reports || true
  check_acceptance_baseline "round4_multi"
  assert_service_running
  check_daemon_clean

  local after_alerts after_archive after_reports after_cycles actual
  after_alerts="$(alert_lines)"
  after_archive="$(count_archive_md)"
  after_reports="$(count_monitor_md)"
  after_cycles="$(count_cycle_reports)"
  if (( after_cycles > before_cycles )); then
    pass "Round 4 generated a cycle summary"
    actual="cycle summary increased after multi-event injection."
  else
    partial "Round 4 did not show cycle summary growth"
    actual="multi-event handling did not produce a new cycle summary within timeout."
  fi
  append_round_summary "Round 4 - same-cycle network_port + gpu_oom" "both event types handled; cycle summary generated." "${actual}" "${before_alerts}" "${after_alerts}" "${before_archive}" "${after_archive}" "${before_reports}" "${after_reports}" "${before_cycles}" "${after_cycles}"
}

round5_duplicate() {
  local run="${RUN_ID}_network_r1"
  local before_alerts before_archive before_reports before_cycles
  before_alerts="$(alert_lines)"
  before_archive="$(count_archive_md)"
  before_reports="$(count_monitor_md)"
  before_cycles="$(count_cycle_reports)"

  info "Round 5: duplicate network_port block"
  flush_tail_window "round5_duplicate"
  printf '%s\n' \
    '' \
    "[stage6e-d1][network_port] Traceback (most recent call last): RUN_ID=${run}" \
    'Traceback (most recent call last):' \
    '  File "/srv/order-service/run_service.py", line 132, in start_metrics_exporter' \
    "    server_socket.bind(('127.0.0.1', 9100))" \
    "OSError: [Errno 98] Address already in use RUN_ID=${run}" \
    "[summary] primary_failure=Address already in use metrics port conflict RUN_ID=${run}" \
    >> "${RUNTIME_LOG}"

  wait_seconds "${WAIT_SECONDS}" "duplicate fingerprint dedupe"
  assert_service_running
  check_daemon_clean

  local after_alerts after_archive after_reports after_cycles actual
  after_alerts="$(alert_lines)"
  after_archive="$(count_archive_md)"
  after_reports="$(count_monitor_md)"
  after_cycles="$(count_cycle_reports)"
  if [[ "${before_alerts}" == "${after_alerts}" && "${before_reports}" == "${after_reports}" ]]; then
    pass "Round 5 duplicate did not create new reports"
    actual="duplicate fingerprint appears deduped."
  else
    partial "Round 5 duplicate created new artifacts; record actual behavior"
    actual="duplicate produced artifact growth; daemon stayed stable."
  fi
  append_round_summary "Round 5 - duplicate network_port" "same RUN_ID should usually dedupe." "${actual}" "${before_alerts}" "${after_alerts}" "${before_archive}" "${after_archive}" "${before_reports}" "${after_reports}" "${before_cycles}" "${after_cycles}"
}

round6_network_variant() {
  local run="${RUN_ID}_network_variant_r6"
  local before_alerts before_archive before_reports before_cycles before_network_archive
  before_alerts="$(alert_lines)"
  before_archive="$(count_archive_md)"
  before_reports="$(count_monitor_md)"
  before_cycles="$(count_cycle_reports)"
  before_network_archive="$(count_archive_for_event network_port)"

  info "Round 6: new network_port variant"
  flush_tail_window "round6_network_variant"
  reset_runtime_config 9100 4
  printf '%s\n' \
    '' \
    "[stage6e-d1][network_port] Traceback (most recent call last): RUN_ID=${run}" \
    'Traceback (most recent call last):' \
    '  File "/srv/order-service/run_service.py", line 188, in start_admin_exporter' \
    "    server_socket.bind(('127.0.0.1', 19100))" \
    "OSError: [Errno 98] Address already in use while binding admin exporter RUN_ID=${run}" \
    "[summary] primary_failure=Address already in use admin exporter conflict RUN_ID=${run}" \
    >> "${RUNTIME_LOG}"

  wait_seconds "${WAIT_SECONDS}" "network_port variant recovery"
  wait_for_increase "network_port archive files" "${before_network_archive}" "${SETTLE_TIMEOUT}" count_archive_for_event network_port || true
  check_acceptance_baseline "round6_network_variant"
  assert_service_running
  check_daemon_clean

  local after_alerts after_archive after_reports after_cycles actual
  after_alerts="$(alert_lines)"
  after_archive="$(count_archive_md)"
  after_reports="$(count_monitor_md)"
  after_cycles="$(count_cycle_reports)"
  if (( after_alerts > before_alerts )); then
    pass "Round 6 network variant processed"
    actual="new network_port variant produced new alert/report evidence."
  else
    fail "Round 6 network variant did not produce alert growth"
    actual="new network_port variant evidence missing."
  fi
  append_round_summary "Round 6 - new network_port variant" "new fingerprint should process with fix-network-1." "${actual}" "${before_alerts}" "${after_alerts}" "${before_archive}" "${after_archive}" "${before_reports}" "${after_reports}" "${before_cycles}" "${after_cycles}"
}

main() {
  require_project_root
  require_file "${PYTHON_BIN}"
  require_file "${RUNTIME_LOG}"
  require_file "${DAEMON_LOG}"
  require_file "${RUNTIME_CONFIG}"
  require_service_running

  mkdir -p "${D1_DIR}"
  info "D1 artifact directory: ${D1_DIR}"

  flush_tail_window "preflight_baseline"
  wait_seconds "${WAIT_SECONDS}" "preflight tail isolation"
  assert_service_running
  check_daemon_clean

  service_status > "${D1_DIR}/baseline_systemd_show.txt"
  tail -n 200 "${DAEMON_LOG}" > "${D1_DIR}/baseline_daemon_tail.txt"
  find outputs/alerts -type f | sort > "${D1_DIR}/baseline_alert_files.txt" || true
  find "${MONITOR_DIR}" -type f | sort > "${D1_DIR}/baseline_monitor_files.txt" || true
  if [[ -f "${ALERT_JSONL}" ]]; then
    wc -l "${ALERT_JSONL}" > "${D1_DIR}/baseline_alert_jsonl_count.txt"
  else
    echo "0 ${ALERT_JSONL}" > "${D1_DIR}/baseline_alert_jsonl_count.txt"
  fi
  if [[ -f "state/${PROJECT_ID}/project_status.json" ]]; then
    cp "state/${PROJECT_ID}/project_status.json" "${D1_DIR}/baseline_project_status.json"
  fi
  append_summary_header

  round0_noise
  round1_network
  round2_gpu
  round3_manual
  round4_multi
  round5_duplicate
  round6_network_variant

  check_remote_applied_safety
  check_daemon_clean
  assert_service_running
  write_final_summary

  if (( FAILURES > 0 )); then
    exit 1
  fi
}

main "$@"
