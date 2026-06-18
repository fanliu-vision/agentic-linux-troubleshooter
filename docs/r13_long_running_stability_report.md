# R13 长期运行稳定性验证报告

## 1. 背景

R13 在 R12 功能闭环和 GitHub baseline 建立之后执行，目标从新增能力转向长期运行稳定性验证。进入 R13 前，系统已经具备 daemon/systemd 守护、heartbeat 与 `project_status`、persistent `seen_fingerprints`、detector/policy/auto_recover、manual escalation、notification/alerts 归档、report 生成、multi-event-per-window、remote tail 修复，以及 core/regression tests。

R13 不扩展新故障域，不扩大权限，不新增恢复动作，不修改 systemd unit，也不做破坏性注入。测试重点是验证 daemon 在只读观察、benign 日志、单事件和 multi-event 场景下是否稳定运行，以及 report/alert/state/outputs 是否保持可控。

## 2. 验证范围

本轮覆盖以下稳定性目标：

- daemon 持续 `active/running`；
- heartbeat 与 `project_status.json` 持续更新；
- `daemon.log` 与 journal 无 Traceback、AttributeError、crashed 或异常退出；
- benign 日志不触发 detector；
- 单事件 `process_crash` 能生成独立 report/alert；
- multi-event `process_crash + container_k8s` 能独立识别、独立处理、独立生成 report/alert；
- report evidence 绑定当前事件，不复用旧事件上下文；
- alert 的 `report_paths` 指向对应 report；
- persistent `seen_fingerprints` 能去重旧事件且不跳过新事件；
- `auto_recover` 不在 manual escalation 场景误触发；
- reports/alerts 不出现风暴；
- outputs/state 不异常膨胀；
- CPU/RSS 在观察窗口内稳定。

## 3. R13 分阶段结果

| 阶段 | 验证内容 | 结果 | 关键结论 |
| -- | -- | -- | -- |
| R13-1 | 长期稳定性测试规划 | PASS | 新增测试规划文档，明确阶段、指标、验收标准和风险，不修改运行逻辑 |
| R13-2 | 30 分钟 daemon 只读观察 | PASS | 服务全程 `active/running`，MainPID 稳定，heartbeat/project_status 更新，reports/alerts/state 无异常增长，CPU/RSS 稳定；危险关键词历史文本命中不作为新增危险操作 |
| R13-3 | 30 分钟 benign 日志稳定性测试 | PASS | benign 场景未误触发 detector，reports `63 -> 63`，alerts `40 -> 40`，state 文件数稳定，无新增危险操作证据 |
| R13-4 | 受控单事件 `process_crash` | 初次 FAIL | runtime log 与 watcher 均包含新事件，但同类事件聚合导致新事件沿用旧 fingerprint 并被 seen-skip |
| R13-4b/R13-4d | 单事件修复与 live 复测 | PASS | 新 `process_crash` 获得独立 fingerprint，进入 `_handle_event()`，生成 report/alert，走 `manual_escalation`，未触发 `auto_recover` |
| R13-5 | 受控 multi-event 企业故障测试 | 初次 FAIL | detector/watcher/selection/state 均通过，但两个 final report 未绑定本次 R13-5 evidence；观察期间 MainPID 变化由外部停电影响，不计为 Agent 问题 |
| R13-5b | multi-event report evidence 修复复测 | PASS | `process_crash` 与 `container_k8s` report 分别绑定各自事件 evidence，两个 alert 均正确指向对应 report，无旧 evidence 污染 |
| R13-5c | report/alert 生成后短时只读观察 | PASS | daemon 稳定，MainPID `36153` 不变，reports `88 -> 88`，alerts `54 -> 54`，无重复报告、alert 风暴、`auto_recover` 或资源异常增长 |
| R13-6 | 阶段收尾与文档总结 | PASS | 形成最终报告和后续硬化建议 |

## 4. 已修复问题

### 4.1 同 event_type 多实例导致新事件被旧 fingerprint 跳过

R13-4 初次受控单事件测试中，runtime log 已写入新的 `process_crash` SMOKE_ID，watcher 也能读取到该日志，detector 对单独注入文本可以识别 `process_crash`。但同一 tail 窗口中存在旧 R10 `process_crash` 行，`detect_all()` 按 `event_type` 聚合同类事件，导致新 R13 事件被合并进旧事件 excerpt，fingerprint 仍使用旧 R10 signature。

