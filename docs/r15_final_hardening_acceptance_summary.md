# R15 Final Hardening / Acceptance Summary

## 1. 背景

R15 从自动恢复策略安全分层开始，逐步完成 policy schema、validator / resolver、dry-run、guarded dry-run、runtime gate、existing safe recovery 验证、新安全恢复域扩展，以及最终的执行层 hardening。

本总结覆盖 R15 后半段收敛结果：

- R15 gate 固化为真实恢复唯一执行入口；
- audit / report / alert 从设计字段变成运行时强制字段；
- 真实 precheck 接入执行层；
- per fingerprint / event_type / project cooldown 接入执行层；
- rollback 成功与失败路径完成受控验证；
- 新增 safe auto_recover 域已完成 dry-run 与隔离 live recovery 验证。

R15 的最终目标不是无限扩权，而是让自动恢复形成可控、可审计、可回滚、可限流、可降级的安全边界。

## 2. 最终能力清单

| 能力 | 状态 | 说明 |
| --- | --- | --- |
| 策略分层 | PASS | 支持 `diagnose_only`、`manual_escalation`、`safe_auto_recover`、`guarded_auto_recover`、`disabled` |
| policy schema | PASS | 已有 schema 设计、示例与 validator |
| resolver | PASS | 可把 event_type / fix_id 解析为结构化决策 |
| policy dry-run | PASS | 可对样例事件生成 dry-run 决策 |
| guarded dry-run | PASS | `would_execute=false`，只做审计模型 |
| runtime gate | PASS | 真实 AutoRecoveryRunner 执行前必须经过 R15 gate |
| 唯一执行入口 | PASS | 禁止 legacy passthrough 绕过 R15 gate |
| 强制 audit | PASS | report / alert / cycle summary 均携带 recovery audit summary |
| 真实 precheck | PASS | 执行前检查目标配置、允许字段、planned edits、rollback plan |
| cooldown | PASS | 支持 per fingerprint、per event_type、per project cooldown |
| rollback audit | PASS | 记录 apply / rollback 的 field_path、old_value、new_value、backup_path、diff_path |
| failure-path validation | PASS | 覆盖 rerun 失败、rollback 成功、rollback 失败、cooldown 阻断 |
| forbidden action 阻断 | PASS | 危险动作不得自动执行 |
| 默认安全态 | PASS | 真实配置保持 `auto_recovery_dry_run: true` |

## 3. 当前 safe_auto_recover 范围

当前允许进入 `safe_auto_recover` 的范围仅限显式 allowlist fix_id：

| event_type | fix_id | 安全边界 |
| --- | --- | --- |
| `network_port` | `fix-network-1` | 只修改受控 JSON 端口字段 |
| `gpu_oom` | `fix-gpu-1` | 只下调 batch_size 类字段 |
| `cache_write_failed` | `fix-cache-1` | 只关闭可选缓存写入或演示故障开关 |
| `optional_dependency_missing` | `fix-optional-dep-1` | 只关闭可选依赖集成或演示告警开关 |
| `worker_overload` | `fix-worker-1` | 只下调 worker 并发类字段 |

未知 event_type、未知 fix_id、未显式 allowlist 的 fix_id 不得自动恢复。

## 4. 明确不允许自动恢复的范围

以下故障域仍保持 `manual_escalation`、`diagnose_only` 或 `disabled`：

- `process_crash`
- `container_k8s`
- `disk_full` 的自动删除清理
- `python_env` 的自动 `pip install`
- `auth_cert`
- `slurm`
- `dependency_service`
- unknown event_type

以下动作继续禁止默认自动执行：

- `systemctl restart`
- `systemctl stop`
- `kill -9`
- `rm -rf`
- `pip install`
- `kubectl delete`
- `kubectl apply`
- 权限提升
- 跨主机破坏性操作

## 5. 执行层安全边界

真实恢复执行前必须满足：

1. R15 policy enabled；
2. event_type 是 safe candidate；
3. fix_id 在 event_type policy 中显式允许；
4. fix_id 在项目 `allow_auto_apply` 中显式允许；
5. `auto_recovery_dry_run=false`；
6. precheck 通过；
7. cooldown check 通过并 reserve；
8. rollback plan 可用；
9. strategy_layer 是 `safe_auto_recover`；
10. forbidden action 未命中；
11. R15 gate audit record 已生成。

如果任一条件不满足，AutoRecoveryRunner 不得进入 apply / rerun。

## 6. Audit 强制字段

每次恢复决策必须生成结构化 audit。核心字段包括：

```text
event_type
fingerprint
strategy_layer
selected_policy
action
candidate_fix_id
selected_fix_id
fix_id
auto_recover_allowed
dry_run
would_execute
allowed_to_execute
precheck_result
cooldown_result
rate_limit_result
rollback_available
rollback_plan
operator_required
downgrade_reason
forbidden_action
execution_result
rollback_result
apply_success
rerun_success
rollback_executed
rollback_success
apply_edit_summary
rollback_edit_summary
recovered
event_recovery_status
residual_risk_status
audit_required
created_at
```

