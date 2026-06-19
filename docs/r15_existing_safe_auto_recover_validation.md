# R15-7 existing safe_auto_recover controlled validation

## 1. 本阶段目标

R15-7 只验证已有 `safe_auto_recover` 候选在 R15 策略体系下是否能被正确识别、正确 dry-run、正确审计。本阶段不接入真实恢复执行路径，不调用真实 `AutoRecoveryRunner` 执行动作，不写真实 `state/` 或 `outputs/`。

R15-7 只验证已有 safe_auto_recover 候选，不新增恢复动作，不扩大真实自动恢复权限。

## 2. 已有 safe_auto_recover 候选范围

本阶段只覆盖当前已有低风险候选：

| event_type | 既有 fix_id | 说明 |
| --- | --- | --- |
| `network_port` | `fix-network-1` | 既有 port 类受控恢复候选 |
| `gpu_oom` | `fix-gpu-1` | 既有 GPU OOM / batch_size 类受控恢复候选 |

`fix-gpu-1` 已从现有代码和配置确认：`RemediationPolicy.DEFAULT_FIX_MAPPING`、`configs/projects.yaml`、apply executor 和 guarded dry-run allowlist 均使用该 fix id。未新增任何临时 fix id。

## 3. network_port 验证结果

构造：

```text
event_type=network_port
candidate_fix_id=fix-network-1
confidence=0.95
fingerprint=test-network-port-safe
```

验证结果：

- policy resolver 返回 `safe_auto_recover` candidate；
- policy dry-run 成功；
- guarded dry-run 生成 audit；
- `would_execute=false`；
- `dry_run=true`；
- 未执行真实恢复；
- 未写真实 `state/outputs`。

## 4. gpu_oom / batch_size 验证结果

构造：

```text
event_type=gpu_oom
candidate_fix_id=fix-gpu-1
confidence=0.95
fingerprint=test-gpu-oom-safe
```

验证结果：

- policy resolver 返回 `safe_auto_recover` candidate；
- policy dry-run 成功；
- guarded dry-run 生成 audit；
- `would_execute=false`；
- `dry_run=true`；
- 未执行真实恢复；
- 未扩大 batch_size 类动作范围。

## 5. 高风险故障降级结果

以下故障域保持保守降级：

| event_type | 预期结果 |
| --- | --- |
| `process_crash` | `manual_escalation` 或 `diagnose_only`，不得自动恢复 |
| `container_k8s` | `manual_escalation` 或 `diagnose_only`，不得执行 `kubectl` |
| `disk_full` | `manual_escalation` 或 `diagnose_only`，不得自动清理 |
| `python_env` | `manual_escalation` 或 `diagnose_only`，不得 `pip install` |
| `auth_cert` | `manual_escalation` 或 `diagnose_only`，不得自动替换证书 |

受控验证确认这些事件 `auto_recover_allowed=false`，guarded dry-run 中 `would_execute=false`。

## 6. forbidden action 阻断结果

以下 forbidden action 在 guarded dry-run 中被阻断：

```text
systemctl restart
kubectl delete
rm -rf
pip install
kill -9
```

验证结果：

- 返回 `disabled` 或 `manual_escalation`；
- `allowed_by_policy=false`；
- `would_execute=false`；
- `downgrade_reason=forbidden_action`；
- forbidden action 不会转化为执行。

## 7. 是否扩大真实 auto_recover 权限

否。

R15-7 没有新增恢复动作，没有扩大 `auto_recover` 权限，没有接入新的真实执行路径，也没有修改 detector、MonitorLoop 或 AutoRecoveryRunner 真实执行路径。

## 8. 是否建议未来进入真实小范围 live 验证

可以建议进入后续小范围验证，但必须保持以下前提：

- 仅验证已有低风险 fix id；
- 优先使用 dry-run 和受控环境；
- 不新增危险动作；
- 不允许 `process_crash` 自动恢复；
- 不允许 `container_k8s` 自动恢复；
- 不允许 `disk_full` 自动清理；
- 不允许 `python_env` 自动 `pip install`；
- 每次真实验证前必须明确 precheck、cooldown、rollback、audit、report 和 alert 期望。
