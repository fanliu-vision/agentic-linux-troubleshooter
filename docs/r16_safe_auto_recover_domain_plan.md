# R16 safe_auto_recover 准入标准与候选矩阵

## 1. 目标

R16 的目标是新增或细化企业级 `safe_auto_recover` 故障域，但本文件只完成第一阶段：定义准入标准、候选矩阵和推进顺序。

本阶段不修改 detector、policy、registry、runtime gate、precheck、apply executor 或真实执行路径，不新增真实自动恢复权限。

R16-0 已完成 registry domain policy 收敛。R16 后续新增恢复域时，必须先通过本文准入，再进入 registry、测试和 live dry-run 验证。

## 2. 总原则

所有 R16 safe 候选必须继续遵守 R15/R16-0 安全边界：

- 只允许项目内 JSON 配置变更；
- 只允许显式注册的 `event_type -> fix_id`；
- 只允许修改显式列出的字段；
- 必须有 backup、diff、rollback；
- 必须经过 runtime gate、precheck、cooldown、audit；
- 默认保持 `auto_recovery_dry_run=true`；
- 项目 `policy.allow_auto_apply` 必须显式 allowlist 对应 `fix_id`；
- 找不到受控字段、证据不足、风险升高或策略不明确时必须降级。

以下动作仍禁止进入 `safe_auto_recover`：

- 重启、停止或 kill 进程；
- 删除文件、清理目录或清磁盘；
- 安装依赖或修改解释器环境；
- 修改证书、token、密钥或权限；
- 执行 `kubectl` / systemd / Slurm 控制动作；
- 提权、跨主机破坏性操作或任意 shell 脚本；
- 修改核心业务配置、核心依赖地址或不可回滚状态。

## 3. 准入硬门槛

一个企业故障域只有同时满足以下条件，才可以进入 `R16 batch-1 safe`：

| 准入项 | 要求 | 未满足时 |
| --- | --- | --- |
| 故障边界 | event_type 必须比通用域更窄，例如 optional integration、notification sink、queue 参数 | 转 `manual_escalation` 或 `guarded-only` |
| 低风险动作 | 只关闭可选能力、切换本地降级模式或降低参数 | 转 `manual_escalation` |
| 配置范围 | 只修改项目目录内 JSON 文件，默认 `config.json` | 阻断 |
| 字段范围 | 字段必须显式列出，不能用通配符或动态路径 | 阻断 |
| 变更方向 | boolean 只能安全降级，数值只能下调，mode 只能切到本地/内存/file/console | 阻断 |
| 证据要求 | 日志必须同时说明故障对象和可降级语义，例如 optional、fallback、degraded、non-critical | 证据不足时 `diagnose_only` |
| rollback | 执行前必须可生成 backup 和 diff，并能回滚最近一次 apply | 阻断 |
| allowlist | fix_id 必须在 registry domain policy、runtime gate、project policy overlay 中同时存在 | 阻断 |
| audit | report、alert、cycle summary 必须保留 gate/precheck/apply/rollback 字段 | 阻断 |
| live 验证 | 先 dry-run，再 isolated live dry-run，再小范围 live recovery | 未验证前不得 live |

## 4. 候选分类

R16 候选分三类：

- `R16 batch-1 safe`：优先推进，可在后续阶段进入 registry 和受控验证。
- `guarded-only`：只允许 dry-run、审计和人工确认设计，暂不允许无人值守真实执行。
- `manual-only`：保持人工升级或诊断，不进入自动恢复。

本文中的新 `fix_id` 均为拟定名称，不代表当前系统已支持或已授权。

## 5. R16 batch-1 safe 候选

这些候选都属于“可选能力降级类”或“参数下调类”，与现有 R15 safe 域保持同一安全模型。

