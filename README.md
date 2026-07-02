# Agentic Linux Monitoring & Auto-Recovery Agent

面向 Linux/企业服务日志的 Agentic 运维系统，集成事件审计、自动恢复、通知归档与排障分析报告。

## 1. 项目简介

本项目已经从早期的多 Agent 排障助手，演进为一个完整的 `Monitoring & Auto-Recovery Agent`。它面向 Linux 服务、远程项目日志、企业运维场景和训练/推理任务故障，提供从长期监控到安全恢复、通知和审计报告的闭环能力。

核心链路如下：

```text
日志监控
  ↓
事件检测
  ↓
故障域识别
  ↓
registry domain policy + project policy overlay
  ↓
runtime gate 最终裁决
  ↓
安全执行或人工升级
  ↓
通知负责人
  ↓
生成排障报告与审计记录
  ↓
systemd 长期守护
```

项目重点不是让 Agent 任意执行命令，而是让它在可控、可审计、可测试的边界内识别故障、生成证据链、执行有限的安全修复，或把高风险事件升级给人工处理。

## 2. 当前核心能力

- Linux 项目日志监控。
- 本地日志 watcher 与远程日志 watcher。
- 故障事件检测与 `ErrorEvent` 结构化输出。
- 多故障域识别与回归测试。
- `LLMReportAgent` / `ReportAgent` 双模式报告。
- 自动恢复裁决：由 registry domain policy、project policy overlay 和 runtime gate 收敛决定最终动作；`CompatibilityRemediationPolicy` 仅保留兼容审计输出。
- `manual_escalation` 人工升级路径。
- 通知系统，支持 console、file、webhook 等渠道。
- alerts 归档，包括 `outputs/alerts/...jsonl` 和 Markdown 通知归档。
- `project_status.json` 状态文件。
- `daemon.log` 守护进程日志。
- systemd 服务部署资产：`systemd/agentic-monitor@.service`。
- persistent `seen_fingerprints` 去重，避免重复报告同一事件。
- `multi-event-per-window`：同一日志窗口可识别并处理多个 event。
- 多事件独立 report/alert：每个 event 拥有独立 evidence、fingerprint、报告和通知。
- remote log tail 保留尾部修复，避免长 tail 输出截断掉最新故障行。
- runtime gate：自动恢复执行前强制经过 dry-run、precheck、cooldown、rollback 和 audit 检查。
- registry domain policy：统一管理 safe/manual/diagnose 域，其中 safe projection 当前包含 11 个低风险 `safe_auto_recover` 故障域。
- 语义化安全预检查：仅允许项目内 JSON 配置字段的安全降级，例如关闭可选能力、切换本地/内存模式或下调参数。
- 自动恢复安全边界：危险操作默认不自动执行，生产配置默认仍建议保持 `auto_recovery_dry_run: true`。
- 核心测试基线：`scripts/run_core_tests.sh`。

## 架构概览

| 目录 | 作用 |
|---|---|
| `monitors/` | 监控循环、日志 watcher、状态管理、daemon 日志 |
| `detectors/` | 故障事件识别与 `ErrorEvent` 生成 |
| `policies/` | project policy overlay、runtime gate 策略解析和兼容策略适配器 |
| `recovery/` | 安全恢复执行器与恢复结果记录 |
| `notifiers/` | 通知分发与 alerts 归档 |
| `agents/` | 排障分析与报告生成 |
| `sessions/` | 排障会话编排、证据汇总和报告入口 |
| `configs/` | 项目配置与监控目标配置 |
| `tests/` | 核心测试、故障域回归和专项测试 |
| `docs/` | 验收、设计、测试计划和模板文档 |

## 3. 支持的故障域

### 3.1 safe_auto_recover 候选域

以下故障域已进入 registry domain policy。它们只是自动恢复候选；真实执行还必须同时满足 project policy overlay（例如 `policy.allow_auto_apply`、`auto_recovery_dry_run=false`）、runtime gate、语义 precheck、cooldown、rollback 和 audit 要求。