这些字段进入：

- event report；
- alert JSON；
- alert Markdown；
- cycle summary；
- session auto recovery evidence。

## 7. Precheck / Cooldown / Rollback

### Precheck

precheck 会检查：

- 目标 fix_id 是否有 safety spec；
- event_type 与 fix_id 是否匹配；
- 目标 project_dir / remote_project_dir 是否存在；
- 目标 config 路径是否安全；
- 可修改字段是否存在；
- planned edits 是否可生成；
- action 是否为低风险动作；
- rollback plan 是否可用；
- evidence 是否存在。

### Cooldown

执行层 cooldown 支持：

- per fingerprint cooldown；
- per event_type cooldown；
- per project cooldown。

只有 live 执行窗口才 reserve cooldown。dry-run 不 reserve。

### Rollback

apply 成功但 rerun 失败时会触发 rollback。最终状态区分：

- `rollback_succeeded`
- `rollback_failed`
- `not_needed_recovered`
- `not_run_before_execution`

如果 rollback 失败，cycle summary 总体状态必须为 `rollback_failed`，不能被 `partially_recovered` 掩盖。

## 8. 验收证据

已完成的真实或受控验收包括：

| 验收项 | 结果 | 证据 |
| --- | --- | --- |
| network_port live dry-run | PASS | `acceptance_artifacts/r15_9b_network_port_live_dry_run_20260620_122244/R15_9B_NETWORK_PORT_LIVE_DRY_RUN_SUMMARY.md` |
| 新增 3 个 safe 域 live dry-run | PASS | `acceptance_artifacts/r15_new_safe_domains_live_dry_run_20260620_155740/R15_NEW_SAFE_DOMAINS_LIVE_DRY_RUN_SUMMARY.md` |
| 新增 3 个 safe 域 live recovery | PASS | `acceptance_artifacts/r15_new_safe_domains_live_recovery_20260620_161547/R15_NEW_SAFE_DOMAINS_LIVE_RECOVERY_SUMMARY.md` |
| R15 preflight | PASS | `acceptance_artifacts/r15_9a_safe_recovery_preflight_20260619_123023/R15_9A_SAFE_RECOVERY_PREFLIGHT.md` |
| failure-path controlled validation | PASS | `tests/test_auto_recovery_failure_path_validation.py` |

新增 3 个 safe 域真实恢复验证结果：

| event_type | fix_id | apply | rerun | recovered |
| --- | --- | --- | --- | --- |
| `cache_write_failed` | `fix-cache-1` | true | true | true |
| `optional_dependency_missing` | `fix-optional-dep-1` | true | true | true |
| `worker_overload` | `fix-worker-1` | true | true | true |

## 9. 测试结果

R15 final hardening 验收运行：

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
  tests/test_fault_domain_regression.py -q
```

结果：PASS。

Core baseline：

```bash
scripts/run_core_tests.sh
```

结果：`CORE TEST BASELINE PASSED`。

## 10. 后续测试策略

已经做过真实错误注入和真实恢复验证后，不需要每次普通改动都重复 live 注入。

但仍然需要测试，测试分层如下：

| 场景 | 是否需要测试 | 建议 |
| --- | --- | --- |
| 普通文档修改 | 低 | 可只跑 core tests 或不跑 live |
| policy / resolver / gate 修改 | 高 | 必跑 R15 policy + gate + runner tests |
| report / alert 字段修改 | 高 | 必跑 notification / cycle summary tests |
| apply / rollback / cooldown 修改 | 高 | 必跑 failure-path validation |
| detector 修改 | 高 | 必跑 fault domain regression |
| 新增 safe_auto_recover 故障域 | 很高 | 先 dry-run，再 isolated live dry-run，再小范围 live recovery |
| 修改真实服务配置或部署 | 很高 | 必须做 preflight，必要时做 live dry-run |

真实 live 注入只在以下情况需要重复：

- 新增恢复域；
- 修改 fix_id 的真实 apply 行为；
- 修改 AutoRecoveryRunner 执行顺序；
- 修改 R15 gate / cooldown / rollback 关键逻辑；
- 修改 detector 可能影响 event_type 识别；
- 生产环境或 runtime 项目配置发生明显变化。

否则，以自动化测试作为日常回归即可。

## 11. 最终结论

R15 final hardening / acceptance 已完成。

系统现在具备：

- 策略分层；
- policy validator / resolver；
- dry-run / guarded dry-run；
- R15 gate 唯一执行入口；
- 强制 audit/report/alert 字段；
- 真实 precheck；
- 执行层 cooldown；
- rollback 成功/失败审计；
- failure-path controlled validation；
- 已验证 safe_auto_recover 小范围真实恢复能力。

R15 自动恢复策略分层可以标记完成。

下一阶段不建议继续改 R15 基础设施，建议进入：

```text
R16：新增/细化企业级 safe_auto_recover 故障域
```

每新增一个恢复域都必须从 schema、precheck、dry-run、failure-path、isolated live validation 逐级进入，不得直接进入真实恢复。
