# R15-2 auto_recovery policy schema 设计

## 1. 背景

R15-1 已完成自动恢复策略安全分层设计，明确了五个策略层：

- `diagnose_only`
- `manual_escalation`
- `safe_auto_recover`
- `guarded_auto_recover`
- `disabled`

R15-2 的目标是把这些策略层转化为未来可配置的 policy schema，使策略层、风险等级、fix allowlist、precheck、rollback、cooldown、rate limit 和 audit 字段可以被结构化表达。该 schema 只用于设计，不接入当前运行逻辑，不修改 detector、MonitorLoop、policy 运行逻辑或 AutoRecoveryRunner，也不改变现有 `configs/projects.yaml`。

schema 默认必须保守：未知策略无效，未知 `event_type` 不得自动恢复，未知 `fix_id` 不得自动执行，缺少 precheck 或 rollback 的恢复动作不得进入 `safe_auto_recover`。R15-2 不新增恢复动作，不扩大 `auto_recover` 权限。

## 2. schema 总体结构

未来 policy schema 可以采用如下顶层结构：

```yaml
auto_recovery_policy:
  schema_version: "r15.2"
  default_strategy: "manual_escalation"
  dry_run_default: true
  global_limits:
    max_auto_recover_per_cycle: 1
  event_type_policies: {}
  action_allowlist: {}
  forbidden_actions: []
```

字段含义如下：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `auto_recovery_policy` | object | 顶层对象，承载未来自动恢复策略配置。 |
| `schema_version` | string | schema 版本，用于后续 validator 和兼容性判断。R15-2 建议为 `r15.2`。 |
| `default_strategy` | string | 未命中具体 `event_type` policy 时的默认策略。不得为 `guarded_auto_recover`，也不得自动恢复。 |
| `dry_run_default` | boolean | 默认是否 dry-run。建议为 `true`，尤其是 guarded 层和新策略首次启用时。 |
| `global_limits` | object | 全局自动恢复限流配置，不能放宽 R14/R15 已有安全边界。 |
| `event_type_policies` | map | 按 `event_type` 定义策略层、风险、fix allowlist、precheck、rollback 和降级行为。 |
| `action_allowlist` | map | 未来受控动作或 fix_id 的元数据 allowlist。仅 allowlist 存在不代表可以执行，还必须命中 event policy 和项目授权。 |
| `forbidden_actions` | list | 全局禁用动作。即使出现在报告建议、LLM 输出、人工说明或错误配置中，也不得自动执行。 |

建议的完整结构示意：

```yaml
auto_recovery_policy:
  schema_version: "r15.2"
  default_strategy: "manual_escalation"
  unknown_event_strategy: "diagnose_only"
  dry_run_default: true

  global_limits:
    max_auto_recover_per_cycle: 1
    max_auto_recover_per_hour: 3

  cooldown:
    fingerprint_seconds: 3600
    event_type_seconds: 1800
    project_seconds: 600

  rate_limits:
    max_per_cycle: 1
    max_per_hour: 3

  precheck_defaults:
    required: true
    require_target_match: true
    require_evidence: true
    require_rollback_available: true
    require_low_risk: true

  rollback_defaults:
    required: true
    verify_after_rollback: true

  event_type_policies: {}
  action_allowlist: {}
  forbidden_actions: []
  audit:
    required: true
```

该结构只定义未来 schema，不表示当前系统会读取或执行该配置。

## 3. 策略层字段

每个 `event_type` policy 应支持以下字段：

```text
strategy_layer
risk_level
confidence_required
allowed_fix_ids
forbidden_actions
require_precheck
require_rollback
require_operator_confirmation
cooldown
rate_limits
audit_required
fallback_strategy
```

字段解释如下：