| event_type | 典型现象 | fix_id | 安全动作边界 |
|---|---|---|---|
| `network_port` | `Address already in use`、端口冲突、服务绑定失败 | `fix-network-1` | 只修改受控 JSON 端口字段，并检查目标端口可用 |
| `gpu_oom` | `CUDA out of memory`、`HIP out of memory`、显存不足 | `fix-gpu-1` | 只下调明确的 batch size 类 JSON 字段 |
| `cache_write_failed` | 缓存写入失败、cache `Errno 28`、fallback 到 memory cache | `fix-cache-1` | 只关闭可选缓存写入或缓存故障模拟开关 |
| `optional_dependency_missing` | 可选依赖、插件或 internal SDK 缺失，并存在 fallback | `fix-optional-dep-1` | 只关闭可选依赖或可选集成开关，不执行 `pip install` |
| `optional_integration_failed` | 可选 webhook、risk SDK、enrichment client 失败，并存在本地降级 | `fix-optional-integration-1` | 只关闭失败的可选外部集成，不改 token 或外部服务 |
| `optional_cache_backend_failed` | 可选 Redis/cache backend 失败，并可切换 memory/local cache | `fix-cache-backend-1` | 只切换到 memory/local 或关闭可选缓存后端，不清理缓存、不 flush Redis |
| `optional_service_unavailable` | 可选 enrichment、recommendation、risk scoring 服务不可用 | `fix-optional-service-1` | 只关闭可选服务开关，不修改核心 DB/MQ/Redis/Kafka |
| `notification_sink_failed` | 可选 webhook/notification sink 超时、HTTP 5xx，并可保留 file/console | `fix-notification-sink-1` | 只关闭可选远程通知 sink，不修改密钥、证书或 token |
| `observability_export_failed` | metrics/tracing/OTel exporter 失败，并可 fallback file/console/local | `fix-observability-export-1` | 只关闭远程 exporter 或切到 local/file/console |
| `queue_backpressure` | queue backpressure、prefetch 过高、max inflight exhausted | `fix-queue-backpressure-1` | 只下调配置化 queue consumer 参数，不 purge 队列、不 ack/nack |
| `worker_overload` | worker pool exhausted、concurrency too high、worker overload | `fix-worker-1` | 只下调配置化 worker 并发，不 kill/restart 进程 |

当前示例项目 `enterprise_demo_local` 已把这些 `fix_id` 放入 `policy.allow_auto_apply`，但配置仍保持 `auto_recovery_dry_run: true`。因此默认行为是生成审计和报告，不做真实 apply。

### 3.2 manual_escalation / report_only 域

以下故障域默认不允许自动恢复。Agent 会生成报告、通知负责人或进入人工升级路径。

| event_type | 典型现象 | 默认处置方向 | 禁止的自动动作 |
|---|---|---|---|
| `disk_full` | `No space left on device`、inode 或缓存目录耗尽 | `manual_escalation` | 不自动 `rm`、不清目录、不压缩或删除文件 |
| `python_env` | `ModuleNotFoundError`、解释器与 pip 环境不一致 | `manual_escalation` | 不执行任意 `pip install`，即使存在 `fix-python-1` 候选也默认受 R15 gate 阻断 |
| `slurm` | 作业 pending、资源不足、`oom-kill`、节点异常 | `manual_escalation` | 不 `scancel`、不改调度器状态、不重提作业 |
| `process_kill` | 进程被 kill、exit 137、SIGKILL | `manual_escalation` | 不 kill/restart 进程 |
| `permission_denied` | 权限不足、路径不可写、EACCES | `manual_escalation` | 不 `chmod`、不 `chown`、不提权 |
| `process_crash` | `core-dump`、`SIGSEGV`、非零退出 | `manual_escalation` | 不自动 `systemctl restart` 或替换进程 |
| `host_resource` | 主机内存、CPU、文件句柄、系统负载异常 | `manual_escalation` | 不修改系统资源限制、不杀进程 |
| `network_connectivity` | DNS、连接超时、连接拒绝、TLS handshake timeout | `manual_escalation` | 不改网络、DNS、iptables 或证书 |
| `dependency_service` | DB、Redis、Kafka、RabbitMQ、MQ、连接池异常 | `manual_escalation` | 不重启外部依赖、不改核心依赖地址 |
| `config_error` | 配置缺失、字段错误、格式错误、非法 path/port | `manual_escalation` | 不猜测核心业务默认值 |
| `auth_cert` | token、证书、HTTP 401/403、TLS 校验失败 | `manual_escalation` | 不修改 token、secret、certificate、ACL |
| `container_k8s` | `CrashLoopBackOff`、`ImagePullBackOff`、`OOMKilled`、Pod 调度失败 | `manual_escalation` | 不执行 `kubectl delete/apply/rollout restart` |
| `traceback` / `unknown` | 泛化异常或证据不足 | `report_only` 或 `manual_escalation` | 不执行恢复动作 |

只有 registry domain policy 标为 safe、project policy overlay 显式允许、语义 precheck 通过且 runtime gate 放行的故障域才可能进入真实自动恢复。高风险故障默认走 `manual_escalation`，由负责人确认后处理。

