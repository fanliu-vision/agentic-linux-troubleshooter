# R18 下一阶段执行手册

## 目标

R18 把当前 Trace UI 产品雏形推进到真实 / 仿生产 dry-run 观察、venv + systemd 部署、角色权限审计和浏览器回归阶段。本阶段默认不扩大 `safe_auto_recover` 域，不默认开启生产 live apply。

## Phase 0 基线冻结

1. 确认工作区只包含本阶段预期改动：

```bash
git status --short
```

2. 运行核心基线：

```bash
scripts/run_core_tests.sh
```

3. 将结果记录到本阶段验收记录中。不要提交 `state/`、`outputs/`、`acceptance_artifacts/`、`.env`、token、私钥或真实日志原文。

完成标志：核心测试通过，已记录当前 commit/tag、README 状态、UI 已知限制和回滚点。

## Phase 1 真实日志只读 Shadow

真实日志必须先脱敏，原始日志只放外部运行目录。推荐输入条件：

- 每个项目至少 7 天日志，或至少 50MB 代表性日志；
- 标注样本覆盖正常日志、安全候选、高风险 manual、多事件和跨域噪声；
- 未标注样本用于检测量、manual escalation 量和跨域共现 baseline。

运行 evaluator：

```bash
.venv/bin/python scripts/r17_real_log_shadow_evaluate.py \
  --manifest /path/to/sanitized/manifest.json \
  --output-dir outputs/r18_real_log_shadow/staging_$(date +%Y%m%d_%H%M%S)
```

运行 R18 gate：

```bash
.venv/bin/python scripts/r18_real_log_shadow_gate.py \
  --summary outputs/r18_real_log_shadow/<run_dir>
```

默认 PASS 门槛：

- `false_positive_count=0`，或单独通过参数放宽并在报告说明例外；
- `false_negative_count=0`；
- `safe_swallowed_high_risk_count=0`；
- `manual_escalation_noise_count=0`；
- 至少 1 个带标注样本。

完成标志：形成 R18 real-log shadow report，列出 detector/policy 缺口，但不直接扩大自动恢复范围。

## Phase 2 Daemon Dry-Run 观察

从 `configs/projects.staging.example.yaml` 复制 staging profile 到本地私有配置，按真实 staging 环境替换 host、user、path 和日志路径。不要把私有配置、token、私钥或真实日志提交到 Git。

```bash
mkdir -p "$HOME/agentic-staging" state outputs/monitors outputs/alerts
cp configs/projects.staging.example.yaml "$HOME/agentic-staging/projects.staging.local.yaml"
chmod 600 "$HOME/agentic-staging/projects.staging.local.yaml"
```

私有配置必须保持：

```yaml
auto_recovery_dry_run: true
require_human_approval_for_live_apply: true
auto_rerun_after_apply: false
```

如果使用 remote profile，把 SSH key 路径放在环境变量中，不写进 YAML：

```bash
export AGENTIC_TRACE_STAGING_API_SSH_KEY="$HOME/.ssh/staging_api_ed25519"
export AGENTIC_TRACE_STAGING_WORKER_SSH_KEY="$HOME/.ssh/staging_worker_ed25519"
```

启动前解析配置并做一次 core baseline：

```bash
STAGING_CONFIG="$HOME/agentic-staging/projects.staging.local.yaml"

.venv/bin/python -c "from monitors.project_registry import ProjectRegistry; print([p.project_id for p in ProjectRegistry('$STAGING_CONFIG').load_all()])"
scripts/run_core_tests.sh
```

对每个项目先跑 1 个 cycle smoke，确认日志可读、不会触发真实 apply/rerun：

```bash
.venv/bin/python main_monitor.py \
  --config "$STAGING_CONFIG" \
  --project staging_web_api_remote \
  --once \
  --agent-depth balanced \
  --report-mode rule \
  --state-dir state \
  --output-root outputs/monitors

.venv/bin/python main_monitor.py \
  --config "$STAGING_CONFIG" \
  --project staging_worker_remote \
  --once \
  --agent-depth balanced \
  --report-mode rule \
  --state-dir state \
  --output-root outputs/monitors

.venv/bin/python main_monitor.py \
  --config "$STAGING_CONFIG" \
  --project staging_etl_local \
  --once \
  --agent-depth balanced \
  --report-mode rule \
  --state-dir state \
  --output-root outputs/monitors
```

用普通用户后台运行 72 小时。建议每个项目单独 stdout 文件和 PID 文件，便于停止和复盘：

```bash
mkdir -p "$HOME/agentic-staging/logs"

nohup .venv/bin/python main_monitor.py \
  --config "$STAGING_CONFIG" \
  --project staging_web_api_remote \
  --daemon \
  --agent-depth balanced \
  --report-mode rule \
  --state-dir state \
  --output-root outputs/monitors \
  --heartbeat-interval 60 \
  --health-check-interval 300 \
  > "$HOME/agentic-staging/logs/staging_web_api_remote.stdout.log" 2>&1 &
echo "$!" > "$HOME/agentic-staging/logs/staging_web_api_remote.pid"

nohup .venv/bin/python main_monitor.py \
  --config "$STAGING_CONFIG" \
  --project staging_worker_remote \
  --daemon \
  --agent-depth balanced \
  --report-mode rule \
  --state-dir state \
  --output-root outputs/monitors \
  --heartbeat-interval 60 \
  --health-check-interval 300 \
  > "$HOME/agentic-staging/logs/staging_worker_remote.stdout.log" 2>&1 &
echo "$!" > "$HOME/agentic-staging/logs/staging_worker_remote.pid"

nohup .venv/bin/python main_monitor.py \
  --config "$STAGING_CONFIG" \
  --project staging_etl_local \
  --daemon \
  --agent-depth balanced \
  --report-mode rule \
  --state-dir state \
  --output-root outputs/monitors \
  --heartbeat-interval 60 \
  --health-check-interval 300 \
  > "$HOME/agentic-staging/logs/staging_etl_local.stdout.log" 2>&1 &
echo "$!" > "$HOME/agentic-staging/logs/staging_etl_local.pid"
```

