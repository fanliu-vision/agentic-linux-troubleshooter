# R16-S1a safe recovery validation gap closure

## 1. 目标

R16-S1 已建立 safe recovery domain 验证基线，但 shadow validation 结论为 `PARTIAL`。R16-S1a 只关闭验证缺口，不新增第三批 safe 域，不扩大 `auto_recover` 权限，不执行真实恢复。

## 2. negative fixture 补齐

R16-S1a 为缺失专属 negative fixture 的 5 个 safe 域补齐回归样本：

| safe domain | negative fixture | 期望分类 | 安全含义 |
| --- | --- | --- | --- |
| `network_port` | `network_port_connectivity_negative.txt` | `network_connectivity` | 连接拒绝/网络问题不得触发端口修复 |
| `gpu_oom` | `gpu_oom_host_resource_negative.txt` | `host_resource` | 主机内存不足不得触发 GPU batch size 修复 |
| `cache_write_failed` | `cache_write_failed_disk_negative.txt` | `disk_full` | 通用磁盘满不得被缓存写入 safe 域吞并 |
| `optional_dependency_missing` | `optional_dependency_missing_python_env_negative.txt` | `python_env` | 核心依赖缺失不得按可选依赖降级 |
| `worker_overload` | `worker_overload_process_crash_negative.txt` | `process_crash` | worker 崩溃不得按并发下调自动恢复 |

补齐后 shadow validation 中 negative fixture 覆盖达到 `11/11`。

## 3. forbidden alias 增强

R16-S1a 将权限提升相关表达加入统一 forbidden action list：

- `sudo`
- `/usr/bin/sudo`
- `pkexec`
- `doas`
- `runas`
- `权限提升`
- `提权`
- `privilege escalation`

这些 alias 命中时，guarded dry-run 返回 `disabled`，`downgrade_reason=forbidden_action`，`would_execute=false`。

## 4. validation 结果

最新 shadow validation 输出：

```text
acceptance_artifacts/r16_safe_domain_validation_20260623_091843/R16_SAFE_RECOVERY_DOMAIN_VALIDATION_SUMMARY.md
```

结果：

| 指标 | 结果 |
| --- | --- |
| safe domain 总数 | `11` |
| positive fixture | `11/11` |
| negative fixture | `11/11` |
| no-op | `11/11` |
| rollback test | `11/11` |
| dry-run blocks execution | `11/11` |
| forbidden action blocked | `11/11` |
| high-risk manual/diagnose fallback | `True` |
| unknown fix_id downgrade | `True` |
| conclusion | `PASS` |

## 5. 后续建议

建议进入 `R16-S2` 长周期 dry-run / shadow 验证。进入 S2 时仍应保持：

- 默认 `auto_recovery_dry_run=true`；
- 不执行真实恢复；
- 不调用 `remote_apply_fix`；
- 不调用 `rerun_remote_project`；
- 继续统计 false positive、false negative、no-op、manual fallback 和 rollback metadata。
