# Agentic Linux Monitoring & Auto-Recovery Agent

面向 Linux/企业服务日志的监控、事件检测、自动恢复、通知与排障报告系统。

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
自动恢复策略判断
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
- 自动恢复策略判断，由 `RemediationPolicy` 决定 `auto_recover`、`manual_escalation` 或 `report_only`。
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
- 自动恢复安全边界：危险操作默认不自动执行。
- 核心测试基线：`scripts/run_core_tests.sh`。

## 3. 支持的故障域

| event_type | 典型现象 | 默认处置方向 |
|---|---|---|
| `network_port` | `Address already in use`、端口冲突、服务绑定失败 | 可在 policy 允许时自动恢复 |
| `gpu_oom` | `CUDA out of memory`、`HIP out of memory`、显存不足 | 可在 policy 允许时自动恢复 |
| `disk_full` | `No space left on device`、inode 或缓存目录耗尽 | 高风险，默认人工升级 |
| `python_env` | `ModuleNotFoundError`、解释器与 pip 环境不一致 | 通常人工确认或报告 |
| `slurm` | 作业 pending、资源不足、`oom-kill`、节点异常 | 通常人工确认或报告 |
| `process_kill` | 进程被 kill、被 OOM killer 终止 | 高风险，默认人工升级 |
| `permission_denied` | 权限不足、路径不可写、认证失败 | 高风险，默认人工升级 |
| `process_crash` | `core-dump`、`SIGSEGV`、非零退出 | 高风险，默认人工升级 |
| `host_resource` | 主机内存、CPU、磁盘、inode 等资源异常 | 通常人工确认或报告 |
| `network_connectivity` | DNS、连接超时、服务不可达 | 通常人工确认或报告 |
| `dependency_service` | Redis、DB、对象存储、外部依赖不可用 | 通常人工确认或报告 |
| `config_error` | 配置缺失、字段错误、格式错误 | 通常人工确认或报告 |
| `auth_cert` | token、证书、权限、密钥相关异常 | 高风险，默认人工升级 |
| `container_k8s` | `BackOff`、`ImagePullBackOff`、`OOMKilled`、Pod 异常 | 高风险，默认人工升级 |

只有安全允许并显式配置的故障域会进入自动恢复。高风险故障默认走 `manual_escalation`，由负责人确认后处理。

## 4. 自动恢复安全边界

自动恢复由 policy 严格控制，不会默认执行危险操作。

安全边界包括：

- `kill`、`rm`、`pip install`、`systemctl`、`kubectl` 等操作必须受 policy 限制，并需要人工确认。
- 每轮最多处理 3 个 event。
- 每轮最多执行 1 个 `auto_recover`。
- 高风险域默认走 `manual_escalation`。
- 自动修复失败时会 rollback 或通知人工处理。
- 报告中不得把 `manual_escalation` 描述成已自动修复。
- Agent 不应声称执行了上下文中没有发生的操作。

当前自动恢复重点覆盖低风险、可回滚、可验证的配置类修复，例如端口调整、训练 batch size 调整等。缓存清理、进程终止、服务重启、集群变更和依赖安装默认不自动执行。

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
  --report-mode llm
```

daemon 模式：

```bash
python main_monitor.py \
  --config configs/projects.yaml \
  --project enterprise_demo_local \
  --daemon \
  --agent-depth balanced \
  --report-mode llm
```

systemd 状态查看：

```bash
systemctl status agentic-monitor@enterprise_demo_local.service --no-pager -l
journalctl -u agentic-monitor@enterprise_demo_local.service -f
```

如果没有配置 LLM API Key，`--report-mode auto` 可以回退到规则报告模式。不要把 API Key 写入代码或提交到 Git。

## 7. 测试

核心测试基线：

```bash
scripts/run_core_tests.sh
```

关键专项测试：

```bash
.venv/bin/python -m pytest tests/test_fault_domain_regression.py -q
.venv/bin/python -m pytest tests/test_multi_event_detector.py -q
.venv/bin/python -m pytest tests/test_stage6e_monitor_loop_multi_event.py -q
.venv/bin/python -m pytest tests/test_remote_log_tail_truncation.py -q
```

这些测试覆盖核心模块导入、故障域回归、multi-event detector、MonitorLoop 多事件处理和 remote tail 截断修复。

## 8. 当前验收状态

- Stage 6E-2 systemd 生命周期验收通过。
- R9 live report smoke 部分完成，已验证 `process_crash` 和 `container_k8s` 的独立报告能力。
- R10 multi-event live smoke 通过，同一日志窗口中 `process_crash` 与 `container_k8s` 均可生成独立 report/alert。
- R10 remote log tail 截断问题已修复，日志 tail 场景保留尾部故障行。
- R11 报告模板质量审计与小幅优化完成。
- R12 本地 Git baseline 已建立，包含稳定 commit 和 `stage6e-r11-stable` tag；远程推送取决于 GitHub 凭据配置。

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
