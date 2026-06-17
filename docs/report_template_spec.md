# R11-1 中文报告模板规范

## 一、适用范围

本文定义后续 R11-2 可采用的中文报告模板规范。该规范只描述报告正文结构和质量要求，不要求在 R11-1 修改任何报告生成代码。

适用报告类型包括：

- 事件排障报告
- 通知后状态报告
- 周期汇总报告
- 同窗口多事件报告

报告中可以保留英文文件名、路径、event_type、类名、函数名、命令、字段名和原始日志。除这些技术标识外，正文说明应使用中文。

## 二、事件排障报告模板

事件排障报告用于解释单个 event 的检测、证据、影响、策略、处置边界和人工排查步骤。每个 event 应独立生成一份报告，不能把另一个 event 的状态混写成当前 event 的结果。

模板结构如下：

```text
# 事件排障报告

## 一、事件概览
## 二、检测结果
## 三、关键证据
## 四、影响判断
## 五、根因分析
## 六、处置策略
## 七、安全边界
## 八、建议的人工排查步骤
## 九、验证方式
## 十、后续观察建议
```

### 一、事件概览

必须包含：

- 项目名称或 project_id。
- event_type。
- event id 或 fingerprint。
- source，例如 `remote_log:/path/to/service.log`。
- severity。
- action，例如 `auto_recover`、`manual_escalation`、`report_only`、`rollback_done`、`unresolved`。
- status 或 event_recovery_status。
- 是否已生成 report。
- 是否已生成 alert。

### 二、检测结果

必须说明：

- detector 识别到的 event_type。
- 事件摘要。
- 是否为本轮主事件。
- 是否来自同一日志窗口中的多个 event。
- 如果存在多个 event，只能说明“同窗口还检测到其他事件”，不得把其他事件写成当前事件已处理结果。

### 三、关键证据

必须包含：

- 原始 evidence。
- 日志来源。
- 行号或时间戳，如果上下文提供。
- fingerprint 或 event id。
- 匹配到的关键字或模式。

证据必须来自输入上下文，不得拼接、改写或编造不存在的错误行。

### 四、影响判断

必须说明：

- 当前事件可能影响的服务、任务或用户侧表现。
- 影响判断的证据来源。
- 不确定项必须标注“可能”“需要进一步确认”或“当前证据不足以证明”。

### 五、根因分析

必须区分：

- 已确认事实。
- 合理推断。
- 需要人工确认的信息缺口。

不得把关联事件强行写成确定因果。例如 `process_crash` 与 `container_k8s` 可以写为“可能相关”，但不能在缺少证据时写成“必然由对方导致”。

### 六、处置策略

必须包含：

- policy action。
- 是否 auto_recover。
- 是否 manual_escalation。
- fix_id，如果存在。
- apply_success、rerun_success、rollback_executed、recovered，如果上下文提供。
- 已执行动作和未执行动作。

如果 action 为 `manual_escalation`，必须明确写出“未执行自动修复”。

### 七、安全边界

必须明确：

- 不建议自动执行危险命令。
- 涉及 `kill`、`rm`、`pip install`、`systemctl`、`kubectl` 的建议必须标注“需人工确认”。
- 不得把 `manual_escalation` 描述成已自动修复。
- 不得夸大系统已经完成的动作。
- 不得暗示 Agent 已执行实际未执行的 apply、rerun、rollback、restart 或 cleanup。

### 八、建议的人工排查步骤

必须分为两类：

- 只读检查步骤。
- 需人工确认的处置步骤。

只读检查步骤可以给出命令示例。需人工确认的处置步骤优先用自然语言描述，不应放入可直接复制执行的危险命令代码块。

### 九、验证方式

必须包含：

- 验证目标。
- 建议的只读验证命令。
- 预期看到的恢复信号。
- 如果需要重启、删除、终止进程、安装依赖或执行 `kubectl` 变更，必须写明“需人工确认后执行”。

### 十、后续观察建议

必须说明：

- 建议观察的日志、指标或状态文件。
- 建议观察时间窗口。
- 重复出现时应如何升级。
- 仍然缺少哪些信息。

## 三、通知后状态报告模板

通知后状态报告用于记录通知是否发出、状态是否更新、生成了哪些工件。该报告应比事件排障报告更短，避免重复完整根因分析。

