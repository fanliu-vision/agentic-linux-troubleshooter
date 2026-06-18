# R14 结构硬化阶段验收报告

## 1. 背景

R14 承接 R13 长期稳定性验证结果，目标是结构硬化，而不是新增故障域或扩大自动恢复权限。

R13 已验证 daemon、单事件、多事件、report/alert 链路稳定，并修复了同 `event_type` 多实例被旧 fingerprint 吞掉、multi-event report 复用旧 evidence 两类长期运行治理问题。R14 围绕这些经验，增强状态可观测性、产物增长治理设计、运行时限流、persistent seen dry-run compact 与阶段验收材料。

R14 全阶段遵守以下原则：不新增故障域，不扩大 `auto_recover` 权限，不新增危险自动恢复，不修改 detector、policy、AutoRecoveryRunner 的职责边界，不通过真实删除或破坏性测试验证结构硬化能力。

## 2. R14 分阶段结果

| 阶段 | 内容 | 结果 | 说明 |
| -- | -- | -- | -- |
| R14-1 | 结构硬化规划 | PASS | 新增结构硬化规划，明确目标、非目标、安全边界、验收指标、优先级和后续路线 |
| R14-2 | project_status / runtime_health 增强 | PASS | `project_status.json` 增加兼容的 `runtime_health` 子对象，记录 cycle、daemon、error、fallback、report/alert 状态 |
| R14-3 | retention / log rotation 设计 | PASS | 新增 retention 与 log rotation 设计，完成只读 inventory 和 dry-run 策略，不真实删除或轮转 |
| R14-4 | report / alert rate limit | PASS | 新增运行时 rate limit tracker、per fingerprint cooldown、per-cycle report/alert budget 和 flood control |
| R14-5 | seen_fingerprints compact dry-run | PASS | 新增 compact dry-run 组件，生成 compact plan 与审计报告，不写回真实 state |
| R14-6 | 阶段验收总结 | PASS | 形成本报告，确认 R14 可标记完成并建议进入 R15 |

## 3. 已完成能力

R14 已完成以下结构硬化能力：

- `runtime_health` 状态增强；
- last cycle 开始时间、结束时间、耗时、events/reports/alerts 统计；
- daemon pid / uptime 记录；
- last error / exception 信息记录；
- LLM fallback 使用状态记录；
- report rate limit；
- alert rate limit；
- per fingerprint cooldown；
- per-cycle report / alert budget；
- flood control；
- `seen_fingerprints` compact dry-run；
- compact plan 的 keep/drop 统计、风险说明和审计报告；
- retention / log rotation dry-run 设计；
- report/alert/state/daemon.log 治理安全边界文档化。

## 4. 安全边界

R14 保持以下安全边界：

- 未新增危险自动恢复；
- 未扩大 `auto_recover`；
- 未修改 detector；
- 未修改 policy；
- 未修改 AutoRecoveryRunner；
- 未新增故障域；
- 未新增自动恢复逻辑；
- compact 默认 dry-run；
- retention 仍是设计阶段；
- 不真实删除 `state/` 或 `outputs/`；
- 不真实移动、清空或 truncate 运行产物；
- rate limit 不影响首次关键事件；
- rate limit 不修改 report 正文生成逻辑；
- alert 限流不修改已发送 alert 的内容格式；
- 所有真实 compact、retention、log rotation 必须在后续单独阶段先备份、再执行、再审计。

## 5. 测试结果

R14-6 收尾前重新运行以下测试，结果均为 PASS：

| 测试 | 结果 | 说明 |
| -- | -- | -- |
| core tests | PASS | `scripts/run_core_tests.sh` 输出 `CORE TEST BASELINE PASSED` |
| rate limit tests | PASS | `tests/test_rate_limit_tracker.py` 通过 |
| notification tests | PASS | `tests/test_stage6d_notification.py` 通过 |
| multi-event monitor tests | PASS | `tests/test_stage6e_monitor_loop_multi_event.py` 通过 |
| seen_fingerprints compactor tests | PASS | `tests/test_seen_fingerprints_compactor.py` 通过 |
| state_store tests | PASS | `tests/test_stage6e_state_store.py` 通过 |
| fault regression tests | PASS | `tests/test_fault_domain_regression.py` 通过 |

## 6. 剩余风险

R14 完成后仍保留以下风险和后续工作：

- retention 仍未真实执行；
- `daemon.log` rotation 仍未真实实现；
- `seen_fingerprints` compact 仍是 dry-run；
- rate limit 后续需要 live observation，确认实际 daemon 长时间运行下不会误伤关键事件；
- R15 自动恢复策略优化前仍需更多安全边界；
- 更复杂三事件窗口未覆盖，尤其是多故障域、多同类型实例和不同严重级别混合窗口；
- retention、compact、log rotation 的真实执行仍需备份、审计和回滚机制；
- flood control 的默认阈值后续可能需要项目级配置化。

## 7. 后续建议

建议进入：

```text
R15：自动恢复策略安全分层与 guarded auto_recover 设计
```

R15 应先做策略设计，不直接扩大恢复动作。建议将自动恢复策略分层为：

- `diagnose_only`；
- `manual_escalation`；
- `safe_auto_recover`；
- `guarded_auto_recover`；
- `disabled`。

R15 的重点应是明确每一层的触发条件、权限边界、冷却时间、失败记录、人工接管条件、回滚要求和审计格式。只有在策略安全边界明确、测试覆盖充分、人工确认路径清晰之后，才考虑扩大具体恢复动作。

## 8. R14 最终结论

R14 结构硬化阶段完成。Agent 已具备更强的长期运行治理能力，可以进入 R15 自动恢复策略安全分层阶段。