## 4. 自动恢复安全边界

自动恢复由 registry domain policy、project policy overlay 和 runtime gate 共同控制，不会默认执行危险操作。

安全边界包括：

- `kill`、`rm`、`pip install`、`systemctl`、`kubectl`、`scancel`、`chmod`、`chown`、提权等操作默认禁止自动执行。
- 每轮最多处理 3 个 event。
- 每轮最多执行 1 个 `auto_recover`。
- 高风险域默认走 `manual_escalation`。
- 自动修复失败时会 rollback 或通知人工处理。
- 报告中不得把 `manual_escalation` 描述成已自动修复。
- Agent 不应声称执行了上下文中没有发生的操作。

runtime gate 执行前必须确认：

- 项目启用 `auto_recover` 且 `fix_id` 出现在 `policy.allow_auto_apply`。
- `auto_recovery_dry_run=false`；默认示例配置仍保持 `true`。
- event_type 属于 `safe_recovery.registry` 中登记的 safe 候选。
- precheck 能读到目标配置，并找到可执行的 planned edit。
- planned edit 满足语义规则：`disable_bool`、`lower_int`、`port_available` 或 `safe_enum_downgrade`。
- rollback plan 可用，并能记录 backup / diff / applied record。
- cooldown、rate limit、forbidden action 和 operator confirmation 检查通过。
- audit record 已写入报告、alert 或 cycle summary。

当前自动恢复重点覆盖低风险、可回滚、可验证的项目内 JSON 配置降级。缓存清理、进程终止、服务重启、依赖安装、集群变更、认证密钥修改和外部依赖重启默认不自动执行。

## 5. 报告能力

当前报告不是简单日志总结，而是带有证据链和安全边界的排障与审计产物。

报告类型包括：

- 事件排障报告。
- 通知后状态报告。
- 多事件 `cycle_summary`。
- 事件审计记录。
- evidence 证据链追踪。
- policy action 与恢复状态说明。
- 安全边界说明。
- 验证方式。
- 后续观察建议。

R11 已对报告模板做小幅优化：事件报告固定包含“安全边界”和“验证方式”，`post_notification` 报告更短，多事件报告要求每个 event 保持独立 evidence、处置策略和状态说明。

## 输出产物

运行过程中会生成以下产物，用于事件审计、故障复盘和安全追踪：

- `outputs/monitors/`：事件排障报告、通知后报告、`cycle_summary`。
- `outputs/alerts/`：告警 Markdown、JSONL 和 latest alert。
- `state/<project_id>/project_status.json`：项目运行状态。
- `state/<project_id>/daemon.log`：daemon 运行日志。
- `seen_fingerprints`：持久化去重状态，用于避免重复处理同一事件。

## 入口说明

| 入口 | 定位 | 使用边界 |
|---|---|---|
| `main_monitor.py` | 正式监控入口 | 单轮监控、daemon、systemd 和自动恢复链路都应从这里进入 |
| `main_interactive.py` | 历史交互入口 | 仅用于手工排障、交互调试和旧流程验证 |
| `main_multi_agent.py` / `main_multi_agent_v3.py` | 历史 / 实验多 Agent 入口 | 仅用于多 Agent 报告实验或回归对照 |
| `main.py` | 更早期单 Agent 排障入口 | 仅用于单次 ad-hoc 排障实验 |

新功能、daemon 长期运行和自动恢复链路应接入 `main_monitor.py`。legacy CLI 保留兼容性，但不作为生产监控或 systemd 入口。

## 6. 运行方式

建议先创建虚拟环境并安装依赖：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

单轮监控：

```bash
source .venv/bin/activate

python main_monitor.py \
  --config configs/projects.yaml \
  --project enterprise_demo_local \
  --cycles 1 \
  --agent-depth balanced \
  --report-mode rule
```

daemon 模式：

```bash
python main_monitor.py \
  --config configs/projects.yaml \
  --project enterprise_demo_local \
  --daemon \
  --agent-depth balanced \
  --report-mode rule
```

systemd 状态查看：

```bash
systemctl status agentic-monitor@enterprise_demo_local.service --no-pager -l
journalctl -u agentic-monitor@enterprise_demo_local.service -f
```

如已配置 LLM API Key，可使用 `--report-mode llm` 或 `--report-mode auto`。不要把 API Key 写入代码或提交到 Git。

### Trace UI 控制台

Trace UI 用于查看项目运行状态、事件证据、审批请求、job 日志、恢复历史和报告预览。默认监听 `127.0.0.1:8765`，并启动内嵌 worker 处理 UI 入队的 job。

