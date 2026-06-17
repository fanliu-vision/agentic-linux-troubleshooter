# R11-1 报告模板质量审计

## 一、审计范围

本次审计只读取现有报告样本和报告生成相关源码位置，不修改报告生成逻辑，不修改历史 `outputs` 报告文件。

已检查的事件报告样本包括：

- `outputs/monitors/enterprise_demo_local/**/event_*_process_crash_*_final_llm_report.md`
- `outputs/monitors/enterprise_demo_local/**/event_*_container_k8s_*_final_llm_report.md`
- `outputs/monitors/enterprise_demo_local/**/event_*_network_port_*_final_llm_report.md`
- `outputs/monitors/enterprise_demo_local/**/event_*_gpu_oom_*_final_llm_report.md`

已检查的通知后报告样本包括：

- `event_*_process_crash_post_notification_final_llm_report.md`
- `event_*_container_k8s_post_notification_final_llm_report.md`
- `event_*_network_port_post_notification_final_llm_report.md`
- `event_*_gpu_oom_post_notification_final_llm_report.md`

已检查的周期汇总报告样本包括：

- `outputs/monitors/enterprise_demo_local/**/cycle_*_summary_report.md`

未发现独立命名为 `event_*_manual_escalation_*_final_llm_report.md` 且 event_type 为 `manual_escalation` 的样本。现有人工升级形态主要体现在 `process_crash`、`container_k8s`、`disk_full` 等事件的 `action=manual_escalation` 报告中，因此本次以这些样本覆盖人工升级报告审计。

同时已定位报告生成相关代码位置，结果保存到 `/tmp/report_generator_locations.txt`。核心位置包括：

- `agents/report_agent.py`
- `monitors/monitor_loop.py`
- `monitors/cycle_summary_reporter.py`
- `notifiers/`
- `policies/remediation_policy.py`

## 二、当前优点

### 事件说明能力

现有事件报告通常能明确说明项目、事件类型、严重级别、策略动作和最终状态。`network_port`、`gpu_oom`、`process_crash`、`container_k8s` 等样本都能让读者快速知道当前事件是自动恢复、人工升级还是仍需排查。

### 证据引用能力

多数报告能够引用日志来源、关键错误行、fingerprint、fix_id、apply/rerun 结果和通知路径。`process_crash` 样本能展示 `status=11/SEGV`、`core-dump` 等关键证据；`container_k8s` 样本能展示 `BackOff`、`ImagePullBackOff`、`OOMKilled` 等证据；周期汇总报告能给出每个事件的 fingerprint 和状态。

### 排查建议能力

现有报告通常会给出下一步排查方向，例如查看 core dump、检查容器日志、检查端口占用、检查磁盘空间、确认 Python 环境、查看 GPU 显存状态等。自动恢复事件也会说明已执行的 fix_id、修改字段和 rerun 返回码。

### 人工升级表达能力

`manual_escalation` 事件基本能说明未执行自动修复，并提醒负责人介入。`process_crash` 和 `container_k8s` 报告能够体现高风险事件需要人工处理。

### 安全边界表达能力

多数报告会说明不建议自动执行高风险操作，并把清理缓存、终止进程、重启服务、分析 core dump、执行 `kubectl` 等内容放到人工确认区域。`cycle_*_summary_report.md` 由确定性代码生成，能够降低 LLM 把部分恢复误写为全部恢复的风险。

## 三、当前不足

### 结构不完全统一

事件报告大体稳定，但不同 event_type 和不同生成时机的章节内容仍有差异。有些报告以单事件为主，有些报告会写入多个事件；有些报告强调自动恢复链路，有些更像综合排障报告。建议 R11-2 统一事件报告、通知后报告和周期汇总报告的章节边界。

### 部分报告偏长

部分 `network_port` 和 `gpu_oom` 报告超过一百五十行，包含多个次要问题、历史风险和命令建议。信息完整性较好，但一线值班读者需要先筛选重点。建议保留详细信息，但把结论、状态、证据、下一步动作放在更靠前的位置。

### 通知后报告与事件报告重复

`post_notification` 报告当前仍可能完整复制事件排障报告的结构，导致通知后报告和人工升级报告内容高度重复。建议通知后报告只保留通知状态、事件状态、已生成工件和后续建议，不再重复完整根因分析。

### 部分建议不够具体

有些建议命令或人工操作偏通用，例如只说检查服务、检查日志、查看容器状态。建议 R11-2 在模板中要求区分只读验证命令、需人工确认的操作、禁止自动执行的危险操作。

