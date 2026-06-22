# R16-5 safe_auto_recover 验证与语义加固

本阶段不新增企业 safe 域，目标是加固 R16 阶段 4 已接入的 3 个 batch-1 域，并为第二批字符串/模式类降级提前补语义护栏。

## 1. negative fixture

阶段 4 三个新域新增 negative regression fixture：

| safe 域 | negative 证据 | 期望分类 |
| --- | --- | --- |
| `optional_integration_failed` | 可选集成失败同时出现核心 `ModuleNotFoundError` | `python_env`，不得生成 safe 自动恢复候选 |
| `notification_sink_failed` | notification sink 失败同时出现 HTTP 401/token expired | `auth_cert`，不得生成 safe 自动恢复候选 |
| `queue_backpressure` | queue backpressure 同时出现 Kafka/MQ broker down | `dependency_service`，不得生成 safe 自动恢复候选 |

Detector 在同一 scope 内用这些 manual/high-risk 事件压过对应 safe 子域，避免 safe 域吞并核心依赖、鉴权证书或外部 MQ 故障。

## 2. isolated dry-run / live 验证

新增 isolated 测试覆盖：

- 默认 `auto_recovery_dry_run=true` 时，runtime gate 只生成审计，`allowed_to_execute=false`，JSON 配置不变；
- 切到 `auto_recovery_dry_run=false` 后，gate 才会返回 `would_run_r15_live`；
- local apply 只修改 registry 中显式字段；
- apply 必须生成 backup、diff 和 `applied_fixes.json`；
- rollback 能恢复原始 JSON。

验证范围限定为阶段 4 三个新域：

- `fix-optional-integration-1`
- `fix-notification-sink-1`
- `fix-queue-backpressure-1`

## 3. safe_enum_downgrade

新增语义规则 `safe_enum_downgrade`，供第二批候选中类似 `cache.backend="memory"`、`notification.mode="local"` 的模式降级使用。

安全条件：

- 旧值和目标值必须都是 string；
- 目标值必须在白名单内：`memory`、`local`、`file`、`console`；
- 旧值等于目标值时按 no-op 审计；
- `remote`、`webhook`、`redis`、`kafka` 等非白名单目标不得作为 safe 自动恢复目标。

该规则已接入本地语义判断、registry governance 允许列表和远程 apply 的嵌入编辑脚本。当前阶段不把任何新增域切到该规则，只先提供第二批接入前的安全基础。