| 字段 | 类型 | 含义 | 保守规则 |
| --- | --- | --- | --- |
| `strategy_layer` | enum | 当前 `event_type` 的策略层，只允许 R15-1 定义的五个值。 | 未知值无效，不得自动恢复。 |
| `risk_level` | enum | 事件和候选动作的综合风险，可为 `low`、`medium`、`high`、`critical`。 | 非 `low` 不得进入 `safe_auto_recover`。 |
| `confidence_required` | number | 允许进入该策略所需的最低置信度，建议范围 `0.0` 到 `1.0`。 | 置信度不足时降级。 |
| `allowed_fix_ids` | list | 该 `event_type` 可使用的受控 fix_id。 | `safe_auto_recover` 必须非空，且必须同时被项目显式允许。 |
| `forbidden_actions` | list | 针对该 `event_type` 的附加禁用动作。 | 与全局禁用动作合并生效。 |
| `require_precheck` | boolean | 是否要求执行前置检查。 | `safe_auto_recover` 和 `guarded_auto_recover` 必须为 `true`。 |
| `require_rollback` | boolean | 是否要求 rollback 可用。 | `safe_auto_recover` 必须为 `true`。 |
| `require_operator_confirmation` | boolean | 是否需要人工确认。 | `manual_escalation` 和 `guarded_auto_recover` 默认需要。 |
| `cooldown` | object | 该事件类型的冷却时间，可覆盖全局默认但不得更危险。 | 未满足 cooldown 时降级，不执行恢复。 |
| `rate_limits` | object | 该事件类型的自动恢复限流。 | 超限时不执行恢复。 |
| `audit_required` | boolean | 是否必须生成审计记录。 | 恢复候选、降级、跳过、禁止均应审计。 |
| `fallback_strategy` | enum | 条件不满足时的降级策略。 | 只能降级到 `manual_escalation`、`diagnose_only` 或 `disabled`。 |

建议增加的辅助字段：

| 字段 | 含义 |
| --- | --- |
| `dry_run` | 是否强制 dry-run；`guarded_auto_recover` 默认应为 `true`。 |
| `operator_confirmation_reason` | 需要人工确认的原因说明。 |
| `evidence_requirements` | 必须满足的证据类型，例如日志片段、目标对象、状态快照。 |
| `rollback` | 该 policy 的 rollback 配置。 |

## 4. event_type policy 示例

保守示例：

```yaml
event_type_policies:
  network_port:
    strategy_layer: safe_auto_recover
    risk_level: low
    allowed_fix_ids:
      - fix-network-1
    require_precheck: true
    require_rollback: true
    fallback_strategy: manual_escalation

  process_crash:
    strategy_layer: manual_escalation
    risk_level: high
    allowed_fix_ids: []
    require_operator_confirmation: true
```

扩展示例：

```yaml
event_type_policies:
  network_port:
    strategy_layer: safe_auto_recover
    risk_level: low
    confidence_required: 0.85
    allowed_fix_ids:
      - fix-network-1
    forbidden_actions:
      - systemctl restart
      - kill -9
    require_precheck: true
    require_rollback: true
    require_operator_confirmation: false
    cooldown:
      fingerprint_seconds: 3600
      event_type_seconds: 1800
      project_seconds: 600
    rate_limits:
      max_per_cycle: 1
      max_per_hour: 3
    audit_required: true
    fallback_strategy: manual_escalation
    rollback:
      required: true
      rollback_fix_id: rollback-network-1
      verify_after_rollback: true

  process_crash:
    strategy_layer: manual_escalation
    risk_level: high
    confidence_required: 0.80
    allowed_fix_ids: []
    require_precheck: false
    require_rollback: false
    require_operator_confirmation: true
    audit_required: true
    fallback_strategy: manual_escalation

  container_k8s:
    strategy_layer: manual_escalation
    risk_level: high
    allowed_fix_ids: []
    forbidden_actions:
      - kubectl delete
      - kubectl apply
    require_operator_confirmation: true
    audit_required: true
    fallback_strategy: manual_escalation
```

说明：

