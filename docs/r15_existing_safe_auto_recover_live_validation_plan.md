# R15-8 existing safe_auto_recover 小范围 live 验证设计与准备

## 1. 背景

R15 已完成自动恢复策略分层、policy schema、policy validator / resolver、policy dry-run、guarded dry-run、audit 与 report/alert 集成设计，以及 existing safe_auto_recover controlled validation。

R15-7 已验证已有安全候选：

- `network_port -> fix-network-1`；
- `gpu_oom / batch_size -> fix-gpu-1`。

下一步如需 live 验证，只能从已有低风险 fix_id 开始，且必须先保持 dry-run、受控对象、明确 rollback、明确 cooldown 和人工接管路径。本阶段只是 live 验证设计与准备，不注入日志，不运行 smoke，不执行恢复动作，不修改真实 `state/outputs`。

## 2. 验证范围

R15-8 live 验证设计只包含：

```text
network_port / fix-network-1
gpu_oom / fix-gpu-1
```

明确不包含：

```text
process_crash
container_k8s
disk_full
python_env
auth_cert
slurm
dependency_service
unknown event_type
```

这些故障域仍保持：

```text
manual_escalation
diagnose_only
disabled
```

本阶段不新增故障域自动恢复，不允许 `process_crash` 自动恢复，不允许 `container_k8s` 自动恢复，不允许 `disk_full` 自动清理，不允许 `python_env` 自动 `pip install`。

## 3. live 验证前置条件

未来执行任何 live 验证前必须满足：

- daemon active/running；
- `git status --short` 干净；
- core tests 通过；
- policy validator tests 通过；
- policy dry-run tests 通过；
- guarded dry-run tests 通过；
- existing safe_auto_recover controlled validation tests 通过；
- 明确 rollback 边界；
- 明确 cooldown；
- 明确 per-cycle limit；
- 明确失败后人工接管；
- 明确不允许危险动作；
- 明确测试对象与真实服务对象隔离；
- 明确 report/alert/audit 预期；
- 明确验证结束后的观察窗口和退出标准。

## 4. network_port live 验证设计

本阶段不执行 `fix-network-1`。

未来可设计一个安全、可控的 port 场景：

- 使用专门测试项目或测试配置，不影响真实业务服务；
- 使用非关键端口，避免占用生产监听端口；
- 在验证前记录目标端口、目标进程和测试对象边界；
- 通过 dry-run 先确认 resolver 只返回 `network_port / fix-network-1`；
- 通过 guarded dry-run 先确认 `would_execute=false`、audit 完整；
- 只有在后续单独阶段明确允许时，才讨论真实执行。

确认只触发 `fix-network-1` 的方法：

- 检查 event_type 必须为 `network_port`；
- 检查 candidate_fix_id 必须为 `fix-network-1`；
- 检查 policy allowlist 仅命中 `fix-network-1`；
- 检查 forbidden action 未命中；
- 检查高风险 event 不在同一轮进入恢复候选。

确认不会影响其他服务的方法：

- 只使用测试端口；
- 只对测试项目生效；
- 不执行 `systemctl restart`；
- 不执行 `kill -9`；
- 不修改非测试配置；
- 验证前后检查目标端口与非目标端口状态。

确认 report/alert/audit 生成的方法：

- report 中包含 `strategy_layer`、`candidate_fix_id`、`dry_run`、`would_execute`、`downgrade_reason`；
- alert 中包含 `recovery_audit_summary`；
- audit 中包含 precheck、cooldown、rollback、execution_result；
- dry-run 阶段不得把 candidate 写成已执行。

确认 cooldown 生效的方法：

- 同一 fingerprint 在 cooldown 内不应重复进入恢复候选；
- 同一 event_type 在 event_type cooldown 内不应重复恢复；
- 单项目 cooldown 未满足时应降级；
- cooldown 结果必须进入 audit。

确认没有重复恢复的方法：

