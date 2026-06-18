# R14 Agent 结构硬化规划

## 1. 背景

R13 长期稳定性验证已基本完成，daemon、单事件、多事件、report/alert 链路均已通过验证。当前系统在 R13 验证范围内已经满足核心稳定性要求：daemon 持续运行，heartbeat 与 `project_status` 正常更新，benign 日志不误报，单事件和 multi-event 能独立处理，report evidence 与当前事件绑定，alerts 指向对应 report，未发现 report/alert 风暴或 `auto_recover` 越权触发。

R13 暴露并修复了两个关键问题：

- 同 event_type 多实例被旧 fingerprint 吞掉；
- multi-event report 复用旧 evidence。

这两个问题本质上属于长期运行治理问题，而不是单纯的功能缺口。它们说明 Agent 在长期运行、历史状态累积、多事件窗口和 report 上下文复用场景下，需要更强的结构约束、状态可观测性和回归保护。

因此，R14 的重点是结构硬化，而非功能扩展。本阶段不新增故障域，不扩大自动恢复权限，不引入危险自动操作，也不改变 detector、MonitorLoop、policy 或 AutoRecoveryRunner 的核心职责边界。

## 2. R14 目标

R14 以长期运行可治理、可观测、可回归为目标，重点覆盖以下方向：

- `daemon.log` 轮转：避免长期运行日志无限增长，形成可审计、可配置、低风险的轮转策略；
- `outputs/reports/alerts` 保留策略：限制历史产物长期累积，保留必要诊断证据；
- `seen_fingerprints` compact：降低 persistent seen 长期膨胀和误判风险；
- report/alert rate limit：抑制重复报告和通知风暴，但不得吞掉首次关键事件；
- health check 与 `project_status` 增强：让 daemon、LLM fallback、report/alert、seen 状态和最近错误更容易被只读观察；
- LLM fallback 状态记录：记录 LLM 失败、降级路径和最近 fallback 结果，避免静默降级；
- evidence scope 回归测试：确保 report 只使用当前事件 evidence，不复用旧事件上下文；
- multi-event 同类型多实例回归测试：确保同一 `event_type` 的不同实例可独立识别、独立 fingerprint、独立处理；
- 可观测性增强：增加状态汇总、计数器、最近事件摘要和风险提示，减少依赖人工翻阅长日志。

## 3. 非目标

R14 不做以下事项：

- 不新增故障域；
- 不扩大自动恢复权限；
- 不新增危险自动操作；
- 不执行危险自动命令；
- 不调整 systemd unit 或服务管理方式；
- 不做破坏性测试；
- 不通过真实故障注入扩大测试风险；
- 不改变 detector、MonitorLoop、policy、AutoRecoveryRunner 的核心运行逻辑；
- 不扩大 `auto_recover` 的触发条件、命令集合或执行权限。

## 4. R13 经验总结

R13 的主要经验如下：

- event lifecycle 必须保持独立性：同一观察窗口中的多个事件应拥有各自的 detection、fingerprint、selection、report 和 alert 生命周期。
- evidence scope 必须严格绑定当前事件：report 生成不能复用 session 级旧 evidence，也不能在 multi-event 场景中跨事件串线。
- persistent seen 存在长期运行风险：`seen_fingerprints` 能防止重复处理，但如果 fingerprint 粒度不足或状态长期膨胀，也可能跳过真实新事件。
- multi-event 同类型实例必须被显式保护：同一 `event_type` 下多个不同实例不能被旧实例覆盖，也不能因为历史 fingerprint 已 seen 而被误跳过。

这些经验应在 R14 中转化为结构硬化设计、只读状态增强和回归测试，而不是通过扩大自动恢复权限来掩盖问题。

## 5. R14 分阶段规划

### R14-1：设计与验收标准

新增 R14 结构硬化规划文档，明确目标、非目标、安全边界、阶段计划、验收指标、优先级和后续路线。

