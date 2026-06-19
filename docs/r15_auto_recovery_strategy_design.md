# R15-1 自动恢复策略安全分层设计

## 1. 背景

R13 已完成长期运行稳定性验证，确认 daemon、单事件、多事件、report/alert 链路、persistent seen、manual escalation 与既有 `auto_recover` 边界在验证范围内保持稳定。R14 已完成结构硬化阶段，补充了 `runtime_health` 可观测性、report/alert rate limit、per fingerprint cooldown、flood control、seen compact dry-run、retention/log rotation 设计与阶段验收材料。

当前系统已经具备稳定的监控、检测、报告、告警、多事件处理、限流和 dry-run 治理能力，但这并不意味着可以直接扩大自动恢复范围。自动恢复一旦从报告建议进入真实执行，就会影响运行服务、依赖组件、调度环境或用户数据；如果缺少策略分层、前置检查、冷却限制、回滚要求和审计记录，低频误判也可能放大成持续性影响。

R15 的目标是先定义自动恢复的安全策略分层，而不是新增恢复动作。所有未来自动恢复能力都必须可控、可审计、可回滚、可限流，并且能够在证据不足、风险升高或策略不明确时自动降级为诊断、报告或人工升级。

本文件仅完成 R15-1 设计，不修改 detector、MonitorLoop、policy、AutoRecoveryRunner，不新增恢复动作，不扩大 `auto_recover` 权限。

## 2. 策略分层

自动恢复策略建议分为五层：

```text
diagnose_only
manual_escalation
safe_auto_recover
guarded_auto_recover
disabled
```

| 策略层 | 含义 | 适用场景 | 允许动作 | 禁止动作 | 是否生成 report | 是否生成 alert | 是否允许自动执行 | 是否需要人工确认 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `diagnose_only` | 只诊断、只报告，不进入恢复执行 | 证据不足、首次出现、低置信度、不确定归因、benign 边界不清晰 | 收集 evidence、生成诊断 report、记录 event/fingerprint、给出排查建议 | 执行修复命令、修改配置、重启服务、删除文件、安装依赖、变更集群资源 | 是 | 视严重级别和通知策略决定 | 否 | 否 |
| `manual_escalation` | 自动形成建议，但由人工处理 | 中高风险故障、外部依赖故障、权限/认证问题、可能影响业务连续性的故障 | 生成 report、生成 alert、列出人工处理步骤、记录需要确认的风险点 | 自动修复、自动回滚、自动提升权限、自动操作调度器或 Kubernetes | 是 | 是 | 否 | 是 |
| `safe_auto_recover` | 仅允许低风险、范围明确、可回滚的自动恢复 | 项目显式允许的低风险动作，且 evidence 充分、cooldown/rate limit 满足、rollback 可用 | 执行受控 fix_id、写入审计记录、生成恢复结果 report/alert、失败后降级人工升级 | 高风险命令、不可回滚变更、跨主机操作、影响其他服务的动作、任意脚本执行 | 是 | 是，至少在执行、失败或回滚时生成 | 是 | 默认否，但可由项目策略要求确认 |
| `guarded_auto_recover` | 未来受保护自动恢复层，必须满足多重条件 | 风险高于 safe 层但可被严格约束的场景；当前仅讨论设计，不启用 | dry-run、precheck、人工确认、双重策略允许、变更窗口校验、可验证 rollback | 默认禁止真实执行；禁止绕过人工确认；禁止无审计、无回滚、无 evidence 执行 | 是 | 是 | 当前否，未来可在 guarded 条件下讨论 | 是 |
| `disabled` | 明确禁止恢复 | 被策略禁用、命中危险动作、未知项目、受保护环境、禁止自动变更的故障域 | 记录禁止原因、生成诊断 report、必要时生成 alert | 任何恢复动作、任何状态变更、任何权限提升或破坏性操作 | 是 | 视严重级别和安全策略决定 | 否 | 如需处理必须人工确认 |

