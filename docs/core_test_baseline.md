# Core Test Baseline

## Why pytest

The project now uses pytest as the one-command baseline for core test engineering. It gives the repository a consistent collector, marker support, and a simple default command for fast regression checks without running acceptance workflows.

## Dependency Scope

pytest is a development dependency. It is not required for the Agent runtime path and should not be treated as an Agent production dependency.

Install development dependencies with:

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
```

## Run Core Tests

Run the core baseline with:

```bash
scripts/run_core_tests.sh
```

The script first runs `py_compile` on the monitor, notification, auto-recovery, web UI, deployment preflight, and shadow gate modules that anchor the current core baseline. It then runs:

```bash
.venv/bin/python -m pytest tests -q
```

The default pytest configuration excludes tests marked as `integration`, `manual`, `slow`, `browser`, or `e2e`.

## Core Baseline Tests

The current core pytest baseline includes the fast, local tests that do not require systemd, sudo, long-running daemons, localhost SSH, or real persistent `state/` and `outputs/` directories.

The Stage 6 core files are:

- `tests/test_stage6a_monitor_local.py`
- `tests/test_stage6d_notification.py`
- `tests/test_stage6e_monitor_loop_seen.py`
- `tests/test_stage6_cycle_summary_reporter.py`
- `tests/test_stage6e_state_store.py`

Other existing fast unit tests that are unmarked are also included by `pytest tests -q`.

## Outside The Core Baseline

The following test classes are not part of the core pytest baseline:

- D1-mini
- D2
- systemd lifecycle acceptance
- long-running daemon or watch tests
- manual tests that require sudo
- localhost SSH or external-service integration checks
- Playwright browser / visual regression checks

D1-mini PARTIAL is an exploratory long-running result and does not block the core test baseline.

## Related Documents

- `docs/fault_domain_matrix.md`
- `docs/stage6e2_acceptance_report.md`