本地带鉴权启动：

```bash
export AGENTIC_TRACE_UI_TOKEN="<set-a-local-token>"

.venv/bin/python -m web_ui.server \
  --host 127.0.0.1 \
  --port 8765 \
  --config configs/projects.yaml \
  --state-dir state \
  --output-root outputs/monitors
```

浏览器访问 `http://127.0.0.1:8765`，登录时输入启动前设置的 `AGENTIC_TRACE_UI_TOKEN`。UI 会创建登录会话并使用 CSRF token 保护非 GET 请求；`--disable-auth` 只适合本机临时开发。

如果刚运行过 `scripts/run_browser_tests.sh` 后发现 `http://127.0.0.1:8765` 打不开，通常是因为浏览器测试只会启动临时随机端口的测试 server，测试结束后会自动关闭。日常查看 Trace UI 仍需使用上面的命令或 systemd service 单独启动；可用 `ss -ltnp | grep ':8765'` 确认端口是否正在监听。

Trace UI 也支持可选角色 token，未配置角色 token 时 `AGENTIC_TRACE_UI_TOKEN` 仍按 admin token 兼容：

```bash
export AGENTIC_TRACE_UI_VIEWER_TOKEN="<viewer-token>"
export AGENTIC_TRACE_UI_OPERATOR_TOKEN="<operator-token>"
export AGENTIC_TRACE_UI_APPROVER_TOKEN="<approver-token>"
export AGENTIC_TRACE_UI_ADMIN_TOKEN="<admin-token>"
```

角色边界：

- `viewer`：只读查看。
- `operator`：连接、健康检查、启动/停止 monitor、刷新日志、生成报告、dry-run 和普通 job 操作。
- `approver`：审批、live apply、rollback 和高风险 job 重试。
- `admin`：全部权限。

Worker 模式：

- 默认模式：不传 `--disable-worker`，UI server 会启动内嵌 worker，处理 `generate_report`、`dry_run_recovery`、`approved_recovery_job`、`live_apply`、`rollback_latest` 等任务。
- 观察模式：传 `--disable-worker`，只提供状态、事件、报告、历史和已有 job 查看能力；新入队任务不会由该进程消费。
- 轮询间隔：可用 `--worker-poll-interval-seconds 1.5` 调整 worker 拉取 job 的频率。

端口与部署建议：

- 单机或 SSH 隧道优先绑定 `127.0.0.1`，通过 `ssh -L 8765:127.0.0.1:8765 <host>` 访问。
- 需要团队访问时，建议放在 HTTPS 反向代理后面，并保留 `AGENTIC_TRACE_UI_TOKEN` 鉴权；不要把 `--disable-auth` 暴露到共享网络。
- 生产观察期建议继续保持项目配置里的 `auto_recovery_dry_run: true`，先验证事件、报告、审批、恢复历史和 rollback 元数据。
- `state/` 和 `outputs/monitors/` 是运行数据目录，应挂载到持久化磁盘并纳入日志/备份策略，但不要提交到 Git。

Trace UI systemd 部署：

```bash
export AGENTIC_TRACE_UI_TOKEN="<admin-token>"
scripts/install_trace_ui_service.sh lf lf
systemctl status agentic-trace-ui.service --no-pager -l
```

部署前检查：

```bash
.venv/bin/python scripts/preflight_deploy.py \
  --project-root "$PWD" \
  --python-bin "$PWD/.venv/bin/python" \
  --config "$PWD/configs/projects.yaml" \
  --state-dir "$PWD/state" \
  --output-root "$PWD/outputs/monitors" \
  --host 127.0.0.1 \
  --port 8765
```

只读与高风险边界：

- 状态、事件、报告、恢复历史和 job 日志读取走 GET 接口，适合日常观察。
- 连接项目、启动/停止监控、刷新日志、生成报告和 dry-run 恢复会写入 job、trace 或状态文件，但不应绕过 policy / runtime gate。
- live apply、审批通过、重试高风险 job 和 rollback 会触发明确确认弹窗，并要求输入确认词；后端仍会校验 confirmation、operator、request_id、fix_id、fingerprint、rollback 可用性和 runtime gate。
- 高风险故障域仍默认进入 `manual_escalation`；UI 只是受控操作面，不扩大自动恢复允许范围。

## 7. 测试

核心测试基线：

```bash
scripts/run_core_tests.sh
```