- `network_port` 只有显式 `fix_id` 才可能进入 `safe_auto_recover`，并且仍需项目策略允许、precheck 通过、rollback 可用、cooldown/rate limit 满足；
- `process_crash` 默认人工升级，不自动重启服务，不自动替换进程；
- `container_k8s` 默认人工升级，不自动执行 `kubectl delete`、`kubectl apply` 或任何集群资源变更；
- 未知 `event_type` 默认 `diagnose_only` 或 `manual_escalation`，不得自动恢复。

## 5. forbidden actions

schema 必须支持全局禁用以下动作：

```text
kill -9
rm -rf
pip install
systemctl restart
systemctl stop
kubectl delete
kubectl apply
权限提升
跨主机破坏性操作
```

建议表达为：

```yaml
forbidden_actions:
  - kill -9
  - rm -rf
  - pip install
  - systemctl restart
  - systemctl stop
  - kubectl delete
  - kubectl apply
  - 权限提升
  - 跨主机破坏性操作
```

这些动作即使出现在建议中，也不得自动执行。它们也不得通过别名、包装脚本、LLM 生成命令、fix 描述或错误的 `allowed_fix_ids` 绕过。

校验器应至少检查：

- `forbidden_actions` 不得出现在 `action_allowlist` 的命令或描述中；
- `forbidden_actions` 不得被放进 `allowed_fix_ids`；
- `allowed_fix_ids` 指向的 fix 元数据不得包含禁用命令；
- event-level `forbidden_actions` 与 global `forbidden_actions` 合并生效；
- 命中禁用动作时策略层强制降级为 `disabled` 或 `manual_escalation`。

## 6. cooldown / rate limit schema

建议 schema：

```yaml
cooldown:
  fingerprint_seconds: 3600
  event_type_seconds: 1800
  project_seconds: 600

rate_limits:
  max_per_cycle: 1
  max_per_hour: 3
```

字段说明：

| 字段 | 含义 |
| --- | --- |
| `cooldown.fingerprint_seconds` | 同一 fingerprint 自动恢复后的冷却时间。 |
| `cooldown.event_type_seconds` | 同一 `event_type` 自动恢复后的冷却时间。 |
| `cooldown.project_seconds` | 同一项目任意自动恢复后的冷却时间。 |
| `rate_limits.max_per_cycle` | 单轮最多允许的自动恢复次数。 |
| `rate_limits.max_per_hour` | 单小时最多允许的自动恢复次数。 |

规则：

- cooldown 不满足则降级，不执行恢复；
- rate limit 超限则不执行恢复；
- 自动恢复限流不得绕过 R14 report/alert rate limit；
- report/alert rate limit 不等于恢复授权；
- 多事件窗口中不得因为事件数量增加而放宽 `max_per_cycle`；
- 自动恢复失败、rollback 失败或 repeated fingerprint 应触发更保守的 cooldown。

与 `monitors/rate_limit_tracker.py` 的关系：

- R14 tracker 当前关注 event/report/alert 的 runtime flood control；
- R15 policy schema 关注 future auto_recover 动作级 cooldown/rate limit；
- 两者应互补，不应互相覆盖；
- R15-2 不修改 tracker，不改变 report/alert 限流实现。

## 7. precheck schema

建议 schema：

```yaml
precheck:
  required: true
  require_target_match: true
  require_evidence: true
  require_rollback_available: true
  require_low_risk: true
```

字段说明：

| 字段 | 含义 |
| --- | --- |
| `required` | 是否必须执行 precheck。`safe_auto_recover` 必须为 `true`。 |
| `require_target_match` | 事件 evidence 中的目标对象必须与 fix 目标匹配。 |
| `require_evidence` | 必须有足够日志、状态或检测证据支撑恢复。 |
| `require_rollback_available` | 执行前必须确认 rollback 可用。 |
| `require_low_risk` | 只允许低风险动作进入自动恢复。 |

precheck 失败时必须降级为以下策略之一：

```text
manual_escalation
diagnose_only
disabled
```

建议降级规则：

