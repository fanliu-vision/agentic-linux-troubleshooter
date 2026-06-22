# R16 阶段 4：第一批企业 safe 域接入

本阶段把 3 个 R16 batch-1 候选接入 `safe_auto_recover` 链路：

- `optional_integration_failed -> fix-optional-integration-1`
- `notification_sink_failed -> fix-notification-sink-1`
- `queue_backpressure -> fix-queue-backpressure-1`

它们都沿用 R16 registry 协议：detector 负责生成精确 event，`safe_recovery.registry` 提供 fix、字段、语义规则和消息，policy / runtime gate / precheck / guarded dry-run / local apply / remote apply 从 registry 派生。

## 安全边界

本阶段只允许项目内 `config.json` 的显式 JSON 字段编辑：

| event_type | 允许动作 | 语义规则 | 禁止动作 |
| --- | --- | --- | --- |
| `optional_integration_failed` | 关闭可选 webhook、risk SDK、enrichment client 等集成开关 | `disable_bool` | 不安装 SDK，不改 token，不调用外部 API |
| `notification_sink_failed` | 关闭可选远程 notification webhook/sink，保留 file/console 降级 | `disable_bool` | 不修改 webhook token、密钥、证书或通知平台配置 |
| `queue_backpressure` | 下调 `prefetch_count`、`max_inflight`、`consumer_workers` 等配置化队列参数 | `lower_int` | 不 purge 队列，不 ack/nack 消息，不 kill/restart worker 或 broker |

默认 `configs/projects.yaml` 仍保持 `auto_recovery_dry_run: true`。即使项目 allowlist 中包含新 fix，真实执行也必须通过 runtime gate、semantic precheck、cooldown、rollback 和 audit。

## 接入点

- detector：新增 3 个精确规则和 regression fixture；
- registry：新增 3 个 `SafeRecoverySpec`；
- policy：safe candidate、legacy mapping 和 runtime `_safe_policy` 从 registry 自动派生；
- precheck：复用 registry safety spec 和阶段 2 语义规则；
- executor：local / remote apply 从 registry 支持新 fix；
- rollback / audit：沿用现有 backup、diff、applied record 与 R15 audit 字段；
- config：`projects.yaml` allowlist 显式加入 3 个新 fix，dry-run 默认不变。

## 非目标

这不是“所有企业故障自动恢复”。以下仍保持 manual-only 或 guarded-only：

- 核心依赖服务故障，例如 DB/Redis/Kafka/RabbitMQ broker unavailable；
- 认证、token、证书、HTTP 401/403；
- 核心 Python 依赖缺失或解释器环境问题；
- 进程崩溃、Kubernetes、Slurm、权限、磁盘清理；
- 没有 fallback/degraded/local mode 证据的外部集成失败。

## 验证要求

阶段 4 接入后必须通过：

- `tests/test_fault_domain_regression.py`
- `tests/test_safe_auto_recover_domain_expansion.py`
- `tests/test_safe_recovery_registry_governance.py`
- R15/R16 runtime gate、precheck、apply / rollback 相关回归
- `scripts/run_core_tests.sh`