本阶段仅修改 docs，不修改运行逻辑，不执行 smoke，不新增故障域，不扩大自动恢复权限。

### R14-2：health/project_status 增强

增强只读状态汇总，让 `project_status` 能表达 Agent 当前健康度和最近关键状态。

建议覆盖：

- daemon heartbeat 与最近 cycle 时间；
- 最近 detector/report/alert 结果摘要；
- report/alert 计数和最近生成时间；
- LLM fallback 状态；
- persistent seen 文件大小、条目数和最近 compact 状态；
- 最近错误或 warning 摘要；
- health check PASS/PARTIAL/FAIL 风险提示。

R14-2 已开始实现 `project_status / health check` 增强，优先在 `project_status.json` 中追加兼容的 `runtime_health` 子对象，记录最近 cycle 开始/结束时间、cycle 耗时、events/reports/alerts 计数、daemon pid/uptime、LLM fallback 使用状态，以及 `ok/degraded` 健康状态和最近异常信息。

该实现保持旧字段兼容，不修改 detector、policy、AutoRecoveryRunner，不新增自动恢复逻辑，也不扩大 `auto_recover` 权限。

### R14-3：retention 与 log rotation 设计

设计 `daemon.log` 轮转与 `outputs/reports/alerts` 保留策略。优先提供 dry-run 和审计输出，先观察将被清理的对象，再决定是否启用实际清理。

建议覆盖：

- 按数量、时间或大小限制 report/alert 保留；
- 保护最近关键事件和最新 evidence；
- retention 操作记录审计日志；
- `daemon.log` 轮转后的可追溯性；
- 配置默认值保守，避免误删诊断证据。

R14-3 已开始 retention 与 log rotation 设计，新增 `docs/r14_retention_log_rotation_design.md`，并通过只读 inventory 采集 `outputs/monitors`、`outputs/alerts`、`state` 与 `daemon.log` 的文件数量、时间、大小和行数信息。

本阶段仅做文档设计、只读 inventory 和 dry-run 策略，不删除、不移动、不清空任何文件，不修改 `state/` 或 `outputs/` 中既有文件，不执行真实轮转。

### R14-4：report/alert rate limit

设计 report/alert 频率限制，防止重复事件造成产物风暴和通知风暴。

rate limit 必须满足：

- 不吞掉首次关键事件；
- 不影响不同 fingerprint 的真实新事件；
- 能记录被抑制事件的原因和计数；
- 对 report 与 alert 分别统计；
- 与 manual escalation 兼容，不扩大 `auto_recover` 权限。

R14-4 已开始实现最小运行时限流与 flood control，新增独立 tracker、fingerprint cooldown 和 per-cycle counters。该阶段不修改 detector、policy、AutoRecoveryRunner，不改变 report/alert 内容正确性，只控制重复事件和同轮产物风暴入口。

### R14-5：seen_fingerprints compact

设计 persistent `seen_fingerprints` compact 机制，降低长期运行状态膨胀与误判风险。

compact 必须满足：

- 默认 dry-run；
- compact 前可备份；
- compact 后可回滚；
- 保留近期关键事件；
- 保留必要字段用于审计；
- 不改变 fingerprint 生成语义；
- 不影响同类型多实例回归场景。

R14-5 采用 dry-run first。新增 compact 组件只读取指定 `project_status.json` 与可选 `events.jsonl`，生成 compact plan 和审计报告，默认 `dry_run=true`，不写回原文件，不修改真实 `state/`。

后续如果进入真实 compact，必须先备份目标 state，再执行 compact，再写审计日志，并保留回滚路径。真实 compact 不在 R14-5 执行。

### R14-6：验收总结

汇总 R14 结构硬化结果，确认每项设计或实现是否满足验收指标。

验收总结应包含：

- 变更范围；
- 测试结果；
- 是否只读或是否实际清理；
- 是否影响 report/alert 首次事件；
- 是否影响同类型多实例和 evidence scope 回归；
- 剩余风险；
- 是否建议进入 R15。

