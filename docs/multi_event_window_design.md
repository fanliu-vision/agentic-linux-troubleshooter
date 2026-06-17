# 同窗口多事件设计

## 当前行为回顾

R9 之前的 monitor 与 detector 流程通常在一个日志窗口内只生成一个主事件。

当多个故障在同一个窗口中出现时，次要故障可能被保留在主事件的 raw evidence 中，而不是成为独立事件。

R9 combined smoke 中曾观察到该现象：`container_k8s` 证据被包含在 `process_crash` 的 raw evidence 中。R9 isolated smoke 同时证明，`container_k8s` 单独出现时可以正常生成 report 和 alert。

因此，R9 的限制不是 `container_k8s` 故障域本身不可识别，而是同一窗口内缺少事件拆分能力。

## 为什么需要 multi-event

企业日志中多个故障经常在短时间内同时出现。

服务崩溃可能伴随 Kubernetes `BackOff`、依赖异常、资源异常、配置异常等问题。如果只保留一个主事件，会降低排障准确性，也容易掩盖故障范围。

独立 event/report 可以让每个故障域拥有自己的分类、证据、fingerprint、policy 决策和面向负责人的说明。

## 设计目标

一个日志窗口应支持多个 event。

每个 event 应独立拥有：

- `event_type`
- `fingerprint`
- raw evidence
- report

同时必须保持既有单事件行为兼容。仍使用 `detect()` 的旧调用方不需要修改，并继续接收兼容的主事件结果；需要多事件能力的调用方显式使用 `detect_all()`。

## 安全边界

multi-event 路径必须在 live monitor 中保持硬限制：

- 每轮最多处理 `3` 个 event；
- 每轮最多执行 `1` 个 `auto_recover`；
- 允许多个 `manual_escalation` event，但需要控制 report/alert 风暴；
- 不自动执行 `kill`、`rm`、任意 `pip install`、`systemctl`、`kubectl` 等危险操作；
- 保持既有 `network_port` 与 `gpu_oom` 自动恢复规则不变；
- 不扩大 policy 允许的自动操作边界。

R10 的目标是提升事件拆分和报告准确性，不是扩大自动恢复能力。

## 演进路线与完成状态

### R10-2：detector API

R10-2 已完成。

`ErrorEventDetector` 新增 `detect_all()`，并保留原有 `detect()` API。

`detect_all()` 仅负责识别候选 `ErrorEvent` 列表，不执行恢复、不发送通知、不写报告、不修改状态。

`detect()` 保持兼容，旧调用方不需要修改。

### R10-3b：MonitorLoop 接入

R10-3b 已完成。

`MonitorLoop` 在 detector 提供 `detect_all()` 时优先使用该 API，并在缺失时回退到 `detect()`。

每轮安全限制：

```text
MAX_EVENTS_PER_CYCLE = 3
MAX_AUTO_RECOVER_PER_CYCLE = 1
```

每个 event 独立处理：

- 独立 `fingerprint`；
- 独立 seen 判断；
- 独立 `_handle_event()`；
- 成功后才 mark seen；
- 单个 event 失败不阻止后续 event；
- 多个成功 event 可进入同一轮 cycle summary。

若同一轮出现多个 auto-recover 候选，只允许第一个候选继续执行恢复，其余候选跳过并记录警告。未新增任何自动恢复动作。

### R10-4：multi-event live smoke

R10-4 已完成，最终验证点为 R10-4h。

R10-4h clean multi-event live smoke 在同一日志窗口注入：

- `process_crash`
- `container_k8s`

验证结果：

- `process_crash` 生成当前 smoke 对应独立 report/alert；
- `container_k8s` 生成当前 smoke 对应独立 report/alert；
- daemon 稳定；
- 未发现危险自动操作；
- daemon log 已验证 `events_detected=2`；
- R10-4h 结论为通过。

R10-4h summary：

```text
acceptance_artifacts/multi_event_live_smoke_r10_clean_20260617_105036/R10_4D_CLEAN_MULTI_EVENT_LIVE_SMOKE_SUMMARY.md
```

### R10-5：验收文档更新

R10-5 用于记录 R10 的最终状态、live smoke 结果、安全边界和已知说明。

## fixture 草案

R10-1 新增了 multi-event fixture 草案：

```text
tests/fixtures/regression_logs/multi_event_process_crash_container_k8s.log
```

该 fixture 将 `process_crash` 与 `container_k8s` 日志放入同一个窗口，用于 multi-event 检测测试。

R10-1 同时新增 expected 草案：

```text
tests/fixtures/regression_logs/expected_multi_event_cases.json
```

该文件记录未来期望识别：

- `process_crash`
- `container_k8s`

该 expected 文件保持独立，不并入 `expected_cases.json`。

## remote tail 截断问题与修复

R10-4f 诊断发现 live daemon 未识别当前 clean smoke 的根因不在 detector，也不在 persistent_seen/fingerprint，而在 watcher 输入链路。

实际情况：

- runtime log 中存在当前 smoke；
- 当前 smoke 仍在 `tail_lines=200` 范围内；
- 未截断内容可被 `detect_all()` 识别为 `process_crash` 和 `container_k8s`；
- 默认 watcher 输出被 `[REMOTE_OUTPUT_TRUNCATED]` 截断在 benign filler 附近；
- 默认 watcher 输出不包含 `status=11/SEGV`、`core-dump`、`r10-combined-worker`、`r10-combined-api`、`OOMKilled`；
- 默认 watcher 输出 `detect_all()` 返回空列表。

根因是 `RemoteReadonlySSHExecutor` 默认保留输出前 `max_output_chars` 字符，导致 `tail -n 200` 的最新故障行被截掉。

R10-4g 已修复日志 tail 场景的截断策略：`read_remote_log_tail()` 在截断时保留输出尾部。普通远程只读命令仍保持默认前缀截断行为。

R10-4h 重启 daemon 后验证该修复生效。

## 报告生成说明

每个 event 可能生成多份报告：

- 人工升级报告；
- 通知后报告；
- 周期汇总报告。

这些报告对应不同处理阶段，不属于重复生成。

多次 smoke 的结果会保留在同一个 `outputs/` 目录中。判断是否为重复，需要结合：

- smoke id；
- `event_type`；
- `fingerprint`；
- 时间戳；
- raw evidence。

## 安全结论

R10 未新增危险自动恢复。

R10 未新增以下自动操作：

- `kill`
- `rm`
- 任意 `pip install`
- `systemctl`
- `kubectl`

R10 未突破现有 policy 边界。自动恢复仍受 `MAX_AUTO_RECOVER_PER_CYCLE = 1` 限制。

## R10 收尾结论

`detect_all()` 已实现，`MonitorLoop` 已接入，同窗口多事件 live smoke 已通过，remote tail 截断问题已修复。

R10 可以标记为完成。
