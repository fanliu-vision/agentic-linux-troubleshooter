# MonitorLoop 多事件接入计划与完成状态

## 1. 当前流程

`MonitorLoop.run_once()` 是 monitor cycle 的入口。

当前流程如下：

1. 通过 `self.watcher.poll()` 读取日志 chunk；
2. 对每个 chunk 调用 detector；
3. R10-3b 后优先调用 `self.detector.detect_all(text=chunk.content, source=...)`；
4. 若 detector 不支持 `detect_all()`，回退到 `detect()`；
5. 将返回的 `ErrorEvent` 候选追加到 `candidate_events`；
6. 通过 `_select_events_for_cycle()` 做 event_type 聚合、排序和数量限制；
7. 对每个选中的 event 独立检查 `event.fingerprint` 是否已在 `seen_fingerprints` 中；
8. 已 seen 的 event 跳过；
9. 未 seen 的 event 调用 `_handle_event(event)`；
10. `_handle_event()` 成功返回 `CycleEventRecord` 后调用 `_mark_event_seen(event)`；
11. 成功 event 追加到 `detected_events`；
12. 成功 `CycleEventRecord` 追加到 `cycle_records`；
13. 如果没有检测到 event，则更新 idle 状态；
14. 如果存在 cycle record，则写周期汇总报告；
15. 返回 `detected_events`。

`_handle_event()` 负责单个 event 的处理链路：

- 输出 alert block；
- 将 event evidence 加入 troubleshooting session；
- 调用 `AutoRecoveryRunner.recover(event)`；
- 追加 recovery report path；
- 在启用 persistent state 时更新 report 与状态计数；
- 调用 `NotificationManager.notify_recovery(...)`；
- 在 session 支持时记录 notification 结果；
- 按配置生成通知后报告；
- 返回 `CycleEventRecord`。

`cycle_records` 是每轮周期汇总的输入。`detected_events` 是 `run_once()` 的返回值，并用于 daemon idle-cycle 行为。

## 2. 当前安全边界

当前 loop 保持以下安全属性：

- `_handle_event()` 成功后才 mark seen；
- `_handle_event()` 抛异常时不 mark seen；
- 单个 event 失败不会让整个 daemon 崩溃；
- 单个 event 失败不会阻止后续 event 处理；
- 如果所有 event 处理失败，项目状态会进入 `event_handling_failed`；
- 周期汇总写入使用 `try/except` 包裹，summary 失败不导致 daemon 崩溃；
- 通知后报告生成失败只记录错误；
- notification dispatch 失败不应终止 monitor cycle；
- 高风险故障域按 policy 进入 `manual_escalation`；
- 自动恢复仍由既有 policy 与受控 fix 规则限制。

R10-3b 和 R10-4g 均保留这些边界。

## 3. multi-event 接入设计

R10-3b 将候选事件收集从单事件兼容路径扩展到多事件路径：

```text
events = detector.detect_all(log_text)
events = events[:MAX_EVENTS_PER_CYCLE]
```

实际硬限制：

```text
MAX_EVENTS_PER_CYCLE = 3
MAX_AUTO_RECOVER_PER_CYCLE = 1
```

接入规则：

- 每个 event 保留独立 `fingerprint`；
- 每个 event 独立检查 `seen_fingerprints`；
- 每个 event 独立调用 `_handle_event(event)`；
- 成功 event 在自身处理完成后才 mark seen；
- 失败 event 不 mark seen；
- 单轮最多处理 `3` 个 event；
- 单轮最多执行 `1` 个自动恢复；
- 超过本轮上限的 event 跳过或延后；
- 保留 `detect()` 兼容路径；
- detector 没有 `detect_all()` 时回退到 `detect()`。

该实现保持既有 `run_once()` 生命周期，只替换候选事件收集并增加恢复限流。既有 `cycle_records` 和汇总报告流程可以承载多个成功 event。

## 4. 自动恢复安全策略

multi-event 支持不得扩大自动恢复动作面。

R10-3b 已实现规则：

- 同一 monitor cycle 不执行多个自动恢复；
- 多个 auto-recover 候选同时出现时，只允许第一个候选执行；
- 其余 auto-recover 候选在该轮跳过并记录警告；
- 不新增自动 `kill`、`rm`、任意 `pip install`、`systemctl` 或 `kubectl` 操作；
- 保持当前 `network_port` 与 `gpu_oom` 规则；
- 不改变 policy 语义。

自动恢复限流是 `MonitorLoop` 编排层职责，不是新增危险 fix 的理由。

## 5. R10-3b 测试覆盖

R10-3b 新增测试覆盖以下场景：

1. 同一轮中两个 `manual_escalation` event 均被处理；
2. 生成两个独立 `CycleEventRecord`；
3. 两个 event 成功时都 mark seen；
4. 一个成功一个失败时，仅成功 event mark seen；
5. 已 seen event 被跳过，不影响另一个未 seen event；
6. 超过 `3` 个 event 时只处理前 `3` 个；
7. 多个 auto-recover 候选同轮最多执行 `1` 个；
8. 多个 `manual_escalation` event 可生成多个 report；
9. 周期汇总支持多个 event record；
10. 单事件行为保持不变。