| event_type | 拟定 fix_id | 允许字段 | 禁止动作 | 回滚方式 | 证据要求 | 降级策略 |
| --- | --- | --- | --- | --- | --- | --- |
| `optional_integration_failed` | `fix-optional-integration-1` | `optional_webhook_enabled=false`、`risk_sdk_enabled=false`、`enrichment_client_enabled=false`、`optional_integrations.risk_sdk.enabled=false`、`optional_integrations.enrichment.enabled=false` | 不安装 SDK，不修改 Python 环境，不改 token，不调用外部 API | JSON backup + diff；rollback 最近一次 apply | 日志包含 optional/integration/plugin unavailable，并明确存在 fallback/local rule/degraded mode | 无 fallback 证据转 `manual_escalation`；命中 `ModuleNotFoundError` 核心依赖转 `python_env` manual |
| `notification_sink_failed` | `fix-notification-sink-1` | `notification.webhook_enabled=false`、`notification.file_enabled=true`、`notification.console_enabled=true`、`notification.channels=["console","file"]`、`notification.mode="local"` | 不修改 webhook token，不刷新密钥，不访问通知平台管理 API | JSON backup + diff；rollback 最近一次 apply | 日志指向非核心通知 sink 失败，例如 webhook timeout/HTTP 5xx，并且 file/console 通道可用 | 涉及 token/401/403/cert 时转 `auth_cert` manual；所有通知通道失败转 manual |
| `queue_backpressure` | `fix-queue-backpressure-1` | `prefetch_count=2`、`max_inflight=10`、`consumer_workers=2`、`queue.prefetch_count=2`、`queue.max_inflight=10`、`queue.consumer_workers=2` | 不 kill worker，不重启服务，不 purge 队列，不 ack/nack 消息 | JSON backup + diff；rollback 最近一次 apply | 日志包含 queue backpressure、consumer lag、prefetch too high、max inflight exhausted，且对象是本项目配置化消费者 | 如果是 broker 宕机、消息丢失或外部 MQ 故障，转 `dependency_service` manual |
| `optional_cache_backend_failed` | `fix-cache-backend-1` | `cache.backend="memory"`、`cache.mode="memory"`、`cache.redis_enabled=false`、`cache.write_enabled=false`、`feature_cache_enabled=false` | 不删除缓存目录，不清理 Redis，不 flush cache，不处理通用磁盘满 | JSON backup + diff；rollback 最近一次 apply | 日志说明可选 cache backend 不可用，且可切换 memory/local cache | `No space left` 非 cache 上下文转 `disk_full` manual；核心 Redis/DB 依赖故障转 manual |

### batch-1 推进顺序

建议按以下顺序推进：

1. `optional_integration_failed`：最接近现有 `optional_dependency_missing`，风险最低。
2. `notification_sink_failed`：只降级通知后端，要求严格排除 auth/cert/token。
3. `queue_backpressure`：与现有 `worker_overload` 相邻，但需要补参数下调语义。
4. `optional_cache_backend_failed`：与现有 `cache_write_failed` 相邻，但必须严格排除通用 `disk_full` 和核心依赖故障。

## 6. guarded-only 候选

这些候选可能有企业价值，但语义或影响范围更复杂。R16 阶段只建议做 dry-run、审计、precheck 设计和人工确认流程，不进入无人值守真实执行。

| event_type | 拟定 fix_id | 允许字段 | 禁止动作 | 回滚方式 | 证据要求 | 降级策略 |
| --- | --- | --- | --- | --- | --- | --- |
| `config_error_optional_default` | `fix-config-optional-default-1` | `feature_flags.optional_feature_enabled=false`、`optional_config.enabled=false`、`strict_optional_validation=false` | 不补核心配置，不猜测业务默认值，不修改数据库/队列/鉴权地址 | dry-run 生成 planned edits；真实执行需人工确认 | 日志明确缺失的是 optional config，且服务有默认降级路径 | 核心配置缺失、invalid port/path、JSON/YAML 格式错误转 `config_error` manual |
| `gpu_precision_optimization` | `fix-gpu-precision-1` | `precision="bf16"`、`gradient_checkpointing=true`、`training.gradient_checkpointing=true` | 不修改模型结构，不换设备，不杀训练任务，不自动重启作业 | dry-run + planned edits；真实执行需人工确认 | 日志为 GPU OOM 后续优化，且 batch size 已处于安全下限 | 首次 OOM 优先走现有 `gpu_oom/fix-gpu-1`；数值稳定性风险转 guarded/manual |
| `optional_dependency_service_unavailable` | `fix-optional-service-1` | `optional_services.enrichment.enabled=false`、`optional_services.recommendation.enabled=false`、`enrichment_enabled=false` | 不修改外部服务，不改 DB/Redis/Kafka 地址，不重启依赖服务 | dry-run + planned edits；真实执行需人工确认 | 日志明确外部服务是 optional enrichment/recommendation，并存在 degraded mode | 核心 DB/MQ/Redis 故障转 `dependency_service` manual |
| `read_only_mode_recommended` | `fix-readonly-mode-1` | `write_features_enabled=false`、`read_only_mode=true`、`optional_writes_enabled=false` | 不修改数据库权限，不停写入服务，不迁移数据 | dry-run + planned edits；真实执行需人工确认 | 日志说明写入路径可临时关闭且读路径可服务业务 | 涉及数据一致性、支付、订单写入、库存写入时 manual-only |

