# Stage 6E-2 验收总结

## 项目目标

本项目构建面向企业项目的 Linux 故障排查 Agent 工作流。目标运行闭环包括：

- 监控 Linux 与企业项目日志；
- 从日志中识别结构化故障事件；
- 根据策略决定自动恢复、人工升级或仅生成报告；
- 仅在明确允许的场景中执行受控自动恢复；
- 对高风险或外部依赖类故障进行人工升级；
- 生成通知、告警归档、事件报告、通知后报告和周期汇总；
- 支持长期运行的 systemd daemon 监管。

## 已完成阶段

已完成并纳入当前验收记录的内容包括：

- Stage 6E-2 systemd 验收；
- pytest 核心测试基线；
- 故障域回归日志；
- 故障域矩阵文档；
- R3 到 R6 的企业故障域扩展；
- R9 代表性企业故障域 live report smoke 验证；
- R10 同窗口多事件设计、实现、修复与 live smoke 验证。

## systemd live 已验证能力

Stage 6E-2 live 验收覆盖了原有 monitor 与 recovery 工作流。以下能力已通过 systemd 监管下的 live 验证：

- `network_port` 受控自动恢复；
- `gpu_oom` 受控自动恢复；
- `disk_full` 人工升级；
- `python_env` 人工升级；
- 告警归档生成；
- 事件报告生成；
- 通知后报告生成；
- 周期汇总生成；
- systemd stop/start 行为；
- systemd 进程被 `kill -9` 后自动恢复服务；
- `process_crash` live report smoke 通过；
- `container_k8s` isolated live report smoke 通过；
- R10-4h multi-event live smoke 通过。

这些 live 检查不属于默认 pytest core baseline。

## R9 live report smoke 结果

R9 验证了新增企业故障域可以复用既有 report 与 alert 链路：

- `process_crash`：live smoke 通过。
  - 报告：`outputs/monitors/enterprise_demo_local/f09ee00e/event_20260616_153917_process_crash_manual_escalation_final_llm_report.md`
  - 告警：`outputs/alerts/enterprise_demo_local_alerts/20260616_153917_process_crash_manual_escalation_bc48d2be.md`
- `container_k8s`：isolated live smoke 通过。
  - 报告：`outputs/monitors/enterprise_demo_local/f09ee00e/event_20260616_155317_container_k8s_manual_escalation_final_llm_report.md`
  - 通知后报告：`outputs/monitors/enterprise_demo_local/f09ee00e/event_20260616_155345_container_k8s_post_notification_final_llm_report.md`
  - 告警：`outputs/alerts/enterprise_demo_local_alerts/20260616_155317_container_k8s_manual_escalation_e7b7c489.md`

R9 combined smoke 的结论是部分通过：`process_crash` 生成了 report/alert，`container_k8s` 进入了 `process_crash` 的 raw evidence，没有生成独立事件报告。随后单独执行的 `container_k8s` isolated smoke 已通过。

该部分通过结果被记录为当时的同窗口多事件限制，不是 `container_k8s` 独立识别能力失败。

## R10 multi-event 验收结果

R10 解决了同一日志窗口内多个故障只生成一个主事件的问题。

关键阶段如下：

- R10-2：`ErrorEventDetector` 新增 `detect_all()`，保留兼容 API `detect()`；
- R10-3b：`MonitorLoop` 接入 `detect_all()`，支持一轮处理多个 event；
- R10-3b：每轮最多处理 `3` 个 event；
- R10-3b：每轮最多执行 `1` 个 `auto_recover`；
- R10-4f：确认 live daemon 未识别当前 smoke 的根因是远程日志 tail 输出被前缀截断；
- R10-4g：修复 `read_remote_log_tail()` 的日志 tail 截断策略，截断时保留输出尾部；
- R10-4h：重启 daemon 后重新运行 clean multi-event live smoke，结果通过。

R10-4h 验证结果：

