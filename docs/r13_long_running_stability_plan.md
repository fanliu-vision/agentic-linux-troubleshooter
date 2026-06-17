# R13 长期运行稳定性测试规划

## 1. 背景

R12 已完成，GitHub baseline 已建立，当前基线包括 `main` 与 `stage6e-r11-stable`。

当前系统已经完成主要功能闭环：daemon/systemd 守护、heartbeat 与 `project_status`、`daemon.log`、persistent `seen_fingerprints`、detector/policy/auto_recover、manual escalation、notification 与 alerts 归档、report 生成、multi-event-per-window、remote tail 修复，以及 core tests/regression tests。

R13 的重点从功能扩展转向长期运行稳定性验证，目标是在不扩大故障域、不新增恢复动作、不修改运行链路的前提下，观察 Agent 在较长时间运行中的状态更新、产物增长、去重、限流和降级行为。

本阶段暂不扩展新故障域，也不改变 detector、MonitorLoop、policy、AutoRecoveryRunner、remote watcher 或 report prompt。

## 2. R13 总目标

R13 需要验证以下长期稳定性目标：

- daemon 长期稳定运行；
- heartbeat 与 `project_status` 持续更新；
- `daemon.log` 无 Traceback；
- `seen_fingerprints` 正常持久化与读取；
- 不重复生成报告；
- alerts 不发生风暴；
- outputs 不异常膨胀；
- multi-event 每轮处理事件数不超过 `3`；
- auto_recover 每轮执行次数不超过 `1`；
- manual_escalation 不阻塞 monitor cycle；
- LLM 失败可安全降级，不导致 daemon 崩溃；
- remote tail 持续可用，能保留最新日志尾部。

## 3. 非目标

R13 不做以下事项：

- 不新增 detector；
- 不新增恢复动作；
- 不扩大权限；
- 不引入危险自动命令；
- 不修改 systemd unit；
- 不做压力测试；
- 不做破坏性注入；
- 不修改 detector、MonitorLoop、policy、AutoRecoveryRunner；
- 不修改 remote watcher 或 report prompt；
- 不改变 state/outputs 的结构或写入逻辑。

## 4. 稳定性指标

| 指标 | 观察方式 | PASS | FAIL |
| -- | ---- | ---- | ---- |
| daemon | `systemctl status`、`systemctl show` | 服务持续处于 active/running，主进程稳定 | 服务退出、反复重启、MainPID 异常变化 |
| heartbeat | 查看 `project_status.json` 中 heartbeat 字段或最近更新时间 | 持续刷新，时间戳符合观察窗口 | 长时间不更新或字段缺失 |
| project_status | `cat state/enterprise_demo_local/project_status.json` | 状态结构可读，状态与运行阶段一致 | JSON 损坏、状态长期卡死、关键字段缺失 |
| daemon.log | `tail -n 200 state/enterprise_demo_local/daemon.log` | 无 Traceback，无循环异常 | 出现 Traceback、未捕获异常或重复错误刷屏 |
| journal | `journalctl -u ... -n 200` | 无 systemd 重启循环，无异常退出 | 反复 restart、exit code 异常、systemd failure |
| events_detected | `daemon.log` 周期记录、summary 记录 | 无事件时保持 0，受控事件时数量符合预期 | benign 日志触发事件，或单轮超过上限 |
| reports | `find outputs/monitors -type f | wc -l` 与文件时间戳 | 只在受控事件后产生合理数量报告 | 无事件时持续新增，或同一事件重复生成 |
| alerts | `find outputs/alerts -type f | wc -l` 与文件时间戳 | 只在受控事件后产生合理数量 alert | 无事件时持续新增，或 alert 风暴 |
| outputs | `du -sh outputs` | 增长与测试事件数量匹配 | 无事件时异常增长或短时大量膨胀 |
| state | `du -sh state` 与状态文件可读性 | 小幅稳定增长，JSON/state 文件可读 | state 异常膨胀、文件损坏 |
| seen_fingerprints | 查看 persistent seen 文件或相关状态记录 | 已处理事件可去重，文件可读 | 文件损坏、已处理事件重复触发、误判跳过新事件 |
| multi-event | 受控 multi-event 日志与 daemon 周期记录 | 每轮最多处理 `3` 个 event，独立记录 | 单轮超过 `3` 个，或事件互相覆盖 |
| auto_recover | recovery report、daemon.log、policy 记录 | 每轮最多 `1` 次 auto_recover | 单轮多次自动恢复，或越权恢复 |
| CPU/RSS | `ps`、`systemctl status`、后续只读采样 | CPU/RSS 在观察窗口内稳定，无持续爬升 | CPU 长期异常占用，RSS 持续增长不可回落 |

## 5. R13 分阶段计划

### R13-1

测试设计与验收标准。当前阶段仅新增本文档，明确后续长期稳定性测试的观察指标、阶段计划、验收标准、风险和硬化方向。

本阶段不运行长时间测试，不执行 smoke 测试，不修改 Agent 运行逻辑。

### R13-2

30 分钟只读观察：

- 观察 daemon 是否持续运行；
- 观察 `daemon.log` 是否存在 Traceback 或异常刷屏；
- 观察 `project_status` 与 heartbeat 是否持续更新；
- 观察 `outputs/alerts` 是否在无事件情况下异常增长；
- 记录 CPU/RSS 是否稳定。

