# R15-6 auto_recovery audit 与 report/alert 集成设计

## 背景

R15-1 到 R15-5 已完成自动恢复策略分层、policy schema、validator / resolver、policy dry-run 和 guarded auto_recover dry-run。当前系统已经可以在离线或 dry-run 场景中表达 policy decision、策略降级、forbidden action 阻断、precheck / cooldown / rollback 状态，以及 guarded dry-run 的 `would_execute=false` 安全边界。

R15-6 的目标是设计 auto_recovery audit 如何供 report/alert 使用。本阶段仅完成设计，不接入真实执行，不修改 detector、MonitorLoop、AutoRecoveryRunner，不写真实 `state/` 或 `outputs/`，不新增恢复动作，也不扩大 `auto_recover` 权限。

audit 的定位是解释和追踪策略判断，不是执行授权。任何 report/alert 中出现的 audit 信息都不能绕过 policy、不能改变 strategy_layer，也不能把 dry-run 写成已经执行。

## audit 来源

auto_recovery audit 可以来自以下来源：

```text
policy validator
policy resolver
policy dry-run
guarded auto_recover dry-run
future AutoRecoveryRunner
```

各来源职责建议如下：

| 来源 | 职责 | 当前状态 |
| --- | --- | --- |
| policy validator | 校验 schema 是否安全，输出 validation error | R15-3 已实现 |
| policy resolver | 将 event + candidate fix 解析为结构化策略决策 | R15-3 已实现 |
| policy dry-run | 批量验证 schema 和样例 event，生成 dry-run summary | R15-4 已实现 |
| guarded auto_recover dry-run | 表达 guarded candidate、precheck、cooldown、rollback 和 audit 结果 | R15-5 已实现 |
| future AutoRecoveryRunner | 未来真实执行后补充 apply/rerun/rollback 结果 | 当前不接入 |

## audit 字段

建议统一 audit record 字段：

```text
event_type
fingerprint
strategy_layer
selected_policy
candidate_fix_id
would_execute
dry_run
precheck_result
cooldown_result
rate_limit_result
rollback_available
operator_required
downgrade_reason
execution_result
rollback_result
created_at
```

字段说明：

| 字段 | 说明 |
| --- | --- |
| `event_type` | 当前事件类型 |
| `fingerprint` | 当前事件 fingerprint |
| `strategy_layer` | 最终策略层，例如 `manual_escalation`、`diagnose_only`、`disabled` |
| `selected_policy` | 命中的 policy 名称、版本或摘要 |
| `candidate_fix_id` | 候选 fix id；无候选时为空 |
| `would_execute` | 是否理论上会进入执行；R15 guarded dry-run 固定为 `false` |
| `dry_run` | 是否 dry-run；R15-4/R15-5 固定为 `true` |
| `precheck_result` | precheck 结果、失败项和原因 |
| `cooldown_result` | cooldown 检查结果 |
| `rate_limit_result` | rate limit 检查结果 |
| `rollback_available` | rollback 是否可用 |
| `operator_required` | 是否需要人工确认或人工接管 |
| `downgrade_reason` | 降级、跳过或禁用原因 |
| `execution_result` | 执行结果；dry-run 应为 `not_run_*` |
| `rollback_result` | rollback 结果；dry-run 应为 `not_run_*` 或 `not_required` |
| `created_at` | audit 创建时间 |

## report 集成

未来 report 可增加 `auto_recovery_audit_summary` 小节，但不得改变现有 event 判断和执行链路。

report 中建议包含：

- `strategy_layer`；
- `dry_run`；
- `would_execute`；
- `downgrade_reason`；
- `operator_required`；
- precheck / cooldown / rollback 摘要；
- forbidden action 命中情况；
- audit summary；
- `execution_result` 和 `rollback_result`。

report 文案原则：

- `safe_auto_recover` 或 `guarded_auto_recover` candidate 只能写为候选；
- `dry_run=true` 必须明确说明未执行；
- `would_execute=false` 必须明确说明不会进入真实恢复；
- `forbidden_action` 必须显式写入安全边界；
- LLM 生成 report 时不得把 audit candidate 改写成已恢复。

## alert 集成

alert 应保留最小但关键的 recovery audit 信息，便于负责人快速判断是否需要人工介入。

alert 中建议包含：

- `strategy_layer`；
- `auto_recover_allowed`；
- `dry_run`；
- `downgrade_reason`；
- `operator_required`；
- `recovery_audit_summary`；
- `forbidden_action`。

alert 文案原则：

- manual escalation 必须突出 `operator_required=true`；
- forbidden action 必须优先显示；
- dry-run candidate 不得写成自动恢复成功；
- rollback 不可用、precheck 失败、cooldown 不满足都应进入告警摘要；
- alert rate limit 不应隐藏关键恢复失败或 forbidden action。

## rate limit

recovery audit 与 report/alert rate limit 的关系应保持保守：

- recovery audit 不应完全丢失；
- 至少保留本地 audit summary；
- 不隐藏恢复失败；
- 不隐藏 forbidden action；
- 不因为 report/alert 被限流就改变策略决策；
- 不因为 audit 存在就绕过 R14 report/alert rate limit；
- flood control 只能限制产物和通知风暴，不能授权恢复动作。

如果 report 或 alert 被 rate limit 抑制，后续实现应至少保留一个轻量 audit summary，记录 event、fingerprint、strategy_layer、downgrade_reason、forbidden_action 和执行状态。

## 安全边界

R15-6 集成设计必须保持以下边界：

- audit 不授权执行；
- report/alert 不绕过 policy；
- LLM 不改变 `strategy_layer`；
- forbidden action 不转化为执行；
- dry-run 不得视为已执行；
- `would_execute=false` 不得被解释为恢复成功；
- `execution_result=not_run_guarded_dry_run` 不得被写成 apply/rerun 成功；
- audit summary 不得触发 `AutoRecoveryRunner`；
- 不写真实 `state/` 或 `outputs/`；
- 不新增恢复动作，不扩大 `auto_recover` 权限。

## 后续建议

后续可以按以下顺序推进：

| 阶段 | 建议 |
| --- | --- |
| audit store | 设计独立 audit store，先 dry-run，再决定是否写入真实 state |
| report 注入 audit summary | 在 report 生成路径中加入受控 audit summary，不改变策略和执行 |
| alert 注入 audit summary | 在 alert payload 中加入关键 audit 字段，并遵守 rate limit |
| 真实执行单独验证 | 仅针对已有低风险 fix_id，单独设计 controlled validation |

任何真实执行集成都必须独立阶段实施，并重新验证 policy、precheck、cooldown、rollback、audit、report 和 alert 的一致性。