测试文件：

```text
tests/test_stage6e_monitor_loop_multi_event.py
```

测试使用 stub watcher、detector、`_handle_event()` 与汇总写入，不依赖真实 `state/` 或 `outputs/`。

## 6. R10-3b 修改范围

R10-3b 修改过：

```text
monitors/monitor_loop.py
tests/test_stage6e_monitor_loop_multi_event.py
docs/multi_event_window_design.md
docs/monitor_loop_multi_event_integration_plan.md
```

原则上不修改：

```text
AutoRecoveryRunner
policy
notification manager
file notifier
systemd unit
```

R10-5 仅更新文档，不修改运行逻辑。

## 7. 风险与缓解

主要风险：

- 多个独立 event 在同一轮触发 report 风暴；
- 多个 manual escalation 触发 alert 风暴；
- failed event 被过早 mark seen 造成状态混乱；
- 周期汇总过长；
- 多个自动恢复在同一轮执行；
- 旧单事件测试回归；
- scoped evidence 与旧 full-window evidence 混合导致排障混乱；
- recovery report、通知后报告、周期汇总同时存在时被误判为重复报告。

缓解措施：

- 保持每轮 event 硬上限；
- 保持每轮 auto-recovery 硬上限；
- 保持成功后才 mark seen；
- summary 生成保持尽力而为；
- 测试覆盖成功与失败混合场景；
- 文档说明每个 event 可能生成人工升级报告和通知后报告，这不属于重复生成。

## 8. R10-3b 完成状态

R10-3b 已完成以下内容：

1. 增加 `MAX_EVENTS_PER_CYCLE`；
2. 增加 `MAX_AUTO_RECOVER_PER_CYCLE`；
3. detector 支持时使用 `detect_all()` 收集候选事件；
4. detector 不支持时回退 `detect()`；
5. 对每个 event 独立检查 `seen_fingerprints`；
6. 对每个选中 event 调用 `_handle_event()`；
7. event 处理成功后才 mark seen；
8. event 处理失败不 mark seen；
9. 使用既有周期汇总流程汇总多个成功 record；
10. 保持现有 pytest 通过。

## 9. R10-4f/R10-4g remote tail 截断问题

R10-4f 发现 daemon 使用了预期的 project config 和 log path：

```text
/home/lf/runtime_projects/enterprise_order_monitoring_service/outputs/service.log
```

当前 combined smoke 存在于 runtime log，且仍在 `tail_lines=200` 范围内。但远程日志 watcher 通过 `RemoteReadonlySSHExecutor` 包装 `tail -n 200` 输出。该 executor 原默认截断策略保留前 `max_output_chars` 字符。当 benign filler 行较长时，前缀截断会在故障行之前发生，导致 detector 收到的内容只有 filler。

当时观察到：

- 默认 watcher 输出包含 filler 和 `[REMOTE_OUTPUT_TRUNCATED]`；
- 默认 watcher 输出不包含 `status=11/SEGV`、`core-dump`、`r10-combined-worker`、`r10-combined-api`、`OOMKilled`；
- 默认 watcher 输出调用 `detect_all()` 返回空列表；
- 未截断 watcher 输出调用 `detect_all()` 返回 `process_crash` 与 `container_k8s`。

R10-4g 已修复远程日志 tail 输入路径：日志 tail 场景截断时保留输出尾部。普通远程只读命令仍保持既有前缀截断行为。该特殊行为仅由 `read_remote_log_tail()` 启用，因为日志监控必须保留最新日志行。

## 10. R10-4h live smoke 结果

R10-4h 在用户手动重启 daemon 后运行一次 clean multi-event live smoke，结果通过。

验证内容：

- `process_crash` 和 `container_k8s` 在同一日志窗口中被识别；
- 两个 event 均生成当前 smoke 对应的独立 report/alert；
- daemon 稳定；
- 未发现危险自动操作；
- daemon log 已验证 `events_detected=2`；
- remote tail 尾部保留修复在 live daemon 中生效。

关键产物：

```text
acceptance_artifacts/multi_event_live_smoke_r10_clean_20260617_105036/R10_4D_CLEAN_MULTI_EVENT_LIVE_SMOKE_SUMMARY.md
acceptance_artifacts/r10_run_logs/r10_4h_clean_multi_event_live_smoke_20260617_105036.log
```

## 11. 安全结论

R10 未新增危险自动恢复逻辑。

R10 未新增以下自动操作：

- `kill`
- `rm`
- 任意 `pip install`
- `systemctl`
- `kubectl`

R10 未突破现有 policy 边界。自动恢复仍受 `MAX_AUTO_RECOVER_PER_CYCLE = 1` 限制。

## 12. R10 收尾结论

R10-3b 已完成 `MonitorLoop` 接入 `detect_all()`。

R10-4h 已通过 live smoke，同窗口多事件处理已可在 live daemon 中生成两个独立事件报告。

R10 可以标记为完成。
