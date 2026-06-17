# Agentic Linux Troubleshooting Assistant

基于 `smolagents` 思路构建的多阶段 Linux 排障助手项目。项目从 **单 Agent + 多 Tool** 逐步演进到 **内容感知路由、多问题诊断、多 Agent 协作、动态执行计划和 LLM 报告生成**，用于分析 Linux 项目运行、GPU/DCU 训练、Slurm 调度、Python 环境、磁盘空间和网络端口等常见故障。

本项目的目标不是简单总结日志，而是验证一个较完整的 Agentic 排障流程：

```text
用户问题 / 日志文件
        ↓
内容感知路由
        ↓
复杂日志诊断
        ↓
多领域 Agent 协作分析
        ↓
动态执行计划
        ↓
LLMReportAgent 生成专家式排障报告
```

---

## 1. 项目背景

在 Linux / WSL / 服务器 / Slurm 集群环境中，项目运行错误往往不是单一问题。例如一次训练失败可能同时包含：

- GPU/DCU 显存不足；
- Slurm 作业 Pending 或 oom-kill；
- `/tmp` 或缓存目录空间不足；
- Python 解释器与 pip 环境不一致；
- 端口被占用导致 TensorBoard 或监控服务启动失败。

传统的日志分析脚本通常只能做关键词匹配，而通用大模型直接分析日志又容易存在工具调用不可控、证据来源不清晰和输出不稳定的问题。因此，本项目采用分阶段方式实现一个可控、可扩展、可回归测试的 Linux 排障 Agent。

---

## 2. 项目能力概览

当前项目支持以下问题类型：

| 问题类型 | 典型现象 | 相关工具 / Agent |
|---|---|---|
| GPU/DCU 显存问题 | `CUDA out of memory`、`HIP out of memory`、`oom-kill` | `GPUAgent`、`check_gpu_status`、`diagnose_mixed_log_file` |
| 磁盘问题 | `No space left on device`、`Errno 28`、inode 耗尽 | `DiskAgent`、`check_disk_usage` |
| 网络端口问题 | `Address already in use`、端口 9100 被占用 | `NetworkAgent`、`run_shell_command` |
| Slurm 调度问题 | `PENDING`、`Reason=Resources`、`DOWN/DRAIN`、`slurmstepd` | `SlurmAgent`、`check_slurm_queue`、`check_slurm_nodes` |
| Python 环境问题 | `ModuleNotFoundError`、`ImportError`、解释器与 pip 不一致 | `PythonEnvAgent`、`check_python_environment` |
| 混合日志问题 | 多个错误同时出现，需要判断主次故障 | `LogDiagnosisAgent`、`diagnose_mixed_log_file` |

---

## 3. 项目目录结构

推荐目录结构如下：

```text
agentic-linux-troubleshooter/
├── main.py
├── main_multi_agent.py
├── main_multi_agent_v3.py
├── agents/
│   ├── troubleshooting_agent.py
│   ├── domain_agents.py
│   ├── report_agent.py
│   ├── agent_protocol.py
│   ├── manager_agent.py
│   ├── agent_registry.py
│   └── multi_agent_orchestrator_v3.py
├── routers/
│   ├── __init__.py
│   └── issue_router.py
├── tools/
│   ├── shell_tool.py
│   ├── log_tool.py
│   ├── disk_tool.py
│   ├── gpu_tool.py
│   ├── slurm_tool.py
│   └── python_env_tool.py
├── examples/
│   └── logs/
│       └── regression/
├── tests/
│   ├── test_tools.py
│   ├── test_regression_logs.py
│   ├── test_multi_agent.py
│   ├── test_llm_report_agent.py
│   └── test_multi_agent_v3.py
├── outputs/
│   └── reports/
├── configs/
│   └── settings.yaml
├── prompts/
│   └── system_prompt.txt
├── requirements.txt
└── README.md
```

---

## 4. 环境准备

### 4.1 推荐运行环境

本项目建议在 WSL2 Ubuntu 或 Linux 服务器中运行。原因是项目会调用一些 Linux 只读诊断命令，例如：

