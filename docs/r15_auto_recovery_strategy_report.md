# R15 自动恢复策略安全分层阶段总结

## R15 目标

R15 的目标是自动恢复策略安全分层，不是扩权阶段。

本阶段承接 R13 长期稳定性验证和 R14 结构硬化结果，重点是把自动恢复从“是否能执行”前移到“是否应被允许、如何被审计、如何在不确定时降级”。R15 不新增恢复动作，不扩大 `auto_recover` 权限，不接入新的真实执行路径。

## 阶段结果表

| 阶段 | 内容 | 结果 | 是否扩大权限 |
| -- | -- | -- | ------ |
| R15-1 | 自动恢复策略安全分层设计 | PASS | 否 |
| R15-2 | auto_recovery policy schema 设计 | PASS | 否 |
| R15-3 | policy validator / resolver 实现 | PASS | 否 |
| R15-4 | policy schema validator dry-run | PASS | 否 |
| R15-5 | guarded auto_recover dry-run | PASS | 否 |
| R15-6 | audit 与 report/alert 集成设计 + 阶段总结 | PASS | 否 |
| R15-7 | existing safe_auto_recover controlled validation | PASS | 否 |
| R15-8 | existing safe_auto_recover live validation plan | PASS | 否 |
| R15-9 | live preflight / live dry-run | PASS | 否 |
| R15-10 | 新增 safe 域隔离 live recovery 验证 | PASS | 仅限显式 safe allowlist |
| R15 final hardening | gate / audit / precheck / cooldown / rollback 收敛 | PASS | 否 |

## 已完成能力

R15 已完成以下能力：

- 策略分层：`diagnose_only`、`manual_escalation`、`safe_auto_recover`、`guarded_auto_recover`、`disabled`；
- policy schema；
- validator / resolver；
- policy dry-run；
- guarded dry-run；
- forbidden action 阻断；
- `manual_escalation` / `diagnose_only` / `disabled` 保守降级；
- `would_execute=false` 安全边界；
- guarded dry-run audit record；
- audit 与 report/alert 集成设计；
- R15 gate 真实执行入口；
- report / alert / cycle summary 强制 audit 字段；
- 真实 precheck；
- per fingerprint / event_type / project cooldown；
- rollback 成功与失败路径审计；
- failure-path controlled validation；
- R15 阶段测试与验收材料。

## 未改变边界

R15 保持以下边界：

- 未新增危险恢复动作；
- 未扩大到未授权故障域；
- 真实执行仅限显式 `safe_auto_recover` allowlist；
- 未修改 detector；
- 未允许 legacy passthrough 绕过 R15 gate；
- 默认配置保持 `auto_recovery_dry_run: true`；
- `process_crash` 不自动恢复；
- `container_k8s` 不自动执行 `kubectl`；
- `disk_full` 不自动执行 `rm`；
- `python_env` 不自动执行 `pip install`；
- forbidden action 命中时禁止自动恢复。

## 测试结果

R15-6 收尾验证运行以下测试，结果均为 PASS：

| 测试 | 结果 | 说明 |
| --- | --- | --- |
| policy validator | PASS | `tests/test_auto_recovery_policy.py` 通过 |
| policy dry-run | PASS | `tests/test_auto_recovery_policy_dry_run.py` 通过 |
| guarded dry-run | PASS | `tests/test_guarded_auto_recover_dry_run.py` 通过 |
| fault regression | PASS | `tests/test_fault_domain_regression.py` 通过 |
| core tests | PASS | `scripts/run_core_tests.sh` 输出 `CORE TEST BASELINE PASSED` |

## 后续路线

R15 完成后可以选择两条后续路线：

```text
R15-7：existing safe_auto_recover controlled validation
```

或：

```text
R16：新增/细化故障域
```

建议优先进入 R15-7，前提是仍然只针对已有低风险 fix_id 做 controlled validation，例如既有 `network_port` / `fix-network-1` 和 `gpu_oom` / `fix-gpu-1` 范围内的受控验证。

R15-7 已按该范围完成受控验证：`network_port / fix-network-1` 与 `gpu_oom / fix-gpu-1` 均可被 resolver 识别为 `safe_auto_recover` candidate，并可通过 policy dry-run 和 guarded dry-run 生成审计结果。该验证仍保持 `would_execute=false`，未执行真实恢复，未扩大真实 `auto_recover` 权限。

后续原则：

- 如需扩权，仅从已有低风险 fix_id 开始；
- 不新增危险动作；
- 不允许 `process_crash` 自动恢复；
- 不允许 `container_k8s` 自动恢复；
- 不允许自动执行 `kill -9`、`rm -rf`、`pip install`、`systemctl restart`、`systemctl stop`、`kubectl delete`、`kubectl apply`、权限提升或跨主机破坏性操作；
- 真实执行必须单独验证 precheck、cooldown、rollback、audit、report 和 alert。

## 最终结论

R15 自动恢复策略安全分层阶段完成。系统已具备策略校验、策略解析、dry-run、guarded dry-run、runtime gate、强制 audit/report/alert、真实 precheck、执行层 cooldown、rollback 成功/失败审计和 failure-path controlled validation。

真实自动恢复能力已被限制在显式 `safe_auto_recover` allowlist 内；默认运行配置保持 `auto_recovery_dry_run: true`。后续新增恢复域应进入 R16，并按 schema、precheck、dry-run、failure-path、isolated live validation 的顺序逐级验证。
