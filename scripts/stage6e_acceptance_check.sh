#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="/home/lf/projects/agentic-linux-troubleshooter"
PROJECT_ID="enterprise_demo_local"
SERVICE_NAME="agentic-monitor@${PROJECT_ID}.service"
DAEMON_LOG="state/${PROJECT_ID}/daemon.log"
ALERT_JSONL="outputs/alerts/${PROJECT_ID}_alerts.jsonl"
ALERT_LATEST="outputs/alerts/${PROJECT_ID}_latest_alert.md"
ALERT_ARCHIVE_DIR="outputs/alerts/${PROJECT_ID}_alerts"
MONITOR_DIR="outputs/monitors/${PROJECT_ID}"

failures=0

info() { echo "[INFO] $*"; }
pass() { echo "[PASS] $*"; }
fail_check() { echo "[FAIL] $*" >&2; failures=$((failures + 1)); }

[[ "$(pwd)" == "${PROJECT_ROOT}" ]] || {
  echo "[FAIL] Please run from ${PROJECT_ROOT}" >&2
  exit 1
}

info "Checking systemd service state"
if mapfile -t service_state < <(systemctl show "${SERVICE_NAME}" -p ActiveState -p SubState --value); then
  active_state="${service_state[0]:-unknown}"
  sub_state="${service_state[1]:-unknown}"
  if [[ "${active_state}" == "active" && "${sub_state}" == "running" ]]; then
    pass "systemd is active/running"
  else
    fail_check "systemd is not active/running: ActiveState=${active_state}, SubState=${sub_state}. Start manually with: sudo systemctl restart ${SERVICE_NAME}"
  fi
else
  fail_check "Unable to read systemd state for ${SERVICE_NAME}"
fi

info "Checking daemon log"
if [[ -f "${DAEMON_LOG}" ]]; then
  pass "daemon log exists: ${DAEMON_LOG}"
  forbidden_patterns=(
    "AttributeError: 'FileNotifier' object has no attribute 'send'"
    "daemon crashed"
    "Traceback"
    "event handling failed"
    "failed to generate cycle summary report"
  )
  for pattern in "${forbidden_patterns[@]}"; do
    if grep -Fq "${pattern}" "${DAEMON_LOG}"; then
      fail_check "daemon log contains forbidden pattern: ${pattern}"
    else
      pass "daemon log does not contain: ${pattern}"
    fi
  done
else
  fail_check "daemon log missing: ${DAEMON_LOG}"
fi

info "Checking alerts"
if [[ -f "${ALERT_JSONL}" ]]; then
  alert_lines="$(wc -l < "${ALERT_JSONL}")"
  if (( alert_lines >= 1 )); then
    pass "alerts jsonl exists with ${alert_lines} records"
  else
    fail_check "alerts jsonl exists but is empty: ${ALERT_JSONL}"
  fi
else
  fail_check "alerts jsonl missing: ${ALERT_JSONL}"
fi

[[ -f "${ALERT_LATEST}" ]] && pass "latest alert exists" || fail_check "latest alert missing: ${ALERT_LATEST}"
[[ -d "${ALERT_ARCHIVE_DIR}" ]] && pass "alert archive directory exists" || fail_check "alert archive directory missing: ${ALERT_ARCHIVE_DIR}"

if [[ -d "${ALERT_ARCHIVE_DIR}" ]]; then
  shopt -s nullglob
  archive_md=("${ALERT_ARCHIVE_DIR}"/*.md)
  archive_json=("${ALERT_ARCHIVE_DIR}"/*.json)
  shopt -u nullglob
  if (( ${#archive_md[@]} >= 1 && ${#archive_json[@]} >= 1 )); then
    paired=0
    for md_path in "${archive_md[@]}"; do
      json_path="${md_path%.md}.json"
      if [[ -f "${json_path}" ]]; then
        paired=1
        break
      fi
    done
    if (( paired == 1 )); then
      pass "alert archive contains at least one md/json pair"
    else
      fail_check "alert archive has md/json files but no matching basename pair"
    fi
  else
    fail_check "alert archive must contain at least one .md and one .json"
  fi
fi

info "Checking monitor reports"
if [[ -d "${MONITOR_DIR}" ]]; then
  pass "monitor report directory exists"
  event_report_count="$(find "${MONITOR_DIR}" -name 'event_*_final_llm_report.md' -type f | wc -l)"
  cycle_report_count="$(find "${MONITOR_DIR}" -name 'cycle_*_summary_report.md' -type f | wc -l)"
  if (( event_report_count >= 1 )); then
    pass "event final LLM reports found: ${event_report_count}"
  else
    fail_check "no event_*_final_llm_report.md found under ${MONITOR_DIR}"
  fi
  if (( cycle_report_count >= 1 )); then
    pass "cycle summary reports found: ${cycle_report_count}"
  else
    fail_check "no cycle_*_summary_report.md found under ${MONITOR_DIR}"
  fi
else
  fail_check "monitor report directory missing: ${MONITOR_DIR}"
fi

info "Checking safety boundary in remote_applied_fixes.json"
if [[ -d "${MONITOR_DIR}" ]]; then
  mapfile -t applied_files < <(find "${MONITOR_DIR}" -name 'remote_applied_fixes.json' -type f | sort)
  if (( ${#applied_files[@]} == 0 )); then
    pass "no remote_applied_fixes.json found; no remote apply evidence to inspect"
  else
    for applied_file in "${applied_files[@]}"; do
      if grep -Eq 'fix-python-1|pip install|rm -rf|kill -9' "${applied_file}"; then
        fail_check "dangerous or disallowed apply evidence found in ${applied_file}"
      else
        pass "safe apply evidence: ${applied_file}"
      fi
    done
  fi
fi

echo ""
if (( failures == 0 )); then
  echo "[PASS] STAGE 6E ACCEPTANCE CHECK PASSED"
else
  echo "[FAIL] STAGE 6E ACCEPTANCE CHECK FAILED with ${failures} failure(s)" >&2
  exit 1
fi
