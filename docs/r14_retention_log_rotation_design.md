# R14-3 Retention 与 Log Rotation 设计

## 1. 背景

R13 已验证 daemon、单事件、多事件、report/alert 链路长期运行稳定，R14-2 已增强 `project_status.json` 中的 `runtime_health` 可观测性。进入 R14-3 后，重点从运行状态可见性扩展到产物增长治理。

长期运行下需要 retention / log rotation 的原因如下：

- daemon 会持续运行并持续写入 `daemon.log`；
- reports 会随事件处理、post-notification report 和 cycle summary 持续增长；
- alerts 会随 notification 归档持续增长；
- `seen_fingerprints` 会随事件去重状态持续增长；
- `acceptance_artifacts/` 会随验收记录、inventory 和阶段产物持续增长；
- 这些对象都可能包含诊断证据，不能直接删除或截断。

R14-3 只做文档设计、只读 inventory 和 dry-run 策略设计，不做真实删除、不做真实轮转、不移动文件、不清空文件。后续任何真实清理动作都必须先生成候选清单、审计记录和风险说明，并由用户确认后在单独阶段执行。

## 2. 当前目录与文件

当前需要纳入治理视野的目录与文件包括：

- `outputs/monitors/`：事件级 report、post-notification report、cycle summary report 等监控报告产物。
- `outputs/alerts/`：notification 归档文件，可能包含 owner、event、action、report_paths 等告警证据。
- `state/<project_id>/daemon.log`：daemon 运行日志，长期运行时会持续增长。
- `state/<project_id>/project_status.json`：项目状态与 `runtime_health` 汇总，不应轮转或删除。
- `state/<project_id>/seen_fingerprints`：persistent seen 的逻辑状态对象；当前若存储在 `project_status.json` 中，也应按同样安全边界处理。
- `state/<project_id>/events.jsonl`：事件历史记录，只做 future design，不在 R14-3 清理。
- `acceptance_artifacts/`：阶段验收、inventory、观察记录等产物，不属于 Agent 运行时核心产物，但同样需要后续保留策略。

R14-3 inventory 已按只读方式采集文件清单，输出到 `acceptance_artifacts/r14_3_retention_inventory_<timestamp>/`。该 inventory 只读取 `outputs/`、`state/` 和 `acceptance_artifacts/` 元数据，不修改这些目录中的既有文件。

## 3. 保留策略设计

### reports

reports 保留策略建议按项目维度执行，避免不同项目产物互相影响。

建议规则：

- 按项目保留：每个 `project_id` 单独计算候选 report；
- 按时间保留：优先保留最近 N 天 report；
- 按数量保留：优先保留每个项目最新 N 个 report；
- 先 dry-run：默认只输出候选清单，不删除；
- 生成待清理清单：包括路径、mtime、大小、所属项目、候选原因、风险等级；
- 保护 cycle summary：最近的 cycle summary report 默认保留；
- 保护 alert 关联 report：任何被 alert `report_paths` 指向的 report 不进入清理候选；
- 保护当前 cycle report：当前 daemon cycle 正在使用或刚生成的 report 不进入清理候选。

候选原因示例：

- `older_than_retention_days`；
- `exceeds_project_report_count_limit`；
- `duplicate_post_notification_report_candidate`；
- `unreferenced_report_candidate`。

### alerts

alerts 保留策略需要比普通 report 更保守，因为 alert 是人工响应和通知审计的重要证据。

建议规则：

- 保留最近 N 天 alert；
- 保留最近 N 条 alert；
- 重要告警可长期保留；
- 不得删除未关联 report 的 alert；
- 不得删除最近 N 个 alert；
- 不得删除处于人工升级链路中的 alert；
- alert 清理候选必须记录其关联 report 是否仍存在。

候选原因示例：

- `older_than_alert_retention_days`；
- `exceeds_alert_count_limit`；
- `low_risk_archived_alert_candidate`。

风险等级建议：

- `low`：历史 alert，关联 report 存在，超过时间和数量保留阈值；
- `medium`：历史 alert，关联 report 存在，但包含 manual escalation；
- `high`：未关联 report、关联 report 缺失、最新 alert、关键故障 alert。`high` 不允许进入真实删除动作。

### daemon.log

`daemon.log` 建议采用 size-based rotation，但 R14-3 不执行真实轮转。

建议规则：

- size-based rotation：当 `daemon.log` 超过配置大小后才生成候选轮转计划；
- 保留最近 N 个备份；
- 轮转前复制当前日志到带时间戳备份文件；
- 轮转后继续写当前 `daemon.log`；
- 不直接清空正在使用的日志，除非确认 daemon 写入方式支持安全 reopen 或 copytruncate；
- 不通过 systemd restart 实现轮转；
- 轮转计划必须记录当前大小、行数、候选备份名、保留备份数量和风险说明。

安全实现建议：

