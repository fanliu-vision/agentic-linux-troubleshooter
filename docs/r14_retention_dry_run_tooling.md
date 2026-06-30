# R14-3b Retention / Log Rotation Dry-Run 工具

## 目标

`scripts/r14_retention_dry_run.py` 将 R14 retention/log rotation 从设计推进到工具化 dry-run。它只做 inventory、artifact 分类、保护判定和候选计划，不执行删除、移动、清空、truncate 或真实轮转。

## 扫描范围

- `outputs/monitors/`
- `outputs/alerts/`
- `acceptance_artifacts/`
- `state/**/daemon.log`
- `state/**/daemon.log.*`
- `state/**/project_status.json`
- `state/**/events.jsonl`
- `state/**/alerts.jsonl`
- `state/**/recoveries.jsonl`

`state` 中的核心状态文件只进入保护清单，不进入删除候选。

## 默认策略

- reports 保留最近 30 天，并按项目保留最新 50 个 monitor 产物。
- alerts 保留最近 90 天，并按项目保留最新 200 个 alert 归档。
- acceptance artifacts 保留最近 30 天，并按 run prefix 保留最新 5 个运行目录。
- `daemon.log` 超过 50 MiB 时生成复制式轮转计划，但当前日志仍受保护。
- `daemon.log.*` 备份按项目保留最新 5 个。

## 保护规则

以下对象不会进入真实删除候选：

- alert JSONL 审计日志和 latest alert 指针；
- alert `report_paths` 指向的 monitor report；
- `remote_applied_fixes.json` 等恢复/回滚审计状态；
- manual escalation、operator required、gate blocked、forbidden 或 rollback failure 证据；
- 每个项目最新 N 个 monitor 产物；
- 每个项目最新 N 个 alert 归档；
- 每个 acceptance prefix 最新 N 个运行目录；
- 无时间戳的 acceptance 目录；
- 当前 `daemon.log`；
- `project_status.json`、`events.jsonl` 等 state 核心文件。

## 输出文件

默认输出目录：

```bash
acceptance_artifacts/r14_3b_retention_dry_run_<timestamp>/
```

输出文件：

- `retention_inventory.json`：全量扫描清单。
- `retention_plan.json`：本次策略配置和安全规则。
- `retention_candidates.jsonl`：候选对象，每行一个 JSON。
- `retention_protected.jsonl`：受保护对象，每行一个 JSON。
- `retention_summary.json`：汇总指标和轮转计划。
- `retention_dry_run_report.md`：中文人工审阅报告。

## 运行命令

```bash
.venv/bin/python scripts/r14_retention_dry_run.py
```

常用参数：

```bash
.venv/bin/python scripts/r14_retention_dry_run.py \
  --reports-retention-days 30 \
  --keep-latest-reports-per-project 50 \
  --alerts-retention-days 90 \
  --keep-latest-alerts-per-project 200 \
  --daemon-log-max-size-bytes 52428800
```

## 验收边界

PASS 条件：

- 能生成 inventory、candidate、protected、summary 和中文 Markdown 报告；
- 每个 candidate 都有原因、动作、风险等级和预计释放字节数；
- 每个 protected 对象都有保护原因；
- `daemon.log` 只出现 dry-run 轮转计划，不执行 truncate；
- pytest 临时目录测试覆盖 fake `outputs/state/acceptance_artifacts`；
- 真实 repo dry-run 只新增本次 `acceptance_artifacts/r14_3b_retention_dry_run_*` 输出目录。