## 3. 每层策略定义

| 策略层 | 自动执行 | 风险级别 | 典型场景 | 允许动作 | 禁止动作 |
| --- | ---- | ---- | ---- | ---- | ---- |
| `diagnose_only` | 否 | 低到中，或风险未知 | 新 fingerprint、低置信度日志、归因不明确、仅需观察的异常 | 只诊断、只报告、记录 evidence、输出排查建议 | 修复、回滚、重启、删除、安装、权限提升 |
| `manual_escalation` | 否 | 中到高 | `disk_full`、`process_crash`、`container_k8s`、外部依赖、权限和认证问题 | 生成 report/alert、建议人工处理、标记 operator required | 自动修复、自动执行命令、自动修改运行环境 |
| `safe_auto_recover` | 是，仅限显式允许 | 低 | 已有受控 fix_id、对象明确、影响范围小、rollback 可用的场景 | 低风险、可回滚动作；执行前 precheck；执行后 audit/report/alert | 不可回滚动作、任意命令、跨服务影响、高风险操作 |
| `guarded_auto_recover` | 当前否，未来需 guarded 条件 | 中到高 | 需要变更窗口、人工确认、二次校验或 dry-run 先行的恢复候选 | 当前仅允许设计和 dry-run；未来可讨论人工确认后的受控执行 | 默认真实执行、无确认执行、无 rollback 执行、绕过 rate limit |
| `disabled` | 否 | 任意，通常为高或禁止 | 命中危险命令、项目禁用自动恢复、策略缺失或安全域不允许 | 记录禁用原因、报告和告警 | 任何恢复动作 |

各层定义必须满足以下原则：

- `diagnose_only`：只诊断、只报告，不执行恢复；
- `manual_escalation`：只建议人工处理，不替代人工决策；
- `safe_auto_recover`：只允许低风险、可回滚动作；
- `guarded_auto_recover`：需要多重条件满足，当前阶段不启用真实执行；
- `disabled`：明确禁止恢复。

## 4. 触发条件

策略选择不应只由 `event_type` 决定，而应由事件、证据、风险、频率和项目配置共同决定。建议触发条件如下：

| 条件 | 说明 | 对策略选择的影响 |
| --- | --- | --- |
| `event_type` | detector 输出的故障域类型 | 作为初始策略映射输入，但不能单独授权执行 |
| `risk_level` | 由故障域、动作、影响范围和环境综合得出 | 高风险默认 `manual_escalation` 或 `disabled` |
| `confidence` | detector 对当前事件归因的置信度 | 低置信度默认 `diagnose_only` |
| fingerprint 是否重复 | 当前 fingerprint 是否已见过、是否持续重复 | 重复事件需要检查 cooldown；重复但未恢复可能升级人工处理 |
| cooldown 是否满足 | per fingerprint、per event_type、per project 冷却是否到期 | 未满足时禁止再次自动恢复 |
| rate limit 是否允许 | 当前 cycle 和项目级 report/alert/auto_recover budget 是否允许 | 超限时降级为记录、报告或人工升级 |
| precheck 是否通过 | 服务状态、对象匹配、影响范围、evidence、rollback 等前置检查结果 | precheck 未通过时禁止自动恢复 |
| rollback 是否可用 | 是否有明确、可执行、可审计的回滚路径 | 无 rollback 的动作默认不得自动执行 |
| 是否存在人工确认要求 | 项目策略、环境、风险层是否要求 operator confirmation | 需要确认时不得无人值守执行 |

推荐决策顺序：

1. 先检查是否命中 `disabled` 条件或危险动作；
2. 再依据 `event_type`、`risk_level`、`confidence` 选择候选策略层；
3. 对候选 `safe_auto_recover` 执行 precheck、cooldown、rate limit 和 rollback 校验；
4. 任一关键条件失败时降级为 `diagnose_only` 或 `manual_escalation`；
5. 所有决策和降级原因都写入 audit record，并进入 report/alert。