### R13-3

30 分钟 benign 日志测试：

- 注入或准备不包含 detector 关键词的 benign 日志；
- 验证 detector 不误报；
- 验证不会生成 report/alert；
- 验证 `seen_fingerprints` 不被异常污染；
- 验证 daemon 与 `project_status` 正常。

### R13-4

受控单事件测试：

- 使用单一受控事件验证事件识别；
- 验证同一 fingerprint 不重复处理；
- 验证 persistent `seen_fingerprints` 在 daemon 周期或重启后仍可去重；
- 验证 auto_recover 每轮限流；
- 验证 manual_escalation 不阻塞后续周期。

### R13-5

受控 multi-event 测试：

- 使用 `process_crash + container_k8s` 组合事件；
- 验证两个事件独立识别、独立处理、独立 report/alert；
- 验证每轮事件上限不超过 `3`；
- 验证每轮 auto_recover 不超过 `1`；
- 验证 remote tail 在多事件窗口中持续保留最新日志尾部。

### R13-6

总结与硬化建议：

- 汇总长期运行结果；
- 给出 `daemon.log` 轮转建议；
- 给出 alert 限流建议；
- 给出 state compact 建议；
- 给出 health check 增强建议；
- 明确是否建议进入下一阶段实现硬化。

## 6. R13-2 观察命令草案

以下命令仅作为 R13-2 只读观察草案，本阶段不执行长测：

```bash
systemctl status agentic-monitor@enterprise_demo_local.service --no-pager -l
systemctl show agentic-monitor@enterprise_demo_local.service -p ActiveState -p SubState -p MainPID -p ExecMainStatus
journalctl -u agentic-monitor@enterprise_demo_local.service -n 200 --no-pager
tail -n 200 state/enterprise_demo_local/daemon.log
cat state/enterprise_demo_local/project_status.json
du -sh outputs state acceptance_artifacts 2>/dev/null || true
find outputs/monitors -type f | wc -l
find outputs/alerts -type f | wc -l
```

执行 R13-2 时，这些命令应保持只读，不应启动、停止或重启 systemd 服务。

## 7. R13-2 验收标准

PASS：

- 服务持续运行；
- 无 Traceback；
- heartbeat 正常；
- `project_status` 正常；
- 无危险自动操作；
- `outputs/alerts` 无异常增长；
- CPU/RSS 正常；
- 无报告风暴。

PARTIAL：

- 存在增长风险或 warning，但 daemon 仍稳定；
- LLM 失败但成功降级；
- notification 或 summary 有非致命失败，且 monitor cycle 未阻塞。

FAIL：

- daemon 崩溃；
- systemd 重启循环；
- 自动恢复越权；
- alert/report 风暴；
- outputs/state 异常膨胀；
- `seen_fingerprints` 损坏；
- benign 日志触发误报并持续生成产物。

## 8. 风险分析

- alert 风暴：manual_escalation 或 notification 链路在重复事件上持续生成 alert，导致 alerts 目录快速增长。
- report 风暴：同一 fingerprint 未正确去重，或 summary/report 触发条件异常，导致 reports 持续生成。
- state 膨胀：长期运行中状态文件持续追加或重复记录，造成 state 目录不可控增长。
- `daemon.log` 膨胀：daemon 周期日志、warning 或异常信息过多，缺少轮转时会持续占用磁盘。
- outputs 膨胀：reports、post-notification reports、cycle summaries 和 alerts 长期累积，缺少保留策略。
- LLM 失败：LLM 调用超时、异常或返回不可用内容时，需要安全降级为本地模板或非阻塞失败。
- remote tail 截断：远程日志 tail 如果再次截断最新尾部，可能导致 detector 漏报或 multi-event 不完整。
- `seen_fingerprints` 误判：fingerprint 冲突可能跳过新事件，fingerprint 未持久化可能重复处理旧事件。
- multi-event 重复处理：同一窗口内多个事件如果 evidence 或 fingerprint 混淆，可能导致重复 report 或事件覆盖。
- auto_recover 连续触发：连续周期识别同类可恢复事件时，可能造成过于频繁的自动恢复。
- notification 失败：通知归档或发送失败不能阻塞 `_handle_event()` 或 daemon 周期。
- summary 失败：周期汇总生成失败应保持尽力而为，不能导致 daemon 崩溃。

## 9. 后续硬化方向

后续可考虑但本阶段不实现：

- `daemon.log` 轮转；
- outputs 保留策略；
- `seen_fingerprints` compact；
- alert/report rate limit；
- health check 增强；
- 长期运行 summary；
- LLM fallback 状态记录；
- stability report；
- CPU/RSS 趋势采样；
- state 文件完整性检查。

## 10. 结论

R13-1 仅完成长期运行稳定性测试规划，明确后续观察指标、分阶段计划、R13-2 命令草案、验收标准、风险分析与硬化方向。

本阶段不修改运行逻辑，不运行长时间测试，不执行 smoke 测试，不改变 detector、MonitorLoop、policy、AutoRecoveryRunner、remote watcher、report prompt、state 或 outputs。

完成 R13-1 后，后续可进入 R13-2，开展 30 分钟只读观察。