```bash
df -h
du -sh
ss -lntp
ps
nvidia-smi
hy-smi
squeue
scontrol
sinfo
```

Windows PowerShell 不适合作为主要运行环境。

### 4.2 创建虚拟环境

```bash
cd ~/projects/agentic-linux-troubleshooter
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements.txt
```

### 4.3 验证依赖

```bash
python -c "import smolagents; import dotenv; import yaml; import openai; print('环境依赖正常')"
```

### 4.4 配置 LLM API Key

如果使用 LLMReportAgent，需要在 WSL 环境或 `.env` 中配置：

```bash
export DEEPSEEK_API_KEY=你的真实APIKey
```

或者在项目根目录创建 `.env`：

```env
DEEPSEEK_API_KEY=你的真实APIKey
```

如果没有配置 API Key，V3 的 `--report-mode auto` 会自动回退到规则报告模式。

---

## 5. 阶段演进说明

### 5.1 第一阶段：单 Agent + 多 Tool

入口文件：

```text
main.py
```

第一阶段主要验证 Agent 是否能调用基础工具完成排障。包括：

- 日志读取；
- 日志关键词诊断；
- 磁盘状态检查；
- GPU/DCU 状态检查；
- 安全 Shell 命令执行。

特点：

```text
用户问题
  ↓
CodeAgent
  ↓
自动选择工具
  ↓
生成排障报告
```

优点是能快速跑通 Agent 工具调用流程；不足是工具选择有一定自由度，可能存在重复调用或漏调用。

---

### 5.2 第二阶段：内容感知路由 + 多问题诊断

第二阶段在第一阶段基础上加入：

- `route_issue_type`；
- 内容感知路由；
- 多标签问题识别；
- `primary_issue_type`；
- `secondary_issue_types`；
- `diagnose_mixed_log_file`；
- 主次故障排序；
- 时间线提取。

核心能力：

```text
用户只给日志路径
        ↓
系统读取日志片段参与路由
        ↓
识别主故障和次要问题
        ↓
CodeAgent 按路由结果选择工具
```

对于复杂混合日志，系统可以识别：

```text
primary_issue_type: gpu
secondary_issue_types: ['slurm', 'disk', 'python_env', 'network_port']
```

---

### 5.3 第三阶段 V1：规则多 Agent

入口文件：

```text
main_multi_agent.py
```

V1 将第二阶段的单 Agent 拆成多个职责清晰的领域 Agent：

```text
ManagerAgent
LogDiagnosisAgent
GPUAgent
DiskAgent
PythonEnvAgent
NetworkAgent
SlurmAgent
ReportAgent
```

V1 的报告由规则模板拼接，不调用 LLM。优点是稳定、便宜、可测试；不足是语言略机械。

---

### 5.4 第三阶段 V2：多 Agent + LLMReportAgent

V2 保留规则化 ManagerAgent 和 DomainAgents，但把最终报告生成交给 `LLMReportAgent`：

```text
ManagerAgent / DomainAgents 负责稳定诊断
LLMReportAgent 负责自然语言专家报告
```

重要约束：

- LLMReportAgent 不调用工具；
- LLMReportAgent 不重新诊断原始日志；
- LLMReportAgent 只根据结构化 AgentResult 和 raw_output_excerpt 写报告；
- 危险命令不能出现在 bash 代码块中。

这样既保留了多 Agent 的可控性，又提升了报告可读性。

---

### 5.5 第三阶段 V3：动态 ExecutionPlan 多 Agent

入口文件：

```text
main_multi_agent_v3.py
```

V3 是当前推荐版本。它在 V2 基础上加入动态执行计划：

```text
用户问题
  ↓
内容感知路由
  ↓
DynamicManagerAgent 生成 ExecutionPlan
  ↓
按 agent-depth 运行不同 Agent
  ↓
LLMReportAgent 生成最终报告
```

V3 支持三种执行深度：