## 5. 权限边界

默认自动恢复不得执行以下动作：

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

上述动作可能终止关键进程、删除用户数据、改变依赖环境、影响 systemd 托管服务、修改 Kubernetes 资源或扩大故障影响范围。它们只能在 future guarded 模式下讨论，并且必须满足人工确认、dry-run、precheck、变更窗口、可回滚、可审计和限流等条件。当前 R15-1 不得直接启用这些动作，也不得通过策略映射间接启用。

其他默认禁止边界：

- 不执行任意 shell 片段；
- 不执行来源不明的 fix 脚本；
- 不对非目标对象执行恢复；
- 不跨项目复用自动恢复授权；
- 不通过自动恢复修改 detector、policy 或运行时核心逻辑；
- 不为了让恢复成功而提升权限；
- 不把 report/alert rate limit 视为恢复授权。

## 6. precheck 设计

每个 `auto_recover` 前必须执行 precheck。precheck 不是日志记录步骤，而是自动恢复能否继续的硬门槛。

| precheck 项 | 设计要求 | 失败处理 |
| --- | --- | --- |
| 当前服务状态 | 确认目标服务、进程、端口、GPU 或配置对象处于预期异常状态 | 停止自动恢复，转 `diagnose_only` 或 `manual_escalation` |
| 目标对象是否匹配 | event evidence 中的对象必须与 fix 目标一致 | 禁止执行，记录 `target_mismatch` |
| 是否命中 cooldown | 检查 fingerprint、event_type、project 级 cooldown | 未到期时禁止再次恢复 |
| 是否超过 per-event limit | 检查当前 cycle 和事件级自动恢复次数 | 超限时禁止执行 |
| 是否有 rollback | 确认 rollback 方案、输入和审计字段可用 | 无 rollback 时默认禁止自动执行 |
| 是否为低风险动作 | 校验 action/fix_id 是否属于 safe allowlist | 非低风险动作转人工确认 |
| 是否会影响其他服务 | 评估端口、配置、进程、依赖和资源影响范围 | 可能影响其他服务时转 `manual_escalation` |
| 是否有足够 evidence | 当前事件必须有足够日志、状态或检测证据支撑 | 证据不足时只诊断、只报告 |

precheck 输出建议为结构化结果，至少包含：

- `passed`；
- `failed_checks`；
- `target_object`；
- `evidence_summary`；
- `risk_level`；
- `cooldown_result`；
- `rate_limit_result`；
- `rollback_available`；
- `operator_required`；
- `downgrade_reason`。

## 7. cooldown 与 rate limit

自动恢复必须复用并扩展 R14 的限流思想。report/alert rate limit 解决的是产物风暴和通知风暴，`auto_recover` cooldown/rate limit 解决的是恢复动作本身的重复执行风险。

建议设计以下冷却与限流：

| 类型 | 作用 | 建议行为 |
| --- | --- | --- |
| per fingerprint cooldown | 防止同一 fingerprint 在短时间内重复恢复 | 同一 fingerprint 恢复后进入冷却；冷却未结束不得再次执行 |
| per event_type cooldown | 防止同类故障反复触发恢复 | 同一 `event_type` 高频出现时降级为人工升级 |
| per project cooldown | 防止单项目持续自动变更 | 项目级恢复过密时暂停自动恢复，只保留 report/alert |
| per cycle auto_recover 上限 | 控制单轮恢复动作数量 | 保持每轮最多执行有限个恢复动作；当前既有边界应继续保守 |
| report/alert rate limit 关系 | 与 R14 产物限流互补 | 恢复被限流时仍应保留必要审计；alert 被限流不得掩盖恢复失败 |

关键原则：

- cooldown 未满足时不得自动恢复；
- rate limit 超限时不得为了完成恢复而绕过审计；
- 自动恢复失败后应进入更长 cooldown 或人工升级；
- 多事件窗口中不得因为多个事件同时出现而放宽单轮恢复上限；
- report/alert rate limit 只能限制产物数量，不能改变恢复安全判断。

