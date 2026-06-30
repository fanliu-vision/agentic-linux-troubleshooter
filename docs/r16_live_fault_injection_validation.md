# R16 Live Fault Injection Validation

## Scope

This stage validates the live behavior after isolated real-shape fault injection:

- report generation through `TroubleshootingSession.generate_report`;
- safe-domain automatic recovery with `auto_recovery_dry_run=false`;
- non-safe-domain notification and audit without automatic recovery;
- file alert archive and recovery audit payload integrity.

All scenarios run inside generated isolated projects. They do not mutate host services, systemd, Kubernetes, Slurm, or real application state.

## Safe-Domain Flow

For each safe recovery spec, the validator creates:

- `logs/service.log` with a realistic injected fault;
- `config.json` with an unsafe but controlled field value;
- `scenario.json` and `app.py` for a real rerun command;
- `state/project_status.json` and `state/events.jsonl`.

The validation then runs:

1. detector identifies the expected `event_type`;
2. initial project rerun fails;
3. `AutoRecoveryRunner.recover()` applies the safe fix;
4. backup and diff files are created;
5. rerun succeeds;
6. recovery audit records `executed_recovered`;
7. report is generated;
8. file notification is written with recovered audit payload;
9. post-notification report includes Stage 6D notification context.

## Non-Safe Flow

For selected high-risk domains, the validator runs the same event handling path but expects:

- `manual_escalation`;
- `auto_recover_allowed=false`;
- `allowed_to_execute=false`;
- `execution_result=not_run_r15_gate_blocked`;
- report generated;
- file notification archived;
- recovery audit embedded in the notification payload;
- no apply and no rerun.

## Running

Deterministic local validation:

```bash
.venv/bin/python scripts/r16_live_fault_injection_validate.py \
  --report-mode rule \
  --output-dir /tmp/r16_live_fault_injection_rule_verify
```

Auto report mode smoke, using LLM if configured and rule fallback otherwise:

```bash
.venv/bin/python scripts/r16_live_fault_injection_validate.py \
  --output-dir /tmp/r16_live_fault_injection_auto_smoke \
  --safe-event-types network_port \
  --high-risk-event-types disk_full
```

The script writes:

- `R16_LIVE_FAULT_INJECTION_SUMMARY.md`;
- `live_fault_injection_summary.json`;
- per-scenario reports, alerts, audit JSON, backup, and diff artifacts.

## Current Result

2026-06-30 local deterministic run:

- safe_rows: `11`
- high_risk_rows: `6`
- conclusion: `PASS`

Auto report-mode smoke:

- safe_rows: `1`
- high_risk_rows: `1`
- conclusion: `PASS`

Target regression:

```bash
.venv/bin/python -m pytest -q \
  tests/test_r16_live_fault_injection_validation.py \
  tests/test_r16_isolated_fault_injection_validation.py \
  tests/test_stage6d_notification.py \
  tests/test_auto_recovery_runner_r15_gate.py
```

Result: `18 passed`
