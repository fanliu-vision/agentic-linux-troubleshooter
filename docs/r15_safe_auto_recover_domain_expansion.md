# R15 safe_auto_recover 故障域扩展说明

## 1. 目标

本次扩展把三个企业项目中常见、且可以严格边界化的故障域加入 `safe_auto_recover`：

- `cache_write_failed -> fix-cache-1`
- `optional_dependency_missing -> fix-optional-dep-1`
- `worker_overload -> fix-worker-1`

这些恢复域只允许项目内 JSON 配置修改，沿用 R15 policy validator、runtime gate、dry-run、audit 与 rollback 边界。它们不是危险操作授权，也不是通用 `disk_full`、`python_env` 或 `host_resource` 的自动恢复入口。

## 2. 安全候选范围

| event_type | fix_id | 允许动作 | 禁止动作 |
| --- | --- | --- | --- |
| `cache_write_failed` | `fix-cache-1` | 关闭可选缓存写入，例如 `cache_enabled=false`，或关闭 demo 缓存故障模拟 | 不删除缓存目录，不执行 `rm -rf`，不处理通用磁盘满 |
| `optional_dependency_missing` | `fix-optional-dep-1` | 关闭可选依赖集成，例如 `optional_dependency_enabled=false` | 不安装包，不修改解释器环境，不处理核心依赖缺失 |
| `worker_overload` | `fix-worker-1` | 降低配置化 worker 并发，例如 `worker_concurrency=2` | 不 kill worker，不重启服务，不执行 systemd/kubectl 操作 |

## 3. 策略边界

进入真实执行路径必须同时满足：

- detector 命中上述精确 `event_type`；
- `RemediationPolicy.DEFAULT_FIX_MAPPING` 映射到对应 fix_id；
- `projects.yaml` 中 `policy.allow_auto_apply` 显式包含该 fix_id；
- R15 runtime policy 将该 event_type 配置为 `safe_auto_recover`；
- R15 precheck 通过；
- rollback 可用；
- `auto_recovery_dry_run=false` 时才允许真实执行。

任一条件不满足时必须降级为 `manual_escalation`、`diagnose_only` 或 `report_only`。

## 4. 执行边界

真实执行仍然只通过 `SafeApplyExecutor` / `RemoteSafeApplyExecutor` 的受控 JSON 编辑能力完成：

- 每次修改都写入 `applied_fixes.json` 或 `remote_applied_fixes.json`；
- 每次修改都生成 backup 与 diff；
- rollback 使用已有备份恢复；
- 找不到受控字段时 apply 失败并降级，不会尝试命令式修复。

## 5. 明确不扩大范围

以下故障域仍保持人工升级或诊断：

- `disk_full`
- `python_env`
- `process_crash`
- `container_k8s`
- `auth_cert`
- `dependency_service`
- `host_resource`
- `network_connectivity`
- `permission_denied`
- `slurm`

以下动作仍禁止自动执行：

- `kill -9`
- `rm -rf`
- `pip install`
- `systemctl restart`
- `systemctl stop`
- `kubectl delete`
- `kubectl apply`
- 权限提升
- 跨主机破坏性操作

## 6. 验证覆盖

新增测试覆盖：

- 新三类 detector 分类；
- 同 scope 下专用域压过通用域，例如 cache 写失败不再同时生成 `disk_full`；
- policy 显式 allowlist 后才允许 auto recover；
- runtime gate 在 `dry_run=true` 时仍阻断真实执行；
- runtime gate 在 `dry_run=false` 且全部条件满足时返回 `would_execute=true`；
- policy dry-run 和 guarded dry-run 能生成审计；
- `SafeApplyExecutor` 只修改受控 JSON 字段并可 rollback；
- 高风险或通用域仍保持人工升级。

## 7. 后续 live 测试建议

建议按以下顺序做 live 验证：

1. `cache_write_failed / fix-cache-1` dry-run live；
2. `optional_dependency_missing / fix-optional-dep-1` dry-run live；
3. `worker_overload / fix-worker-1` dry-run live；
4. 单域隔离真实恢复测试；
5. 多事件窗口下验证每轮 auto_recover 上限。

真实测试前应确认 `auto_recovery_dry_run=false` 是用户主动切换，并且测试对象为隔离项目。

## 8. 结论

本次扩展把三个具备企业价值的低风险故障域纳入 `safe_auto_recover`，但恢复动作仍限制在可审计、可回滚、项目内 JSON 配置修改范围内。系统没有授权危险命令，也没有打开通用高风险故障域的自动恢复。