## 8. rollback 要求

没有 rollback 的动作默认不得自动执行。rollback 是 `safe_auto_recover` 的必要条件，也是 future `guarded_auto_recover` 的核心准入条件。

必须有 rollback 的动作包括：

- 修改配置文件、环境变量、端口或资源参数；
- 修改项目内可运行状态；
- 调整 GPU、端口、依赖或运行参数；
- 任何可能影响下一次启动、下一轮检测或其他服务的恢复；
- future guarded 模式中任何需要人工确认的受控变更。

rollback 设计要求：

- rollback 必须在执行前可知，而不是失败后临时推断；
- rollback 输入必须和 action 输入绑定；
- rollback 结果必须进入 report/alert；
- rollback 失败必须进入 `manual_escalation`；
- rollback 失败后不得重复执行同一恢复动作；
- rollback 失败应触发更严格 cooldown，并记录阻断原因；
- rollback 本身不得执行危险动作或扩大权限。

如果 action 成功但验证失败，应优先执行 rollback；如果 rollback 失败，必须明确记录 `rollback_failed`，生成告警，并把事件标记为需要人工处理。

## 9. 审计格式

每次自动恢复候选，无论最终执行、降级、跳过或禁止，都应生成 auto_recover audit record。建议字段如下：

| 字段 | 说明 |
| --- | --- |
| `event_type` | 当前事件类型 |
| `fingerprint` | 当前事件 fingerprint |
| `strategy_layer` | 最终采用的策略层 |
| `action` | 候选或实际执行的动作；未执行时记录 `none` 或 `skipped` |
| `precheck_result` | precheck 结构化结果，包括通过项、失败项和降级原因 |
| `risk_level` | 当前事件和动作综合风险 |
| `cooldown_result` | fingerprint、event_type、project 级 cooldown 检查结果 |
| `rollback_available` | 是否存在 rollback |
| `execution_result` | 执行结果；未执行时记录跳过原因 |
| `rollback_result` | rollback 结果；未触发时记录 `not_required` 或 `not_run` |
| `operator_required` | 是否需要人工确认或人工接管 |
| `created_at` | 审计记录创建时间 |

示例结构：

```json
{
  "event_type": "network_port",
  "fingerprint": "example-fingerprint",
  "strategy_layer": "safe_auto_recover",
  "action": "fix-network-1",
  "precheck_result": {
    "passed": true,
    "failed_checks": [],
    "evidence_summary": "目标端口冲突证据充分"
  },
  "risk_level": "low",
  "cooldown_result": {
    "fingerprint": "allowed",
    "event_type": "allowed",
    "project": "allowed"
  },
  "rollback_available": true,
  "execution_result": "not_run_in_r15_1_design",
  "rollback_result": "not_required",
  "operator_required": false,
  "created_at": "2026-06-19T00:00:00Z"
}
```

R15-1 只定义格式，不写入真实 `state/` 或 `outputs/`，不改变现有 report/alert 生成逻辑。

## 10. 当前故障域建议映射

以下映射只做设计，不改代码。原则是保守默认：高风险默认 `manual_escalation`，不确定默认 `diagnose_only`，明确禁止的为 `disabled`，只有低风险动作才考虑 `safe_auto_recover`。

