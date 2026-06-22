# R16 safe_auto_recover 语义级 precheck 说明

## 1. 目标

R16 阶段 2 对已有 R15 safe 域增加语义级安全判断，不新增 `event_type`，不新增 `fix_id`，不扩大真实自动恢复权限。

本阶段解决的问题是：过去 precheck 主要确认字段存在，执行层会把字段设置为固定目标值。对 `gpu_oom`、`worker_overload` 等参数类恢复来说，固定设置可能在某些配置下变成反向调高，例如 `batch_size=2` 被设置为 `4`。R16 阶段 2 改为判断“变更方向是否安全”。

## 2. 语义规则

现有 safe 域的字段候选增加 `semantic_rule`：

| semantic_rule | 适用域 | 安全条件 | no-op 条件 | unsafe 条件 |
| --- | --- | --- | --- | --- |
| `lower_int` | `gpu_oom`、`worker_overload` | 旧值和目标值均为 int，且旧值大于目标值 | 旧值等于目标值 | 旧值小于目标值、非 int、bool |
| `disable_bool` | `cache_write_failed`、`optional_dependency_missing` | `true -> false` | 旧值已是 `false` | 非 bool、或不是 `true -> false` |
| `port_available` | `network_port` | 目标端口可绑定 | 旧值已等于目标端口 | 目标端口不可用、目标值不是合法端口 |
| `safe_enum_downgrade` | R16 第二批字符串/模式降级候选 | 旧值和目标值均为 string，且目标值为 `memory`、`local`、`file`、`console` 之一 | 旧值已等于目标值 | 目标值不在白名单、非 string |

## 3. precheck 行为

runtime precheck 会在 `planned_edits` 中记录：

- `semantic_rule`
- `semantic_status`
- `semantic_safe`
- `semantic_reason`
- `actionable`
- `no_op`

并在顶层记录：

- `actionable_planned_edits`
- `no_op_planned_edits`
- `unsafe_planned_edits`
- `actionable_edit_count`
- `semantic_status`
- `no_op`

如果所有候选字段都已处于安全目标值，gate 不进入 apply，审计结果为 `not_run_r15_no_op`，降级原因为 `no_op_already_safe`。

如果命中 unsafe 语义，gate 不进入 apply，降级原因为 `unsafe_semantic_transition`。

## 4. 执行层保护

local / remote apply executor 也执行同样的语义判断：

- unsafe 字段不会写入；
- no-op 字段不会生成 backup/diff；
- no-op 不写入 `applied_fixes.json` 或 `remote_applied_fixes.json`；
- 只有真实写入且生成 backup/diff 的修改才会进入 rollback 记录；
- remote apply 的语义检查在远程 Python 编辑脚本内执行，端口可用性也在远程目标上判断。

这保证即使绕过 runtime gate 直接调用 `/apply <fix_id>`，也不能把 safe fix 变成反向升配或无意义写入。

## 5. 验证

新增测试：

```bash
.venv/bin/python -m pytest tests/test_safe_recovery_semantic_precheck.py -q
```

已验证：

- `batch_size=2` 不会被 `fix-gpu-1` 调高到 `4`；
- `worker_concurrency=1` 不会被 `fix-worker-1` 调高到 `2`；
- `cache_enabled=false` 被审计为 no-op，不执行 apply，不写 apply record；
- `fix-network-1` 在目标端口不可用时被 precheck 阻断；
- 直接调用 local apply 也无法绕过上述语义保护。
- `safe_enum_downgrade` 只允许模式降级到 `memory/local/file/console`，拒绝 `remote` 或非字符串目标值。

R15 回归组合和 core baseline 已通过。