## 6. 安全边界

R14 必须遵守以下安全边界：

- 所有清理动作先 dry-run；
- retention 必须可审计，能说明清理对象、原因、时间和配置；
- compact 必须可回滚或先备份，避免损坏 persistent seen；
- rate limit 不得吞掉首次关键事件；
- rate limit 不得将不同 fingerprint 的真实新事件误判为重复；
- LLM fallback 记录只能增强可观测性，不能阻塞 monitor cycle；
- health check 只能汇总状态，不能触发危险操作；
- 所有变更必须通过 core tests；
- 涉及 event lifecycle、evidence scope、multi-event selection 的变更必须补充或更新回归测试；
- 不新增故障域，不扩大 `auto_recover` 权限，不执行 systemd 调整或破坏性测试。

## 7. 验收指标

| 项目 | 验收指标 | PASS 标准 |
| -- | -- | -- |
| log rotation | `daemon.log` 有明确轮转设计或实现边界 | 日志增长可控，轮转后仍可追溯关键周期，无 daemon 中断 |
| retention | reports/alerts 保留策略可配置、可审计 | dry-run 能列出候选清理对象，实际清理前有明确保护规则 |
| compact | `seen_fingerprints` compact 安全可控 | compact 前可备份或回滚，不改变 fingerprint 语义，不跳过真实新事件 |
| rate limit | report/alert 频率限制可解释 | 首次关键事件不被吞掉，重复抑制有记录，不影响不同 fingerprint 新事件 |
| health check | health 状态可只读观察 | 能汇总 daemon、recent cycle、report/alert、fallback、seen 状态和最近错误 |
| project_status | `project_status` 信息更完整 | 能表达长期运行风险、最近事件、最近产物和关键降级状态 |
| LLM fallback | fallback 状态有记录 | LLM 失败、降级路径、最近 fallback 时间和结果可见，daemon 不崩溃 |
| evidence scope regression | report evidence 绑定当前事件 | multi-event report 不复用旧 evidence，每个 report 可追溯本事件 evidence |
| multi-event regression | 同类型多实例可独立处理 | 同一 `event_type` 不同 fingerprint 实例不会被旧 fingerprint 吞掉 |

## 8. 优先级

P0：可观测性与状态汇总

优先增强 health check 与 `project_status`，因为结构硬化首先需要可见性。只有先知道 daemon、report/alert、LLM fallback、seen 状态和最近风险，后续 rate limit、retention、compact 才能被安全验证。

P1：report/alert rate limit

report/alert 风暴会直接影响长期运行成本、告警质量和 outputs 增长，因此应在可观测性增强后优先设计。但 rate limit 必须保护首次关键事件和不同 fingerprint 新事件。

P2：retention dry-run

outputs/reports/alerts 长期累积需要治理，但清理动作存在误删诊断证据的风险。优先实现 dry-run 和审计口径，再考虑实际清理。

P3：seen_fingerprints compact

`seen_fingerprints` 长期膨胀可能带来性能、可读性和误判风险。compact 风险高于普通 retention，必须在备份、回滚和回归测试明确后推进。

P4：log rotation

`daemon.log` 轮转是长期运行必要项，但相比 report/alert 风暴和 persistent seen 误判，风险更容易通过外部文件策略控制。因此放在后续阶段推进，仍需保证审计和追溯能力。

## 9. 后续路线

R14 是后续扩展的基础。完成 R14 后，Agent 应具备更稳定的长期运行结构、更清晰的只读状态、更安全的产物治理机制，以及覆盖 R13 关键问题的回归保护。

后续路线建议：

- R15：自动恢复策略优化，在不越权的前提下细化恢复策略、冷却时间、失败记录和人工接管边界；
- R16：新增/细化故障域，在已有结构硬化和回归保护基础上扩展 detector 覆盖面；
- R17：CI、Runbook、交付质量，完善持续验证、操作手册、发布检查和交付验收材料。

建议进入 R14-6：验收总结。
