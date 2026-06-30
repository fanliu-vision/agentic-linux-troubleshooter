# R16 隔离真实错误注入验证

## 目标

在 3 天周期性 shadow 验证通过后，进入隔离项目 / 模拟 Linux 项目的真实形态错误注入阶段。该阶段仍保持 `auto_recovery_dry_run=true`，只验证 detector、policy、runtime gate、precheck 和审计产物，不执行真实恢复动作。

## 验证范围

- 为每个 safe recovery domain 创建独立模拟项目目录。
- 注入真实形态 `logs/service.log`、`config.json`、`state/project_status.json`、`state/events.jsonl`、模拟 systemd/runtime 状态文件。
- 验证 detector 命中预期 `event_type`。
- 验证 remediation policy 和 runtime gate 解析到预期 `fix_id`。
- 验证 safe 域只生成 dry-run audit，并包含 planned edit、backup plan、diff plan、rollback plan。
- 验证 dry-run 不创建 backup/diff 文件、不写 `applied_fixes.json`、不修改 `config.json`。
- 验证高风险域继续进入 `manual_escalation` / `diagnose_only`，且 `allowed_to_execute=false`。

## 运行方式

```bash
.venv/bin/python scripts/r16_isolated_fault_injection_validate.py
```

也可以指定输出目录：

```bash
.venv/bin/python scripts/r16_isolated_fault_injection_validate.py --output-dir /tmp/r16_isolated_fault_injection_verify
```

脚本会生成：

- `R16_ISOLATED_FAULT_INJECTION_SUMMARY.md`
- `isolated_fault_injection_summary.json`
- 每个注入场景的隔离项目目录和 dry-run audit JSON

## 当前本地验证结果

2026-06-30 本地执行：

```bash
.venv/bin/python scripts/r16_isolated_fault_injection_validate.py --output-dir /tmp/r16_isolated_fault_injection_verify
```

结果：

- safe_rows: `11`
- high_risk_rows: `12`
- conclusion: `PASS`

同步回归：

```bash
.venv/bin/python -m pytest -q \
  tests/test_r16_isolated_fault_injection_validation.py \
  tests/test_safe_recovery_registry.py \
  tests/test_safe_recovery_semantic_precheck.py \
  tests/test_auto_recovery_runner_r15_gate.py
```

结果：`33 passed`