浏览器回归不属于 core baseline，需安装 Playwright 浏览器后单独运行：

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m playwright install chromium
scripts/run_browser_tests.sh
```

如需更新浏览器截图 baseline：

```bash
AGENTIC_UPDATE_BROWSER_BASELINES=1 scripts/run_browser_tests.sh
```

真实日志 shadow gate：

```bash
.venv/bin/python scripts/r17_real_log_shadow_evaluate.py \
  --manifest /path/to/sanitized/manifest.json \
  --output-dir outputs/r18_real_log_shadow/<run_id>

.venv/bin/python scripts/r18_real_log_shadow_gate.py \
  --summary outputs/r18_real_log_shadow/<run_id>
```

关键专项测试：

```bash
.venv/bin/python -m pytest tests/test_fault_domain_regression.py -q
.venv/bin/python -m pytest tests/test_multi_event_detector.py -q
.venv/bin/python -m pytest tests/test_stage6e_monitor_loop_multi_event.py -q
.venv/bin/python -m pytest tests/test_remote_log_tail_truncation.py -q
.venv/bin/python -m pytest tests/test_auto_recovery_runtime_gate.py -q
.venv/bin/python -m pytest tests/test_auto_recovery_runner_r15_gate.py -q
.venv/bin/python -m pytest tests/test_safe_recovery_registry.py -q
.venv/bin/python -m pytest tests/test_safe_recovery_registry_governance.py -q
.venv/bin/python -m pytest tests/test_safe_recovery_semantic_precheck.py -q
.venv/bin/python -m pytest tests/test_safe_auto_recover_domain_expansion.py -q
```

这些测试覆盖核心模块导入、故障域回归、multi-event detector、MonitorLoop 多事件处理、remote tail 截断修复、runtime gate、registry domain policy、语义 precheck 和 safe 域扩展。

## 8. 当前验收状态

- Stage 6E-2 systemd 生命周期验收通过。
- R9 live report smoke 部分完成，已验证 `process_crash` 和 `container_k8s` 的独立报告能力。
- R10 multi-event live smoke 通过，同一日志窗口中 `process_crash` 与 `container_k8s` 均可生成独立 report/alert。
- R10 remote log tail 截断问题已修复，日志 tail 场景保留尾部故障行。
- R11 报告模板质量审计与小幅优化完成。
- R12 GitHub 版本基线已建立，`main` 分支和 `stage6e-r11-stable` tag 已推送到远程仓库。
- R15 自动恢复策略分层完成：policy schema、validator / resolver、dry-run、guarded dry-run、runtime gate、precheck、cooldown、rollback audit 和 failure-path validation 均已接入。
- registry domain policy 收敛完成：safe/manual/diagnose 域统一由 `safe_recovery.registry` 管理，local / remote executor、runtime gate、precheck 和 governance 测试从 registry 派生。
- R16 safe_auto_recover 域已扩展到 11 个低风险配置降级域，包括 optional integration、cache backend、notification sink、observability exporter、queue backpressure 和 worker overload 等。
- R16-S2 3-day long-cycle shadow validation 已完成 288/288 个周期，结论 `PASS`；期间 `remote_apply_fix_called=False`、`rerun_remote_project_called=False`、`exception_count=0`。
- R16-S2 是 fixture / generated config shadow validation，用于验证策略、gate、precheck、forbidden、no-op 和 rollback metadata 稳定性；它不等同于真实生产日志代表性验证。
- R18 下一阶段执行手册已加入 `docs/r18_next_stage_runbook.md`，覆盖真实日志 shadow、72 小时 dry-run 观察、Trace UI systemd 部署、RBAC/审计和 Playwright 回归。

## 9. 项目定位

这是一个研究型 / 工程验证型 Agent 项目，重点是：

- 可控：通过 policy 限制自动恢复边界。
- 可审计：保留报告、alerts、状态文件和 daemon 日志。
- 可测试：核心测试和故障域回归测试可重复运行。
- 安全恢复：只对低风险、可验证修复执行自动恢复。
- 多事件处理：同一窗口多个故障可独立识别、处理和报告。
- 企业级 Linux 故障覆盖：包含服务进程、远程日志、GPU、磁盘、Python 环境、Slurm、K8s、认证、依赖服务等场景。

## 10. 注意事项

- 不要提交 `.env`。
- 不要提交 `outputs/`。
- 不要提交 `state/`。
- 不要提交 `acceptance_artifacts/`。
- 不要提交 `.venv/`。
- API Key、token、密码、私钥不应写入代码或文档正文。
- 自动恢复策略需要谨慎配置，尤其是涉及服务重启、进程终止、依赖安装、文件删除和集群操作的场景。
- 高风险事件应优先走 `manual_escalation`，由负责人确认后处理。