### 缺少稳定的下一步验证命令区

多数报告已经包含命令，但章节名称和安全边界不完全统一。建议每份事件报告固定包含“验证方式”，并明确哪些命令是只读，哪些命令需要人工确认。

### 缺少置信度和不确定性说明

现有报告有时会把“可能相关”的事件写得较像确定因果。尤其在同窗口多事件场景中，`process_crash` 和 `container_k8s` 可以互相关联，但不能强行说明一个必然导致另一个。建议模板要求使用“可能”“需要进一步确认”“当前证据不足以证明”等表述。

### 危险命令禁止声明不够显式

报告通常会把危险操作放入人工确认区域，但不是每份报告都明确写出“不得自动执行”。建议模板固定加入安全边界章节，明确 `kill`、`rm`、`pip install`、`systemctl`、`kubectl` 等操作必须人工确认，不能由报告暗示自动执行。

### 个别多事件报告存在事件边界混合

部分样本会在单个事件报告中描述其他事件，并且路径或归档文件可能出现跨事件引用。R10 已验证同窗口多事件可以独立生成 report/alert；R11-2 应进一步要求每个 event 保持自己的 evidence、fingerprint、action、report 路径和 alert 路径。

## 四、报告评分

评分采用五分制，分数表示当前样本的总体表现，不代表代码质量。

| 报告类型 | 完整性 | 可读性 | 可执行性 | 安全性 | 中文表达 | 证据可追溯性 | 说明 |
|---|---:|---:|---:|---:|---:|---:|---|
| event report | 4 | 4 | 3 | 4 | 4 | 4 | 信息完整，证据较清楚，但结构和命令安全分区还可统一。 |
| post-notification report | 3 | 3 | 2 | 4 | 4 | 3 | 通知结果可读，但与事件报告重复较多，后续建议应更短。 |
| cycle summary | 4 | 5 | 4 | 5 | 4 | 5 | 确定性强，状态表清晰，适合作为总体状态基准。 |
| multi-event report | 4 | 3 | 3 | 4 | 4 | 4 | 已能覆盖多个事件，但需要进一步强化事件边界和不确定性表达。 |

## 五、总体评价

当前报告质量整体可用，已经能支撑 R10 之后的多事件排障验收。主要价值在于能保留证据、说明策略动作、区分自动恢复与人工升级，并生成确定性的周期汇总。

当前主要问题不是能力缺失，而是模板边界不够统一：事件报告、通知后报告和周期汇总报告承担的职责需要进一步分离；多事件场景需要更明确地保证每个 event 独立成段；危险操作的安全声明需要固定化。

## 六、是否建议进入 R11-2

建议进入 R11-2，但只建议小改，不建议大改。

R11-2 的优先级建议如下：

1. 统一事件排障报告章节。
2. 缩短 `post_notification` 报告，避免重复完整根因分析。
3. 固定“安全边界”和“验证方式”章节。
4. 在多事件报告中强制每个 event 拥有独立 evidence、处置策略和状态说明。
5. 增加置信度与不确定性表达，避免把可能关联写成确定因果。
6. 保持现有 `cycle_*_summary_report.md` 的确定性状态口径，不建议重写。

## 七、R11-2 小幅优化状态

R11-2 已按小改原则完成模板优化，没有重写检测、策略、恢复、通知和周期汇总逻辑。

本次优化内容如下：

1. 事件排障报告 prompt 已统一为十个中文章节：事件概览、检测结果、关键证据、影响判断、根因分析、处置策略、安全边界、建议的人工排查步骤、验证方式、后续观察建议。
2. 通知后报告 prompt 已改为短格式，只保留通知结果、事件状态、已生成工件和后续建议，避免重复完整根因分析和长篇 raw evidence。
3. 安全边界章节已固定要求说明是否执行自动恢复、是否需要人工升级、是否存在危险操作，以及 `kill`、`rm`、`pip install`、`systemctl`、`kubectl` 等操作需人工确认。
4. 验证方式章节已固定要求优先使用只读命令，并避免直接建议破坏性操作。
5. multi-event 报告要求已补强：每个 event 必须拥有独立 evidence、处置策略和状态说明。
6. 不确定性表达已补强：多事件之间只能写“可能相关”“需要进一步确认”“从当前证据看”“尚不能确定”，不得把可能关联写成确定因果。
7. `cycle_*_summary_report.md` 的确定性状态口径保持不变，未重写周期汇总逻辑。

R11-2 后，报告质量预期提升点主要是结构一致性、通知后报告简洁性、安全边界清晰度和多事件边界清晰度。