## 7. manual-only 范围

以下故障域继续保持人工升级或诊断，不进入 R16 safe 候选。

| event_type / 场景 | fix_id | 允许字段 | 禁止动作 | 回滚方式 | 证据要求 | 降级策略 |
| --- | --- | --- | --- | --- | --- | --- |
| `process_crash` / 进程崩溃 | `<none>` | 无 | 不 `systemctl restart`，不 kill，不替换二进制 | 不适用 | core dump、segfault、exit code、systemd failed | `manual_escalation` |
| `container_k8s` / Kubernetes 异常 | `<none>` | 无 | 不 `kubectl delete/apply/rollout restart` | 不适用 | CrashLoopBackOff、ImagePullBackOff、OOMKilled、调度失败 | `manual_escalation` |
| `disk_full` / 通用磁盘满 | `<none>` | 无 | 不 `rm -rf`，不清理目录，不压缩/删除日志 | 不适用 | No space left、inode exhausted、quota exceeded | `manual_escalation` |
| `python_env` / 核心依赖缺失 | `<none>` | 无 | 不 `pip install`，不改解释器，不改 venv/conda | 不适用 | ModuleNotFoundError、ImportError、pip/interpreter mismatch | `manual_escalation` |
| `auth_cert` / 鉴权和证书 | `<none>` | 无 | 不改 token、secret、certificate、ACL | 不适用 | 401/403、token expired、cert verify failed | `manual_escalation` |
| `dependency_service` / 核心依赖服务 | `<none>` | 无 | 不改 DB/Redis/Kafka/MQ 状态，不重启外部依赖 | 不适用 | DB/MQ/Redis/Kafka unavailable、pool exhausted | `manual_escalation` |
| `permission_denied` / 权限不足 | `<none>` | 无 | 不 chmod/chown，不 sudo，不提权 | 不适用 | EACCES、operation not permitted、access denied | `manual_escalation` |
| `slurm` / 调度器问题 | `<none>` | 无 | 不 scancel，不改节点状态，不重提作业 | 不适用 | pending resources、node drain、slurmstepd error | `manual_escalation` |
| unknown event_type | `<none>` | 无 | 不执行任何恢复动作 | 不适用 | 未知或证据不足 | `diagnose_only` |

## 8. 证据与分类边界

R16 新候选必须避免吞并已有高风险域：

- optional integration 缺失不能吞并核心 `python_env`；
- notification sink 失败不能吞并 `auth_cert`；
- queue backpressure 不能吞并外部 MQ `dependency_service`；
- optional cache backend 失败不能吞并通用 `disk_full`；
- optional config 默认值不能吞并核心 `config_error`；
- GPU precision 优化不能替代现有 batch size 安全恢复。

每个新增 detector fixture 必须至少包含：

- 一个 positive fixture；
- 一个与高风险通用域相邻的 negative fixture；
- 一个 multi-event 场景，确认不会覆盖同窗口其他事件；
- 一个 benign/info fixture，确认普通降级日志不会误触发。

## 9. 后续阶段入口条件

R16 batch-1 任一候选进入实现前，必须先补齐以下材料：

1. 本文候选矩阵确认；
2. `event_type` 命名和 `fix_id` 命名确认；
3. detector positive/negative fixture 设计；
4. registry `SafeRecoverySpec` 草案；
5. precheck planned edits 预期；
6. local/remote apply + rollback 测试用例；
7. policy dry-run、guarded dry-run、runtime gate 测试；
8. isolated live dry-run 计划；
9. failure path 和 rollback audit 验证计划。

未完成上述入口条件前，不得把新候选加入真实 `safe_auto_recover` 执行链路。

## 10. 阶段结论

R16 第一阶段建议优先推进 4 个 batch-1 safe 候选：

- `optional_integration_failed -> fix-optional-integration-1`
- `notification_sink_failed -> fix-notification-sink-1`
- `queue_backpressure -> fix-queue-backpressure-1`
- `optional_cache_backend_failed -> fix-cache-backend-1`

这些候选都保持在“关闭可选能力、切换本地降级模式或下调参数”的安全边界内。`guarded-only` 候选只进入 dry-run 和人工确认设计；`manual-only` 范围继续禁止自动恢复。