| 模式 | 说明 | 适用场景 |
|---|---|---|
| `minimal` | 只运行日志诊断和主故障 Agent | 快速定位 |
| `balanced` | 运行主故障 Agent 和强相关次要 Agent | 默认排障 |
| `full` | 运行所有检测到的问题 Agent | 完整复盘 |

V3 的核心价值是：

- 不再固定运行所有相关 Agent；
- 可以解释为什么运行某个 Agent；
- 可以解释为什么跳过某个 Agent；
- 支持不同排障深度；
- 更接近真实动态多 Agent 协作框架。

---

## 6. 运行方式

### 6.1 第二阶段增强版

```bash
python main.py "帮我分析 examples/logs/regression/08_complex_mixed_failure.log"
```

### 6.2 第三阶段 V2

```bash
python main_multi_agent.py "帮我分析 examples/logs/regression/08_complex_mixed_failure.log" --report-mode auto
```

### 6.3 第三阶段 V3：minimal 模式

```bash
python main_multi_agent_v3.py "帮我分析 examples/logs/regression/08_complex_mixed_failure.log" --agent-depth minimal --report-mode auto
```

### 6.4 第三阶段 V3：balanced 模式

```bash
python main_multi_agent_v3.py "帮我分析 examples/logs/regression/08_complex_mixed_failure.log" --agent-depth balanced --report-mode auto
```

### 6.5 第三阶段 V3：full 模式

```bash
python main_multi_agent_v3.py "帮我分析 examples/logs/regression/08_complex_mixed_failure.log" --agent-depth full --report-mode auto
```

---

## 7. 报告输出

默认报告保存位置：

```text
outputs/reports/
```

常见输出文件：

```text
outputs/reports/last_report.md
outputs/reports/last_multi_agent_report.md
outputs/reports/last_multi_agent_llm_report.md
outputs/reports/last_multi_agent_v3_report.md
outputs/reports/last_multi_agent_v3_llm_report.md
```

建议保留典型示例报告：

```text
outputs/reports/examples/v2_complex_report.md
outputs/reports/examples/v3_minimal_report.md
outputs/reports/examples/v3_balanced_report.md
outputs/reports/examples/v3_full_report.md
```

---

## 8. 测试方式

### 8.1 工具层测试

```bash
python tests/test_tools.py
```

### 8.2 回归日志测试

```bash
python tests/test_regression_logs.py
```

### 8.3 多 Agent V1/V2 测试

```bash
python tests/test_multi_agent.py
```

### 8.4 LLMReportAgent 测试

```bash
python tests/test_llm_report_agent.py
```

### 8.5 V3 动态多 Agent 测试

```bash
python tests/test_multi_agent_v3.py
```

测试重点：

- 路由是否正确；
- 主故障是否正确；
- 次要问题是否完整；
- minimal / balanced / full 是否按预期执行不同 Agent；
- 报告中是否避免危险命令；
- LLM 失败时是否能回退到规则报告。

---

## 9. 示例：复杂混合日志分析结果

针对日志：

```text
examples/logs/regression/08_complex_mixed_failure.log
```

系统应识别：

```text
primary_issue_type: gpu
secondary_issue_types: ['slurm', 'disk', 'python_env', 'network_port']
```

主故障：

```text
GPU/DCU 显存不足（HIP out of memory）
```

次要问题：

```text
Slurm 早期 Pending Resources
/tmp 缓存目录 No space left on device
Python 解释器与 pip 环境不一致
PyYAML 缺失
TensorBoard 9100 端口冲突
```

V3 full 模式会执行：

```text
DynamicManagerAgent
LogDiagnosisAgent
GPUAgent
SlurmAgent
DiskAgent
PythonEnvAgent
NetworkAgent
LLMReportAgent
```

最终报告应包含：

- 总体结论；
- V3 执行计划与多 Agent 协作结果；
- 主故障分析；
- 次要问题与连锁影响；
- 关键时间线；
- 关键证据；
- 只读检查命令；
- 低风险修复建议；
- 需要人工确认的操作；
- 风险提醒与信息不足项。

---

## 10. 安全策略

本项目默认只执行只读诊断操作。报告中不会把危险命令作为可复制执行命令输出。

危险操作包括：

