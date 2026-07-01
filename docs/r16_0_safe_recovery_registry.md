# R16-0 registry domain policy 收敛说明

## 1. 目标

R16-0 只做 `safe_auto_recover` 注册信息收敛，不新增恢复域，不新增 `fix_id`，不扩大真实自动恢复权限。

本阶段把现有 5 个 safe 域的重复定义集中到 `safe_recovery.registry`：

- `network_port -> fix-network-1`
- `gpu_oom -> fix-gpu-1`
- `cache_write_failed -> fix-cache-1`
- `optional_dependency_missing -> fix-optional-dep-1`
- `worker_overload -> fix-worker-1`

## 2. 收敛范围

以下运行时入口改为从 registry 派生：

- registry safe event 集合；
- compatibility remediation adapter 中的 safe fix 映射；
- guarded dry-run candidate allowlist；
- runtime gate 的 `event_type -> fix_id` 和 action description；
- runtime precheck 的 safety spec / planned edits；
- local / remote safe apply executor 的字段候选。

执行层安全边界不变：真实恢复仍只允许项目内 JSON 字段编辑，并且必须经过 project policy overlay、runtime gate、precheck、cooldown、rollback 和 audit。

## 3. 非目标

R16-0 不做以下事情：

- 不新增企业故障域；
- 不允许 `systemctl`、`kubectl`、`rm`、`pip install`、权限提升等危险动作；
- 不把项目配置变成自定义恢复动作入口；
- 不修改 detector 分类规则；
- 不启用默认 live recovery，默认 dry-run 策略保持不变。

## 4. 验证

新增一致性测试：

```bash
.venv/bin/python -m pytest tests/test_safe_recovery_registry.py -q
```

R15 回归组合和 core baseline 已验证通过：

```bash
.venv/bin/python -m pytest \
  tests/test_auto_recovery_policy.py \
  tests/test_auto_recovery_policy_dry_run.py \
  tests/test_auto_recovery_runtime_gate.py \
  tests/test_auto_recovery_runner_r15_gate.py \
  tests/test_auto_recovery_failure_path_validation.py \
  tests/test_guarded_auto_recover_dry_run.py \
  tests/test_safe_auto_recover_domain_expansion.py \
  tests/test_stage6d_notification.py \
  tests/test_stage6_cycle_summary_reporter.py \
  tests/test_fault_domain_regression.py \
  tests/test_safe_recovery_registry.py -q

scripts/run_core_tests.sh
```

## 5. 后续

后续新增企业域时，应先增加 `RecoveryDomainSpec`；只有低风险可执行域再增加 `SafeRecoverySpec`，随后补 detector fixture、runtime gate 测试、apply/rollback 测试和 dry-run 证据。