由于该旧 fingerprint 已存在于 persistent seen，MonitorLoop 跳过候选事件，最终未进入 `_handle_event()`，也没有生成 report/alert。

修复后，`detect_all()` 支持同一 `event_type` 返回多个不同 fingerprint 的候选事件，并按 marker 拆分相邻事件，MonitorLoop selection 优先选择未 seen 事件。R13-4d live 复测确认新 `process_crash` 获得独立 fingerprint，生成 report/alert，未被旧 fingerprint 抢占。

### 4.2 multi-event report 复用旧 evidence

R13-5 初次 multi-event 测试中，`process_crash + container_k8s` 两个事件均被 detector、watcher 和 `_select_events_for_cycle()` 正确识别，state/events 也记录了两个新 fingerprint，alert 生成路径为 `manual_escalation`。但两个 final report 正文未绑定本次 R13-5 evidence，`container_k8s` report 仍描述旧 `process_crash` 内容。

根因是 report 生成时复用 session 级历史 evidence，上下文没有限定到当前事件。修复后，report 生成支持当前事件 scoped evidence，`auto_recovery_runner` 与 `monitor_loop` 在生成 event report 时传入本次事件对应 evidence。

R13-5b live 复测确认两个 report 分别包含各自 `PROCESS_CRASH_ID` 与 `CONTAINER_K8S_ID`，alert 的 `report_paths` 指向对应 report，无旧 R13-4/R13-4d evidence 污染。

## 5. 最终稳定性结论

R13 最终稳定性结论为 PASS。

已验证：

- daemon 在只读、benign、单事件、multi-event 和 post-report 观察中保持稳定；
- heartbeat 与 `project_status` 正常更新；
- benign 日志未造成误报；
- 单事件与 multi-event 均能进入正确处理链路；
- `seen_fingerprints` 能跳过旧事件，同时修复后不会跳过 marker/fingerprint 不同的新事件；
- report evidence 与当前事件绑定；
- alert 指向正确 report；
- report/alert 数量在事件处理后不继续增长；
- CPU/RSS 未出现持续增长；
- `daemon.log` 未出现 Traceback、AttributeError、crashed；
- R13-5 中的 MainPID 变化来自外部停电影响，不计为 Agent 稳定性问题。

## 6. 安全结论

R13 验证期间未发现危险自动操作执行证据。

已确认：

- manual escalation 场景未触发 `auto_recover`；
- 未扩大自动恢复权限；
- 未新增危险自动命令；
- 未执行 systemd 启停、sudo 或破坏性注入作为 Agent 自动行为；
- R13-2/R13-3 的危险关键词命中来自历史文本或说明性文本，后续采用 baseline vs final diff 判断新增证据；
- R13-5b/R13-5c 未出现 `auto_recover`、重复报告或 alert 风暴。

## 7. 剩余风险

R13 未覆盖或仍建议继续观察的风险：

- 长周期运行下 `daemon.log`、outputs、alerts、reports 仍需要保留策略和轮转策略；
- `seen_fingerprints` 长期累积后可能需要 compact 或索引优化；
- 未来新增 detector 后仍需复测同窗口同类事件、多事件 evidence scope 和 fingerprint 去重；
- LLM 长时间不可用时的 fallback 状态记录仍可增强；
- notification 失败、summary 失败的长期统计仍可更细化；
- remote tail 在更大日志量和更复杂远程环境下仍需周期性回归；
- CPU/RSS 趋势目前以短时采样为主，尚未形成长期趋势报表。

## 8. 后续建议

建议进入后续硬化阶段，但不阻塞 R13 结项：

- 增加 `daemon.log` 轮转；
- 增加 outputs/reports/alerts 保留策略；
- 增加 `seen_fingerprints` compact；
- 增加 alert/report rate limit；
- 增强 daemon health check；
- 生成长期运行 stability summary；
- 记录 LLM fallback 状态；
- 增加 stability report 自动汇总；
- 将 R13-4/R13-5 暴露的问题沉淀为回归测试，覆盖同类事件多实例和 scoped evidence report。

## 9. R13 最终结论

R13 可以标记完成。

最终结论：**PASS**。

系统在 R13 验证范围内满足长期运行稳定性目标：daemon 稳定运行，heartbeat/project_status 正常，benign 日志无误报，单事件与 multi-event 处理正常，report/alert 绑定正确，无 `auto_recover` 误触发，无重复报告或 alert 风暴，CPU/RSS 稳定，`daemon.log` 无关键异常。