| precheck 失败原因 | 建议降级 |
| --- | --- |
| 目标对象不匹配 | `manual_escalation` |
| evidence 不足 | `diagnose_only` |
| rollback 不可用 | `manual_escalation` |
| 风险不是 low | `manual_escalation` |
| 命中 forbidden action | `disabled` |
| cooldown 或 rate limit 不满足 | `manual_escalation` 或 `diagnose_only` |

precheck 输出必须进入 audit record；对于已经发起恢复候选但被 precheck 阻断的事件，也应记录阻断原因。

## 8. rollback schema

建议 schema：

```yaml
rollback:
  required: true
  rollback_fix_id: rollback-network-1
  verify_after_rollback: true
```

字段说明：

| 字段 | 含义 |
| --- | --- |
| `required` | 是否要求 rollback。`safe_auto_recover` 必须为 `true`。 |
| `rollback_fix_id` | 对应的受控 rollback id。必须是可审计、可验证的已知回滚动作。 |
| `verify_after_rollback` | rollback 后是否需要验证目标状态。建议为 `true`。 |

规则：

- 无 rollback 默认不得 `safe_auto_recover`；
- rollback 必须在 action 执行前确定，不能失败后临时推断；
- rollback 失败必须 alert；
- rollback 结果必须进入 audit/report；
- rollback 不得包含 forbidden action；
- rollback 不得扩大权限；
- rollback 失败后不得重复执行同一恢复动作；
- rollback 失败后应进入更严格 cooldown，并要求人工接管。

## 9. audit schema

建议 audit record 字段如下：

```text
event_type
fingerprint
strategy_layer
selected_policy
action
precheck_result
cooldown_result
rate_limit_result
rollback_available
execution_result
rollback_result
downgrade_reason
created_at
```

字段说明：

| 字段 | 含义 |
| --- | --- |
| `event_type` | 当前事件类型。 |
| `fingerprint` | 当前事件 fingerprint。 |
| `strategy_layer` | 最终采用的策略层。 |
| `selected_policy` | 命中的 policy 名称、版本或摘要。 |
| `action` | 候选或实际动作；未执行时记录 `none` 或 `skipped`。 |
| `precheck_result` | precheck 结果、失败项和证据摘要。 |
| `cooldown_result` | fingerprint、event_type、project 级 cooldown 检查结果。 |
| `rate_limit_result` | per-cycle、per-hour 等恢复限流检查结果。 |
| `rollback_available` | 是否存在 rollback。 |
| `execution_result` | 执行结果；R15-2 只设计，不执行真实恢复。 |
| `rollback_result` | rollback 结果；未触发时记录 `not_required` 或 `not_run`。 |
| `downgrade_reason` | 降级、跳过或禁用的原因。 |
| `created_at` | 审计记录创建时间。 |

示例：

```json
{
  "event_type": "network_port",
  "fingerprint": "example-fingerprint",
  "strategy_layer": "safe_auto_recover",
  "selected_policy": "network_port@r15.2",
  "action": "fix-network-1",
  "precheck_result": {
    "passed": true,
    "failed_checks": []
  },
  "cooldown_result": {
    "fingerprint": "allowed",
    "event_type": "allowed",
    "project": "allowed"
  },
  "rate_limit_result": {
    "max_per_cycle": "allowed",
    "max_per_hour": "allowed"
  },
  "rollback_available": true,
  "execution_result": "not_run_in_r15_2_design",
  "rollback_result": "not_required",
  "downgrade_reason": "",
  "created_at": "2026-06-19T00:00:00Z"
}
```

audit 必须覆盖四类情况：允许执行、降级、跳过、禁止。R15-2 不写入真实 `state/` 或 `outputs/`。

## 10. schema 校验规则

建议 validator dry-run 至少实现以下校验规则：