- 单 cycle 自动恢复候选数不超过 per-cycle limit；
- 同一 fingerprint 不重复恢复；
- report/alert 不因重复事件形成风暴；
- audit 中能看出 suppressed 或 downgrade reason。

## 5. gpu_oom / fix-gpu-1 live 验证设计

本阶段不执行 `fix-gpu-1`。

未来可设计低风险 batch_size / gpu_oom 场景：

- 使用测试项目和测试配置文件；
- 使用小型可控任务，不占用真实 GPU 任务资源；
- 优先用配置层面的 dry-run 验证 batch_size 调整候选；
- 不制造真实 GPU 资源压力；
- 不影响真实训练任务、推理服务或共享 GPU 作业。

确认只触发 `fix-gpu-1` 的方法：

- 检查 event_type 必须为 `gpu_oom`；
- 检查 candidate_fix_id 必须为 `fix-gpu-1`；
- 检查不会命中 `fix-gpu-2`、`fix-gpu-3` 或任何新增 fix；
- 检查 policy resolver 返回 `safe_auto_recover` candidate；
- 检查 guarded dry-run 仍为 `would_execute=false`。

确认不影响真实 GPU 任务的方法：

- 使用隔离测试项目；
- 不运行真实大模型或长任务；
- 不抢占共享 GPU；
- 不杀进程；
- 不改动生产训练配置；
- 验证前后确认真实 GPU 任务未受影响。

确认 report/alert/audit 生成的方法：

- report 中包含 `gpu_oom`、`fix-gpu-1`、`dry_run=true`、`would_execute=false`；
- alert 中包含 operator 接管要求或 dry-run candidate 摘要；
- audit 中记录 precheck、cooldown、rollback_available、execution_result；
- 不把 dry-run candidate 写成已恢复。

确认失败时降级 `manual_escalation` 的方法：

- precheck 不通过时降级；
- rollback 不可用时降级；
- cooldown 不满足时降级；
- candidate_fix_id 不是 `fix-gpu-1` 时降级；
- 命中 forbidden action 时进入 `disabled` 或 `manual_escalation`。

## 6. 禁止项

live 验证设计和后续执行均明确禁止：

```text
systemctl restart
systemctl stop
kill -9
rm -rf
pip install
kubectl delete
kubectl apply
权限提升
跨主机破坏性操作
```

这些动作不得出现在自动执行路径中，也不得通过 report、alert、LLM 建议、fix 描述或脚本包装间接执行。

## 7. 成功标准

未来 live 验证 PASS 标准：

- 只触发目标 event_type；
- 只命中目标 fix_id；
- precheck 通过；
- cooldown 生效；
- audit 记录完整；
- report/alert 包含恢复决策；
- 无危险操作；
- 无重复恢复；
- 无 daemon Traceback；
- 失败时能降级 `manual_escalation`；
- 不影响非测试对象；
- dry-run 阶段 `would_execute=false`；
- 真实执行阶段若未来单独批准，必须有 rollback 和人工接管路径。

## 8. 失败标准

未来 live 验证 FAIL 标准：

- 命中非目标 fix；
- 触发危险动作；
- 自动恢复越权；
- 重复恢复；
- report/alert 缺失；
- audit 缺失；
- daemon 崩溃；
- 影响非测试对象；
- dry-run 被写成已执行；
- `process_crash`、`container_k8s`、`disk_full`、`python_env` 或 `auth_cert` 进入自动恢复；
- cooldown、per-cycle limit 或 rollback 检查缺失。

## 9. 后续建议

建议下一步：

```text
R15-9：network_port / fix-network-1 live dry-run smoke
```

R15-9 仍然应先 dry-run，不执行真实恢复。只有在 dry-run smoke 验证 event_type、fix_id、precheck、cooldown、audit、report/alert 和人工接管路径均符合预期后，才可在后续单独阶段讨论是否进行真实小范围恢复验证。