模板结构如下：

```text
# 通知后状态报告

## 一、通知结果
## 二、事件状态
## 三、已生成的工件
## 四、后续建议
```

### 一、通知结果

必须包含：

- notification_status。
- notification_channels。
- 通知是否成功。
- alert 路径或归档路径。

### 二、事件状态

必须包含：

- event_type。
- action。
- status。
- 是否 auto_recover。
- 是否 manual_escalation。
- 是否 recovered。

该章节只做状态确认，不重复完整根因分析。

### 三、已生成的工件

必须列出：

- event report 路径。
- post-notification report 路径。
- alert 路径。
- cycle summary 路径，如果已生成。

### 四、后续建议

必须保持简短：

- 如果已恢复，建议继续观察。
- 如果人工升级，提醒负责人查看事件排障报告。
- 如果仍未恢复，说明需要人工确认下一步。

## 四、周期汇总报告模板

周期汇总报告用于确定性展示一轮 MonitorLoop 中已处理 event 的状态。它应作为总体状态口径基准。

建议表格如下：

| event_type | action | status | 是否自动恢复 | 是否人工升级 | report 路径 | alert 路径 |
|---|---|---|---|---|---|---|
| `process_crash` | `manual_escalation` | `manual_escalation` | 否 | 是 | `...` | `...` |
| `container_k8s` | `manual_escalation` | `manual_escalation` | 否 | 是 | `...` | `...` |

周期汇总报告必须说明：

- events_total。
- recovered_count。
- manual_escalation_count。
- rollback_count。
- unresolved_count。
- overall_status。
- overall_status 只统计本轮已进入处理链路的事件，不代表系统不存在残留风险。

## 五、同窗口多事件报告要求

当同一轮出现多个 event 时，报告必须遵守以下规则：

1. 每个 event 必须有独立小节。
2. 每个 event 必须有自己的 event_type、fingerprint、evidence、action、status、report 路径和 alert 路径。
3. 不要把两个事件混成一个根因。
4. 可以说明两个事件可能相关，但不能强行绑定因果关系。
5. 每个 event 都要有自己的处置策略。
6. 如果一个 event 成功、另一个 event 人工升级，必须明确区分，不得把整体写成全部恢复。
7. 如果同一事件生成 manual escalation report 和 post-notification report，应说明二者职责不同，不属于重复生成。

## 六、安全约束

所有报告都必须明确：

- 不建议自动执行危险命令。
- 涉及 `kill`、`rm`、`pip install`、`systemctl`、`kubectl` 的建议必须标注“需人工确认”。
- 不得把 `manual_escalation` 描述成已自动修复。
- 不得夸大系统已经完成的动作。
- 不得把未执行的重启、清理、安装、删除、终止进程、集群变更写成已执行。
- `auto_recover` 的描述必须受 policy 和当前执行结果约束。
- 对不确定原因必须使用“可能”“需要进一步确认”“当前证据不足以证明”等表述。

## 七、R11-2 模板修改建议

R11-2 建议做小改，不建议重写报告链路。

优先修改点如下：

1. 统一事件排障报告章节为本文模板。
2. 缩短通知后状态报告。
3. 固定周期汇总报告表格字段。
4. 为每个报告固定加入安全边界。
5. 为每个报告固定加入验证方式。
6. 在多事件报告中强化事件边界。
7. 增加不确定性说明，避免过度推断。

## 八、R11-2 已完成的小改

R11-2 已将本文规范落到报告 prompt 的最小修改中，修改范围保持在报告生成意图和报告结构层面。

已完成内容如下：

- 事件排障报告统一使用本文的十章节中文结构。
- `post_notification` 报告使用短模板，避免重复完整根因分析。
- 事件排障报告固定包含“安全边界”章节。
- 事件排障报告固定包含“验证方式”章节。
- multi-event 报告要求每个 event 拥有独立 evidence、处置策略和状态说明。
- prompt 已要求使用“可能”“需要进一步确认”“从当前证据看”“尚不能确定”等不确定性表达。
- `cycle_*_summary_report.md` 的确定性状态口径保持稳定，未重写周期汇总逻辑。

后续如果进入 R11-3，可优先补充轻量测试，验证生成 prompt 中稳定包含这些章节名称和安全约束。