同时启动 Trace UI，只绑定 localhost，通过本机浏览器或 SSH tunnel 访问：

```bash
export AGENTIC_TRACE_UI_TOKEN="<strong-local-admin-token>"

.venv/bin/python -m web_ui.server \
  --host 127.0.0.1 \
  --port 8765 \
  --config "$STAGING_CONFIG" \
  --state-dir state \
  --output-root outputs/monitors
```

每日检查项：

```bash
for project in staging_web_api_remote staging_worker_remote staging_etl_local; do
  echo "== $project =="
  test -f "state/$project/project_status.json" && tail -n 40 "state/$project/daemon.log"
  test -f "state/$project/events.jsonl" && wc -l "state/$project/events.jsonl"
  test -f "state/$project/jobs.jsonl" && wc -l "state/$project/jobs.jsonl"
  test -f "state/$project/recovery_history.jsonl" && wc -l "state/$project/recovery_history.jsonl"
done
```

重点观察 Trace UI、`state/<project_id>/daemon.log`、`project_status.json`、`events.jsonl`、`jobs.jsonl`、`recovery_history.jsonl`、`outputs/monitors/` 和 `outputs/alerts/`。确认：

- daemon 进程持续运行，heartbeat 和 health check 持续更新；
- 事件能识别，报告、alert、job 日志和恢复历史能在 UI 中查看；
- manual escalation 噪声在负责人可审阅范围内；
- dry-run recovery 只产生 planned/audit/report 证据，不修改真实项目；
- 没有真实 apply、rerun、危险命令、越权写入或 token/secret 泄露。

如需停止观察：

```bash
for project in staging_web_api_remote staging_worker_remote staging_etl_local; do
  kill "$(cat "$HOME/agentic-staging/logs/$project.pid")"
done
```

收尾验收：

```bash
scripts/run_core_tests.sh

for project in staging_web_api_remote staging_worker_remote staging_etl_local; do
  echo "== $project evidence =="
  ls -lah "state/$project" || true
  find "outputs/monitors/$project" -maxdepth 2 -type f | tail -n 20 || true
done
```

完成标志：72 小时内 daemon 可恢复、报告和状态完整，负责人能完成查看、dry-run、审批检查和恢复历史审阅。

## Phase 3 venv + systemd 部署

部署 monitor：

```bash
scripts/install_systemd_service.sh enterprise_demo_local lf lf
```

部署 Trace UI：

```bash
export AGENTIC_TRACE_UI_TOKEN="<admin-token>"
scripts/install_trace_ui_service.sh lf lf
```

角色 token 可选：

```bash
export AGENTIC_TRACE_UI_VIEWER_TOKEN="<viewer-token>"
export AGENTIC_TRACE_UI_OPERATOR_TOKEN="<operator-token>"
export AGENTIC_TRACE_UI_APPROVER_TOKEN="<approver-token>"
export AGENTIC_TRACE_UI_ADMIN_TOKEN="<admin-token>"
```

部署前检查：

```bash
.venv/bin/python scripts/preflight_deploy.py \
  --project-root "$PWD" \
  --python-bin "$PWD/.venv/bin/python" \
  --config "$PWD/configs/projects.yaml" \
  --state-dir "$PWD/state" \
  --output-root "$PWD/outputs/monitors" \
  --host 127.0.0.1 \
  --port 8765
```

卸载 Trace UI：

```bash
scripts/uninstall_trace_ui_service.sh
```

完成标志：staging host 可在 30 分钟内完成 monitor + Trace UI 部署，服务以非 root 用户运行，卸载后无 Trace UI systemd unit 残留。

## Phase 4 权限与审计

权限矩阵：

| role | 权限 |
| --- | --- |
| `viewer` | 只读 GET |
| `operator` | 连接、健康检查、启动/停止 monitor、刷新日志、生成报告、dry-run、普通 job 取消/重试 |
| `approver` | 审批、live apply、rollback、高风险 job 取消/重试 |
| `admin` | 全部权限 |

`/api/auth/status` 返回 `role` 和 `permissions`。后端强制 403；前端按钮禁用只用于体验。POST 产生的 job / approval 记录追加 `role` 和 `request_audit`，审计中不得出现 token 或 secret。

完成标志：`tests/test_web_ui_security.py` 和 `tests/test_web_ui_http_handler.py` 中权限测试通过。

## Phase 5 Playwright 浏览器回归

安装开发依赖：

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m playwright install chromium
```

运行浏览器测试：

```bash
scripts/run_browser_tests.sh
```

浏览器测试不进入 core baseline，默认覆盖登录、项目概览、权限按钮、关键视口和基础布局。更新截图 baseline：

```bash
AGENTIC_UPDATE_BROWSER_BASELINES=1 scripts/run_browser_tests.sh
```

完成标志：Chromium e2e 稳定通过，失败时能从 pytest 输出和截图定位。

## Phase 6 小范围受控 Live Validation

只有 Phase 1-5 全部通过后才进入。选择 isolated staging/demo project，最多启用 1 个低风险 fix_id，保持：

```yaml
require_human_approval_for_live_apply: true
```

单次 live apply 后立即验证 report、alert、audit、backup、diff 和 recovery history。必须演练一次 rollback。任何异常都回退到 dry-run 并暂停 live validation。