```text
rm -rf
kill -9
scancel
sudo rm
chmod -R
chown -R
mkfs
dd
systemctl restart
```

如果确实需要清理目录、终止进程或取消作业，报告只会以自然语言提醒：

```text
确认缓存目录不再被任务使用后，由用户手动清理。
确认进程归属后，由用户手动终止。
确认作业已失败且无需保留后，由用户手动取消。
```

建议用户始终先执行只读检查命令，例如：

```bash
df -h /tmp
df -ih /tmp
du -sh /tmp/$USER
hy-smi
nvidia-smi
squeue -j <JOB_ID>
scontrol show job <JOB_ID>
ss -lntp | grep 9100
which python
python -m pip --version
```

---

## 11. V2 与 V3 对比

| 版本 | 核心机制 | 优点 | 不足 |
|---|---|---|---|
| Stage 1 | 单 Agent + 多 Tool | 快速验证工具调用 | 工具选择不稳定 |
| Stage 2 | 内容感知路由 + 混合日志诊断 | 主次故障识别准确 | 仍是单 Agent |
| Stage 3 V1 | 规则多 Agent + 规则报告 | 稳定、无 API 成本 | 报告较机械 |
| Stage 3 V2 | 固定多 Agent + LLMReportAgent | 报告自然、结构清晰 | 相关 Agent 基本固定运行 |
| Stage 3 V3 | 动态 ExecutionPlan + agent-depth + LLMReportAgent | 可控、可扩展、支持不同排障深度 | 架构更复杂 |

---

## 12. 设计分层

本项目采用四层结构：

```text
tools：底层诊断工具
routers：内容感知路由
domain_agents：领域诊断 Agent
report_agent：报告生成 Agent
```

### tools

负责执行具体诊断能力，例如：

```text
diagnose_mixed_log_file
check_gpu_status
check_disk_usage
check_python_environment
check_slurm_queue
run_shell_command
```

### routers

负责在进入 Agent 前判断问题类型：

```text
primary_issue_type
secondary_issue_types
all_detected_issue_types
primary_tools
optional_tools
```

### domain_agents

负责解释不同领域证据：

```text
GPUAgent
DiskAgent
PythonEnvAgent
NetworkAgent
SlurmAgent
```

### report_agent

负责汇总报告：

```text
ReportAgent：规则报告
LLMReportAgent：LLM 专家报告
```

V3 额外加入：

```text
DynamicManagerAgent
ExecutionPlan
AgentRegistry
MultiAgentOrchestratorV3
```

---

## 13. 后续可扩展方向

当前项目主线已经完成，后续可以选择性扩展：

### 13.1 交互式排障模式

让 Agent 在信息不足时主动追问：

```text
请提供 scontrol show job 输出。
请提供 hy-smi 输出。
请提供训练配置文件。
```

### 13.2 部分领域 Agent LLM 化

不建议把所有领域 Agent 都改成 LLM Agent。可以优先考虑：

```text
SlurmAgent
PythonEnvAgent
```

原因是这两类问题语义更复杂，规则诊断可能不足。

### 13.3 接入真实服务器只读命令

在真实 Slurm 登录节点或训练服务器上运行：

```text
squeue
scontrol
sinfo
hy-smi
nvidia-smi
df
ss
```

将日志分析 Demo 扩展成真实服务器排障助手。

### 13.4 支持配置文件分析

后续可以加入：

```text
Slurm 脚本分析
训练 YAML 配置分析
requirements.txt / environment.yml 分析
```

用于判断 batch size、资源申请、依赖版本是否合理。

---

## 14. 当前项目状态

当前版本可以认为已经完成：

```text
功能完成：是
复杂日志分析：完成
主次故障排序：完成
多 Agent 协作：完成
动态执行计划：完成
LLM 报告生成：完成
安全策略：基本完成
回归测试：已具备基础测试集
```

建议后续主要进行：

```text
README 和设计文档整理
示例报告归档
回归测试固化
少量真实日志测试
```

不建议继续盲目增加大量新工具，以免破坏当前稳定结构。
