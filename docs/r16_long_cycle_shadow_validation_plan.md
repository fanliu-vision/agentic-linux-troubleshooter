# R16-S2 long-cycle dry-run / shadow validation plan

## 1. Scope

R16-S2 validates the existing 11 safe recovery domains over repeated offline shadow cycles. It does not add safe domains, expand `auto_recover` permission, inject runtime logs, run live smoke, or execute recovery.

The long-cycle script is:

```bash
.venv/bin/python scripts/r16_long_cycle_shadow_validate.py \
  --cycles 3 \
  --interval-seconds 1 \
  --output-dir acceptance_artifacts/r16_s2_shadow_pilot_20260623_092455
```

The script reuses `scripts/r16_safe_recovery_shadow_validate.py` for each cycle and writes:

- `cycle_XXX/R16_S2_CYCLE_SUMMARY.md`
- `cycle_XXX/R16_SAFE_RECOVERY_DOMAIN_VALIDATION_SUMMARY.md`
- `R16_S2_LONG_CYCLE_SHADOW_PILOT_SUMMARY.md`
- `long_cycle_shadow_summary.json`

## 2. Pilot Result

Short pilot output:

```text
acceptance_artifacts/r16_s2_shadow_pilot_20260623_092455/R16_S2_LONG_CYCLE_SHADOW_PILOT_SUMMARY.md
```

| Metric | Result |
| --- | --- |
| conclusion | `PASS` |
| cycles_completed | `3` |
| interval_seconds | `1` |
| safe_candidate_count | `33` |
| manual_escalation_count | `36` |
| diagnose_only_count | `0` |
| disabled_count | `528` |
| no_op_count | `33` |
| forbidden_blocked_count | `528` |
| rollback_available_count | `33` |
| rollback_unavailable_count | `0` |
| remote_apply_fix_called | `False` |
| rerun_remote_project_called | `False` |
| exception_count | `0` |

The pilot used a short interval to validate framework behavior inside a local development turn. It is not a replacement for the formal 3-7 day observation window.

## 3. Formal 3-7 Day Run

Recommended 3-day run:

```bash
PILOT_DIR="acceptance_artifacts/r16_s2_shadow_3d_$(date +%Y%m%d_%H%M%S)"

.venv/bin/python scripts/r16_long_cycle_shadow_validate.py \
  --cycles 288 \
  --interval-seconds 900 \
  --output-dir "$PILOT_DIR"
```

Recommended 7-day run:

```bash
PILOT_DIR="acceptance_artifacts/r16_s2_shadow_7d_$(date +%Y%m%d_%H%M%S)"

.venv/bin/python scripts/r16_long_cycle_shadow_validate.py \
  --cycles 672 \
  --interval-seconds 900 \
  --output-dir "$PILOT_DIR"
```

Both commands are shadow-only. They write acceptance artifacts under the requested directory and do not touch real `state/` or `outputs/`.

## 4. Observability Metrics

Each cycle and aggregate summary records:

- safe candidate count;
- manual escalation count;
- diagnose-only count;
- disabled count;
- no-op count;
- forbidden blocked count;
- rollback available / unavailable count;
- remote apply call count;
- rerun call count;
- misidentified count;
- missed detection count;
- downgrade count;
- exception count.

## 5. PASS / PARTIAL / FAIL

PASS:

- every cycle conclusion is `PASS`;
- `remote_apply_fix_called=False`;
- `rerun_remote_project_called=False`;
- exception count is `0`;
- rollback is available for every safe candidate;
- no missing fixture or registry coverage appears.

PARTIAL:

- one or more cycles is `PARTIAL`;
- shadow coverage has a non-execution gap such as missing evidence or unexpected downgrade, but no forbidden/live execution occurred.

FAIL:

- any exception prevents a cycle from completing;
- `remote_apply_fix` or `rerun_remote_project` is called;
- forbidden action is not blocked;
- high-risk domains escape manual escalation or diagnose-only fallback;
- safe candidate registry coverage drifts.

## 6. Safety Boundary

R16-S2 remains dry-run / shadow only:

- no `sudo`;
- no service restart;
- no runtime log injection;
- no live smoke;
- no real recovery;
- no real `state/` or `outputs/` writes;
- no `remote_apply_fix`;
- no `rerun_remote_project`;
- no new safe domains;
- no broader `auto_recover` permission.

## 7. Recommendation

The short pilot completed with `PASS`, so the project is ready to enter the formal 3-7 day shadow validation window.