| 规则 | 说明 | 失败处理 |
| --- | --- | --- |
| 未知 `strategy_layer` 无效 | 只允许 R15-1 定义的五层。 | schema invalid。 |
| `safe_auto_recover` 必须有 `allowed_fix_ids` | 没有受控 fix_id 就不能自动恢复。 | schema invalid 或降级。 |
| `safe_auto_recover` 必须 `require_precheck` | 缺少 precheck 不允许自动恢复。 | schema invalid。 |
| `safe_auto_recover` 必须 `require_rollback` | 缺少 rollback 不允许自动恢复。 | schema invalid。 |
| `guarded_auto_recover` 默认 dry-run | guarded 层当前不得默认真实执行。 | schema invalid 或强制 dry-run。 |
| `forbidden_actions` 不得出现在 `allowed_fix_ids` | 禁用动作不能伪装为 fix_id 或 allowlist 项。 | schema invalid。 |
| unknown `event_type` 不能自动恢复 | 未配置事件类型只能走默认保守策略。 | 降级为 `diagnose_only` 或 `manual_escalation`。 |
| `default_strategy` 不得为 `guarded_auto_recover` | 默认策略不能选择需要多重保护的执行层。 | schema invalid。 |

补充校验建议：

- `default_strategy` 不得为 `safe_auto_recover`；
- `disabled` 策略不得包含 `allowed_fix_ids`；
- `manual_escalation` 不得声明自动执行动作；
- `risk_level` 为 `high` 或 `critical` 时不得进入 `safe_auto_recover`；
- `confidence_required` 必须在 `0.0` 到 `1.0` 之间；
- event-level cooldown 不得比全局默认更激进，除非未来有显式安全评审；
- `rollback.rollback_fix_id` 不得为空，且不得命中 forbidden action；
- `audit_required` 对恢复候选必须为 `true`。

## 11. 后续实现路线

R15 后续阶段建议：

| 阶段 | 目标 | 说明 |
| --- | --- | --- |
| R15-3 | precheck / cooldown 机制设计 | 细化 precheck 输入输出、target match、evidence、rollback available、动作级 cooldown 和失败降级。 |
| R15-4 | policy schema validator dry-run | 只做 schema 读取和校验 dry-run，不接入真实恢复，不改变 policy 执行路径。 |
| R15-5 | guarded auto_recover dry-run | 为 guarded 层设计 dry-run 审计，不执行真实动作，不扩大动作面。 |
| R15-6 | 审计与 report/alert 集成 | 设计 audit record 如何进入 report/alert，并确认限流下仍保留关键安全信息。 |

R15-2 不执行真实恢复，不修改真实 configs，不新增恢复动作，不扩大 `auto_recover` 权限。

## 12. R15-3 validator / resolver 实现说明

R15-3 新增独立模块 `policies/auto_recovery_policy.py`，用于把 R15-2 schema 设计转换为结构化校验和策略解析结果。该模块提供以下对象：

- `StrategyLayer`
- `RiskLevel`
- `PolicyValidationError`
- `EventTypePolicy`
- `AutoRecoveryPolicy`
- `AutoRecoveryDecision`
- `validate_policy()`
- `resolve_policy_for_event()`

R15-3 的实现边界如下：

- 只做 policy schema validator / resolver；
- 不接入 `MonitorLoop`；
- 不接入 `AutoRecoveryRunner`；
- 不修改既有 `RemediationPolicy` 运行逻辑；
- 不读取或修改真实 `configs/projects.yaml`；
- 不新增恢复动作；
- 不扩大 `auto_recover` 权限；
- resolver 返回的 `safe_auto_recover` 只表示策略候选，不表示已经执行恢复。

R15-3 resolver 保持保守默认：

| event_type | 默认解析结果 |
| --- | --- |
| 未知 `event_type` | `diagnose_only` 或 `manual_escalation`，不得自动恢复 |
| `process_crash` | `manual_escalation` |
| `container_k8s` | `manual_escalation` |
| `disk_full` | `manual_escalation` |
| `python_env` | `manual_escalation` |
| `auth_cert` | `manual_escalation` |
| `network_port` | 仅在命中显式允许的 `fix-network-1` 时返回 `safe_auto_recover` candidate |
| `gpu_oom` | 仅在命中显式允许的 `fix-gpu-1` 时返回 `safe_auto_recover` candidate |

