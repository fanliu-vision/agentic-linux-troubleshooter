# R16 第二批企业 safe 域接入

本阶段在 R16-5 negative fixture、isolated dry-run/live 验证和 `safe_enum_downgrade` 语义基础上，新增 3 个企业 safe 域：

- `optional_cache_backend_failed -> fix-cache-backend-1`
- `optional_service_unavailable -> fix-optional-service-1`
- `observability_export_failed -> fix-observability-export-1`

这些域继续沿用 registry 协议：detector 只负责精确分类，`safe_recovery.registry` 声明 fix、字段和语义，policy / runtime gate / precheck / guarded dry-run / local apply / remote apply 从 registry 派生。

## 安全边界

| event_type | 允许动作 | 语义规则 | 禁止动作 |
| --- | --- | --- | --- |
| `optional_cache_backend_failed` | 切 `cache.backend` / `cache.mode` 到 `memory`，或关闭 `cache.redis_enabled` / `cache.write_enabled` | `safe_enum_downgrade`、`disable_bool` | 不清缓存目录，不 flush Redis，不处理通用磁盘满，不处理核心 Redis 依赖 |
| `optional_service_unavailable` | 关闭可选 enrichment、recommendation、risk scoring 服务开关 | `disable_bool` | 不修改核心 DB/MQ/Redis/Kafka，不改外部服务地址或凭据 |
| `observability_export_failed` | 关闭远程 exporter，或将 exporter/sink 切到 `local` / `file` / `console` | `safe_enum_downgrade`、`disable_bool` | 不改 token、证书、网络、collector 配置，不重启 observability 后端 |

默认 `configs/projects.yaml` 仍保持 `auto_recovery_dry_run: true`。真实执行仍必须经过 allowlist、runtime gate、semantic precheck、cooldown、backup/diff、rollback 和 audit。

## negative 证据

新增 negative fixture 确保 safe 子域不吞并 manual-only 域：

- `optional_cache_backend_failed` 不吞 `disk_full` 或核心 Redis `dependency_service`；
- `optional_service_unavailable` 不吞核心 DB/MQ/Redis/Kafka `dependency_service`；
- `observability_export_failed` 不吞 `network_connectivity` 或 `auth_cert`。

## 验证

新增和扩展的测试覆盖：

- detector positive / negative regression；
- policy allowlist；
- runtime dry-run 和 live gate；
- guarded dry-run audit；
- local apply 只改受控 JSON 字段；
- `safe_enum_downgrade` 的 backup/diff/rollback；
- registry governance 一致性。