- R14-3 只记录 `daemon.log` 当前大小和行数；
- 后续实现前先确认 `DaemonLogger` 的文件打开策略；
- 如果 daemon 每次写入都重新打开文件，可考虑复制归档后安全截断，但仍需单独阶段验证；
- 如果 daemon 持有长期文件句柄，应避免 rename 后让 daemon 继续写旧 inode；
- 默认优先选择不影响 daemon 的复制式归档设计，再由后续阶段决定是否启用实际截断。

### state

`state/` 不直接删除。

R14-3 对 state 的设计边界如下：

- `project_status.json` 不轮转、不删除；
- `seen_fingerprints` compact 放到 R14-5；
- `events.jsonl` 只做 future design；
- `daemon.log` 可进入 log rotation 设计，但不在 R14-3 真实轮转；
- state 清理不得影响 daemon 启动、heartbeat、runtime_health、persistent seen 或事件去重。

## 4. dry-run 机制

Retention / log rotation 的默认入口必须是 dry-run。

dry-run 必须满足：

- 不删除；
- 不移动；
- 不清空；
- 不 truncate；
- 只输出候选清单；
- 输出每个候选对象的原因；
- 输出预计释放空间；
- 输出风险等级；
- 输出保留规则命中情况；
- 输出会被保护而不清理的对象及原因；
- 用户确认后才允许未来阶段执行真实动作。

dry-run 输出建议字段：

| 字段 | 说明 |
| -- | -- |
| `path` | 候选文件路径 |
| `artifact_type` | `report`、`alert`、`daemon_log_backup` 等 |
| `project_id` | 所属项目 |
| `size_bytes` | 文件大小 |
| `mtime` | 最近修改时间 |
| `reason` | 入选候选原因 |
| `risk_level` | `low`、`medium`、`high` |
| `protected` | 是否受保护 |
| `protected_reason` | 受保护原因 |
| `estimated_reclaim_bytes` | 预计释放空间 |

dry-run 结果应能回答三个问题：

- 哪些文件理论上可以清理；
- 为什么它们可以清理；
- 为什么某些看似旧的文件仍然必须保留。

## 5. 审计记录

后续 retention 工具应生成以下审计文件：

- `retention_plan_<timestamp>.json`：本次策略配置、阈值、项目范围、dry-run 输入目录、执行模式。
- `retention_dry_run_<timestamp>.md`：面向人工审阅的候选清单、预计释放空间、风险说明和保护规则摘要。
- `retention_action_log_<timestamp>.jsonl`：真实执行阶段的逐文件动作日志；R14-3 不生成真实 action log，只定义格式。

`retention_plan_<timestamp>.json` 建议包含：

- `mode`: `dry_run` 或 `apply`；
- `created_at`；
- `project_id`；
- `retention_days`；
- `keep_latest_reports`；
- `keep_latest_alerts`；
- `daemon_log_max_size_bytes`；
- `daemon_log_keep_backups`；
- `safety_rules`；
- `inventory_dir`。

`retention_action_log_<timestamp>.jsonl` 每行建议包含：

- `created_at`；
- `action`: `candidate`、`protected`、`delete`、`rotate_copy`、`skip`；
- `path`；
- `reason`；
- `risk_level`；
- `size_bytes`；
- `result`；
- `error`。

## 6. 安全边界

R14-3 以及后续 retention/log rotation 必须遵守以下安全边界：

- 不删除 state；
- 不删除当前 cycle 正在使用的 report；
- 不删除 alert 指向的 report；
- 不删除未关联 report 的 alert；
- 不删除最新 N 个 report；
- 不删除最新 N 个 alert；
- 不直接 truncate `daemon.log`；
- 不影响 daemon 运行；
- 不通过 sudo、systemd restart 或服务重启完成清理；
- 不修改 detector、MonitorLoop 运行逻辑、policy、AutoRecoveryRunner；
- 不新增自动恢复逻辑；
- 不扩大 `auto_recover` 权限；
- 所有真实清理必须单独阶段执行；
- 所有真实清理前必须有 dry-run、审计记录和用户确认。

## 7. R14-4 / R14-5 衔接

R14-3 只完成 retention / log rotation 的设计和 inventory。

后续衔接如下：

- R14-4：做 report / alert rate limit，优先减少新增产物风暴，避免 retention 只是在事后被动清理；
- R14-5：做 `seen_fingerprints` compact，处理 persistent seen 长期增长问题；
- retention 执行阶段放到后续，不在 R14-3 做；
- daemon.log 真实轮转也应放到后续单独阶段，并在确认写入安全性后执行。

## 8. 验收标准

R14-3 PASS 标准：

- 完成 `docs/r14_retention_log_rotation_design.md`；
- 更新 `docs/r14_structural_hardening_plan.md`；
- 完成只读 inventory；
- 没有删除文件；
- 没有移动文件；
- 没有清空文件；
- 没有修改 `state/` 或 `outputs/` 中既有文件；
- 没有修改 detector；
- 没有修改 MonitorLoop 运行逻辑；
- 没有修改 policy；
- 没有修改 AutoRecoveryRunner；
- 没有新增自动恢复逻辑；
- core tests 通过；
- git status 干净或仅 docs 变更。

R14-3 不验收真实删除、真实轮转、真实 compact 或 rate limit 行为。