| event_type | 建议策略层 | 原因 |
| ---------- | ----- | -- |
| `network_port` | `safe_auto_recover` | 仅在项目显式允许既有 `fix-network-1`、目标端口明确、rollback 可用、cooldown 满足时考虑；否则降级为 `diagnose_only` 或 `manual_escalation`。 |
| `gpu_oom` | `safe_auto_recover` | 仅在项目显式允许既有 `fix-gpu-1`、影响范围局部、不会影响其他任务且 rollback 可用时考虑；主机 OOM 或 Kubernetes `OOMKilled` 不归入本层。 |
| `python_env` | `manual_escalation` | 依赖环境变更风险较高，默认不执行任意 `pip install`；即使存在候选 fix，也必须显式允许并先完成 guarded/precheck 设计。 |
| `disk_full` | `manual_escalation` | 清理磁盘可能删除用户数据，禁止自动 `rm` 或破坏性清理。 |
| `slurm` | `manual_escalation` | 调度器状态和作业生命周期影响面大，不自动执行 `scancel` 或修改节点状态。 |
| `process_kill` | `manual_escalation` | 不自动执行 `kill`、restart 或进程替换；需要人工判断被 kill 的原因。 |
| `permission_denied` | `manual_escalation` | 权限边界、文件 ACL、OS 限制或安全策略需要人工确认，不自动提升权限。 |
| `process_crash` | `manual_escalation` | core dump、段错误和服务失败需要人工排查，不自动 `systemctl restart`。 |
| `host_resource` | `manual_escalation` | 主机内存、文件句柄和负载问题可能影响多个服务，不适合无人值守恢复。 |
| `network_connectivity` | `manual_escalation` | 外部网络、DNS、TLS 和连接超时原因复杂，默认人工升级。 |
| `dependency_service` | `manual_escalation` | DB、Redis、Kafka、MQ 等依赖故障不应由本 Agent 自动变更外部服务。 |
| `config_error` | `manual_escalation` | 配置修改可能影响启动和业务语义，默认只建议人工处理。 |
| `auth_cert` | `manual_escalation` | token、证书和鉴权变更涉及安全凭据，不自动替换或刷新。 |
| `container_k8s` | `manual_escalation` | Kubernetes 资源变更风险高，不自动执行 `kubectl delete`、`kubectl apply` 或重启类动作。 |
| `benign/info` | `diagnose_only` | 正常日志不应生成事件；如进入观察链路，也只允许诊断和记录，不执行恢复。 |
| 危险动作命中 | `disabled` | 命中 `kill -9`、`rm -rf`、`systemctl stop`、`kubectl delete`、权限提升或跨主机破坏性操作时明确禁止恢复。 |
| 未知 `event_type` | `diagnose_only` | 未知故障域缺少策略和 rollback，默认只诊断、只报告。 |

## 11. R15 后续阶段建议

R15 后续阶段建议按以下路线推进：

| 阶段 | 目标 | 说明 |
| --- | --- | --- |
| R15-2 | policy schema 设计 | 定义策略层、风险级别、动作 allowlist、人工确认、rollback、cooldown 和审计字段，不改变现有执行逻辑。 |
| R15-3 | precheck / cooldown 机制设计 | 细化 precheck 输入输出、失败降级、per fingerprint/event_type/project cooldown 和 per cycle 上限。 |
| R15-4 | guarded auto_recover dry-run | 只做 guarded 候选动作 dry-run 和审计，不执行真实恢复，不扩大动作面。 |
| R15-5 | 审计与 report/alert 集成 | 设计 audit record 如何进入 report/alert，并确认 rate limit 下仍保留关键失败和回滚信息。 |
| R15-6 | R15 验收总结 | 汇总策略分层、安全边界、测试结果、剩余风险和是否进入后续实现阶段。 |

R15-5 已进一步明确 guarded auto_recover 的 dry-run 边界：guarded candidate 可以表达 policy、precheck、cooldown、rollback 和 audit 结果，但本阶段 `would_execute=false`、`dry_run=true`，真实执行结果固定为 `not_run_guarded_dry_run`。这意味着 `guarded_auto_recover` 仍不是新增真实恢复动作，也不会扩大当前 `auto_recover` 权限。

R15-6 补充 audit 与 report/alert 集成设计，并形成 R15 阶段总结。该收尾阶段仍只做设计与验收总结，不接入真实执行，不新增恢复动作，不扩大 `auto_recover` 权限。

## 12. 结论

R15-1 仅完成自动恢复策略安全分层设计，不新增恢复动作，不扩大自动恢复权限。