该实现不会改变当前真实可执行恢复范围。R15-3 后，真实恢复仍由既有运行链路、既有 policy allowlist 和既有 AutoRecoveryRunner 边界决定。

## 13. R15-4 policy schema dry-run 说明

R15-4 新增独立 dry-run 模块 `policies/auto_recovery_policy_dry_run.py`，用于离线读取 policy schema 示例、调用 `validate_policy()`、对样例 event 调用 `resolve_policy_for_event()`，并生成结构化 dry-run 结果和 Markdown 报告。

R15-4 dry-run 输入包括：

```text
policy dict
sample_events list
```

每个 sample event 至少包含：

```text
event_type
fingerprint
confidence
candidate_fix_id
```

dry-run 输出至少包含：

```text
policy_valid
validation_errors
decisions
summary
```

每个 decision 至少包含：

```text
event_type
fingerprint
strategy_layer
auto_recover_allowed
dry_run
selected_fix_id
downgrade_reason
operator_required
audit_required
```

R15-4 的安全边界：

- 不执行任何恢复动作；
- 不调用 `AutoRecoveryRunner`；
- 不接入 `MonitorLoop`；
- 不修改 detector；
- 不修改真实 `configs/projects.yaml`；
- 默认不写任何文件；
- 如需写 dry-run report，只允许写入测试临时目录或 `acceptance_artifacts/`；
- 不写真实 `state/`；
- 不写真实 `outputs/`；
- `safe_auto_recover` 在 dry-run 中最多表示 `auto_recover_allowed=true` 且 `dry_run=true`，不能表示已经执行。

R15-4 示例 YAML 中新增 `r15_dry_run_sample_events`，仅用于文档和测试样例，不属于真实运行配置，也不会被现有运行链路读取。

## 14. R15-5 guarded auto_recover dry-run 说明

R15-5 新增独立模块 `recovery/guarded_auto_recover_dry_run.py`，用于表达 future guarded auto_recover 的 dry-run 决策和审计模型。该模块不接入真实恢复执行路径，不调用 `AutoRecoveryRunner`，不新增恢复动作，不扩大 `auto_recover` 权限。

guarded dry-run 输入包括：

```text
event_type
fingerprint
candidate_fix_id
strategy_layer
policy_decision
precheck_result
cooldown_result
rollback_available
```

guarded dry-run 输出包括：

```text
event_type
fingerprint
strategy_layer
candidate_fix_id
would_execute
dry_run
allowed_by_policy
precheck_passed
cooldown_allowed
rollback_available
operator_required
downgrade_reason
audit_record
```

R15-5 固定执行边界：

```text
would_execute=false
dry_run=true
execution_result=not_run_guarded_dry_run
```

`allowed_by_policy=true` 只表示 policy candidate、precheck、cooldown、rollback 和 forbidden-action 检查都满足 guarded dry-run 条件，不表示已经执行恢复。任何真实恢复仍由既有运行链路、既有 policy allowlist 和既有 `AutoRecoveryRunner` 边界决定。

guarded dry-run 的保守策略：

| 场景 | dry-run 结果 |
| --- | --- |
| `network_port + fix-network-1` | 可生成 guarded dry-run candidate，但不执行 |
| `gpu_oom + fix-gpu-1` | 可生成 guarded dry-run candidate，但不扩大动作范围 |
| `process_crash` | `manual_escalation` |
| `container_k8s` | 不允许 `kubectl`，命中禁用动作时 `disabled` |
| `disk_full` | 不允许 `rm -rf`，命中禁用动作时 `disabled` |
| `python_env` | 不允许 `pip install`，命中禁用动作时 `disabled` |
| `auth_cert` | `manual_escalation`，不得自动替换证书 |
| unknown `event_type` | `diagnose_only` 或 `manual_escalation` |

guarded dry-run audit record 必须记录 policy、precheck、cooldown、rollback、operator required、downgrade reason、forbidden action、execution result 和 created_at。R15-5 不写真实 `state/` 或 `outputs/`。