- `process_crash` 与 `container_k8s` 在同一日志窗口内均被识别；
- 两个 event 均生成了独立 report/alert；
- daemon 稳定；
- 未发现危险自动操作；
- daemon log 已验证 `events_detected=2`；
- 远程日志 tail 尾部保留修复已在 live daemon 中生效。

R10-4h 使用的 clean smoke 汇总：

```text
acceptance_artifacts/multi_event_live_smoke_r10_clean_20260617_105036/R10_4D_CLEAN_MULTI_EVENT_LIVE_SMOKE_SUMMARY.md
```

R10-4h 运行日志：

```text
acceptance_artifacts/r10_run_logs/r10_4h_clean_multi_event_live_smoke_20260617_105036.log
```

## 回归已验证故障域

以下故障域已通过确定性 regression fixture 与 pytest 覆盖：

- `network_port`
- `gpu_oom`
- `disk_full`
- `python_env`
- `slurm`
- `process_kill`
- `permission_denied`
- `process_crash`
- `host_resource`
- `network_connectivity`
- `dependency_service`
- `config_error`
- `auth_cert`
- `container_k8s`
- benign/info normal logs

回归覆盖表示 detector 与 policy 边界已通过 fixture 检查，不等价于每个故障域都完成了 systemd live report smoke。

当前代表性企业故障域 live 验证状态：

- `process_crash`：R9 isolated/live smoke 通过，R10-4h multi-event live smoke 通过；
- `container_k8s`：R9 isolated live smoke 通过，R10-4h multi-event live smoke 通过。

## 策略与安全边界

恢复策略保持较小的自动动作面：

- `network_port` 仅在项目明确允许时通过受控 `fix-network-1` 自动恢复；
- `gpu_oom` 仅在项目明确允许时通过受控 `fix-gpu-1` 自动恢复；
- 其他企业故障域默认走 `manual_escalation` 或 `report_only`；
- multi-event 模式下每轮最多执行 `1` 个 `auto_recover`；
- 未新增任何危险自动恢复动作。

Agent 不会自动执行：

- `kill`
- `rm`
- 任意 `pip install`
- `systemctl restart`
- `kubectl delete`
- `kubectl restart`
- `kubectl apply`

R10 未突破现有 policy 边界，也未修改 `AutoRecoveryRunner` 的恢复动作集合。

## 报告生成说明

每个 event 可能生成多份报告：

- 人工升级报告；
- 通知后报告；
- 周期汇总报告。

这不属于重复生成，而是同一事件在不同处理阶段的正常产物。多次 smoke 的结果会保留在同一个 `outputs/` 目录中。判断是否重复或是否属于当前 smoke，需要结合：

- smoke id；
- `event_type`；
- `fingerprint`；
- report/alert 时间戳；
- raw evidence 中的故障标识。

## 当前已知说明

R10-4f 曾发现远程日志 tail 的前缀截断会导致最新故障行被截掉。R10-4g 已将日志 tail 场景改为保留输出尾部。普通远程只读命令仍保持原有默认截断行为。

R10-4h 已验证修复生效：同窗口 `process_crash` 与 `container_k8s` 均可生成独立 report/alert。

## 测试命令

运行 core pytest baseline：

```bash
scripts/run_core_tests.sh
```

运行 fault-domain regression：

```bash
.venv/bin/python -m pytest tests/test_fault_domain_regression.py -q
```

运行 R10 关键测试：

```bash
.venv/bin/python -m pytest tests/test_remote_log_tail_truncation.py -q
.venv/bin/python -m pytest tests/test_multi_event_detector.py -q
.venv/bin/python -m pytest tests/test_stage6e_monitor_loop_multi_event.py -q
```

## core baseline 之外的内容

以下内容仍不属于默认 pytest core baseline：

- D1-mini；
- D2；
- systemd lifecycle acceptance；
- long-running daemon/watch tests；
- live smoke。

## R10 收尾结论

R10 已完成同窗口多事件设计、detector API、MonitorLoop 接入、远程 tail 输入修复和 live smoke 验证。

当前可以将 R10 标记为完成。
