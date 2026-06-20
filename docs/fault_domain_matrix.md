# 故障域矩阵

## 概览

Agent 通过 `ErrorEventDetector` 识别结构化故障域，并使用 `tests/fixtures/regression_logs/` 下的 regression log fixture 进行确定性验证。

`tests/test_fault_domain_regression.py` 会读取 `expected_cases.json`，运行 detector，检查预期 `event_type`，并验证人工升级类故障域的 policy 边界。这些测试不会调用 `AutoRecoveryRunner`，不会应用修复，也不会写入真实 `state/` 或 `outputs/` 数据。

policy 是 detection 与 action 之间的安全边界。detection 只生成 event；policy 决定该 event 是否可以自动恢复、是否必须人工升级，或是否仅生成报告。

术语说明：

- `auto_recover` 表示 policy 选择了受控 `fix_id`，且项目明确允许该 fix；
- `manual_escalation` 表示 Agent 应通知或报告给负责人，不能自动应用修复；
- `report_only` 表示未映射受控 fix，因此只生成报告。

## 故障域矩阵

| event_type | 典型日志特征 | 回归 fixture | 默认动作 | fix_id | 是否允许 auto_recover | 是否 manual_escalation | 安全说明 |
|---|---|---|---|---|---|---|---|
| `network_port` | `Address already in use`、`Errno 98`、bind failed、port already in use | `network_port_basic.log` | 项目允许时 `auto_recover` | `fix-network-1` | 是，仅限显式允许 | 默认否 | 仅做受控端口或配置调整；不代表通用网络连通性问题。 |
| `gpu_oom` | CUDA/HIP out of memory、`OutOfMemoryError`、accelerator memory constraints | `gpu_oom_basic.log` | 项目允许时 `auto_recover` | `fix-gpu-1` | 是，仅限显式允许 | 默认否 | 仅做受控 GPU 相关缓解；不覆盖主机 OOM 或 Kubernetes `OOMKilled`。 |
| `cache_write_failed` | cache write failed、failed to write cache、cache `Errno 28`、in-memory feature cache fallback | `cache_write_failed_basic.log` | 项目允许时 `auto_recover` | `fix-cache-1` | 是，仅限显式允许 | 默认否 | 只关闭可选缓存写入或缓存故障模拟；不执行 `rm`、不清理目录，不代表通用 `disk_full`。 |
| `optional_dependency_missing` | optional dependency missing、internal risk SDK unavailable、fallback local rule engine | `optional_dependency_missing_basic.log` | 项目允许时 `auto_recover` | `fix-optional-dep-1` | 是，仅限显式允许 | 默认否 | 只关闭可选依赖集成；不安装 package，不代表核心 `python_env` 故障。 |
| `worker_overload` | worker overload、worker pool exhausted、concurrency too high、queue backpressure | `worker_overload_basic.log` | 项目允许时 `auto_recover` | `fix-worker-1` | 是，仅限显式允许 | 默认否 | 只降低配置化 worker 并发；不 kill 进程、不 restart 服务，不代表主机级 `host_resource`。 |
| `disk_full` | `No space left on device`、`Errno 28`、disk quota exceeded、inode exhausted | `disk_full_basic.log` | `manual_escalation` | `<none>` | 否 | 是 | 不自动执行 `rm` 或破坏性清理。 |
| `python_env` | `ModuleNotFoundError`、`ImportError`、missing module、pip/interpreter mismatch | `python_env_basic.log` | 默认 `manual_escalation`，显式允许时才可考虑受控 fix | `fix-python-1` 候选 | 仅限项目显式允许 | 默认是 | 不执行任意 `pip install`。 |
| `slurm` | `slurmstepd`、pending resources、node down/drain、exceeded memory、batch job failed | `slurm_basic.log` | `manual_escalation` | `<none>` | 否 | 是 | 不自动执行 `scancel` 或修改调度器状态。 |
| `process_kill` | `SIGKILL`、signal kill、exit status 137、killed process | `process_kill_unsupported.log` | `manual_escalation` | `<none>` | 否 | 是 | 不自动执行 `kill` 或 restart。 |
| `permission_denied` | permission denied、`EACCES`、operation not permitted、access denied | `permission_denied_unsupported.log` | `manual_escalation` | `<none>` | 否 | 是 | token/cert/auth 归入 `auth_cert`；文件系统或 OS 权限拒绝归入本类。 |
| `process_crash` | systemd failed result、core dumped、segmentation fault、exited with code、signal 11 | `process_crash_basic.log` | `manual_escalation` | `<none>` | 否 | 是 | 不自动执行进程重启或 `systemctl restart`。 |
| `host_resource` | Linux OOM、cannot allocate memory、too many open files、load average too high | `host_resource_basic.log` | `manual_escalation` | `<none>` | 否 | 是 | 不吞并 `disk_full`、CUDA OOM 或 Kubernetes `OOMKilled`。 |
| `network_connectivity` | DNS failure、connection timed out、connection refused、TLS handshake timeout | `network_connectivity_basic.log` | `manual_escalation` | `<none>` | 否 | 是 | 通用网络连通性问题；本地 bind 冲突仍归入 `network_port`。 |
| `dependency_service` | MySQL/PostgreSQL connection failed、Redis connection refused、Kafka broker unavailable、RabbitMQ/MQ timeout、DB pool exhausted | `dependency_service_basic.log` | `manual_escalation` | `<none>` | 否 | 是 | 外部依赖服务故障与通用网络连通性分开。 |
| `config_error` | missing required config key、invalid YAML/JSON/TOML、invalid config value/path/port、config file not found | `config_error_basic.log` | `manual_escalation` | `<none>` | 否 | 是 | 配置格式或配置值问题不归入 `python_env`；Kubernetes `CreateContainerConfigError` 归入 `container_k8s`。 |
| `auth_cert` | HTTP 401/403、token expired、invalid token、certificate expired、certificate verify failed、TLS certificate handshake error | `auth_cert_basic.log` | `manual_escalation` | `<none>` | 否 | 是 | token、证书和鉴权失败与 OS `permission_denied` 分开。 |
| `container_k8s` | `CrashLoopBackOff`、`ImagePullBackOff`、`ErrImagePull`、`OOMKilled`、`CreateContainerConfigError`、back-off restarting failed container、pod failed scheduling | `container_k8s_basic.log` | `manual_escalation` | `<none>` | 否 | 是 | 不自动执行 `kubectl delete`、`kubectl restart` 或 `kubectl apply`；Kubernetes `OOMKilled` 保持在本类。 |
| benign/info | normal startup、health OK、successful metrics listener、scheduled sync complete | `benign_info.log` | 不应生成事件 | `<none>` | 否 | 否 | 回归测试确认普通信息日志不会生成事件。 |

## live 验证状态

| event_type | live 验证状态 | 证据 |
|---|---|---|
| `process_crash` | R9 live smoke 通过；R10-4h multi-event live smoke 通过 | R9: `outputs/monitors/enterprise_demo_local/f09ee00e/event_20260616_153917_process_crash_manual_escalation_final_llm_report.md`；R10-4h: `acceptance_artifacts/multi_event_live_smoke_r10_clean_20260617_105036/R10_4D_CLEAN_MULTI_EVENT_LIVE_SMOKE_SUMMARY.md` |
| `container_k8s` | R9 isolated live smoke 通过；R10-4h multi-event live smoke 通过 | R9: `outputs/monitors/enterprise_demo_local/f09ee00e/event_20260616_155317_container_k8s_manual_escalation_final_llm_report.md`；R10-4h: `acceptance_artifacts/multi_event_live_smoke_r10_clean_20260617_105036/R10_4D_CLEAN_MULTI_EVENT_LIVE_SMOKE_SUMMARY.md` |

其他新增企业故障域已通过 regression 验证，但并未全部逐一执行 live smoke。

## 自动恢复边界

只有少量受控 fix 可以进入 `auto_recover`，且必须由项目 policy 显式允许对应 `fix_id`：

- `network_port` 可在允许时通过 `fix-network-1` 自动恢复；
- `gpu_oom` 可在允许时通过 `fix-gpu-1` 自动恢复；
- `cache_write_failed` 可在允许时通过 `fix-cache-1` 自动恢复，只做配置化缓存降级；
- `optional_dependency_missing` 可在允许时通过 `fix-optional-dep-1` 自动恢复，只关闭可选集成；
- `worker_overload` 可在允许时通过 `fix-worker-1` 自动恢复，只降低配置化并发；
- `python_env` 有 `fix-python-1` 候选，但默认测试要求显式允许，且不能执行任意 package install；
- 高风险企业故障域默认走 `manual_escalation`。

multi-event 模式下仍受以下限制：

- 每轮最多处理 `3` 个 event；
- 每轮最多执行 `1` 个 `auto_recover`；
- 多个人工升级 event 可以分别生成 report/alert；
- 未新增危险自动恢复。

Agent 不会自动执行：

- `kill`
- `rm`
- 任意 `pip install`
- `systemctl restart`
- `kubectl delete`
- `kubectl restart`
- `kubectl apply`

## 分类边界

- `Address already in use` 归入 `network_port`；
- CUDA OOM 归入 `gpu_oom`；
- Kubernetes `OOMKilled` 归入 `container_k8s`；
- Linux `cannot allocate memory` 归入 `host_resource`；
- `No space left on device` 归入 `disk_full`；
- 缓存上下文中的 `No space left on device` 或 `Errno 28` 归入 `cache_write_failed`；
- 带 fallback 证据的可选依赖缺失归入 `optional_dependency_missing`；
- 配置化 worker 并发或队列过载归入 `worker_overload`；
- `ModuleNotFoundError` 归入 `python_env`；
- permission denied 归入 `permission_denied`；
- token、certificate、HTTP 401、HTTP 403 归入 `auth_cert`；
- DB、Redis、Kafka、RabbitMQ、MQ、connection pool 失败归入 `dependency_service`；
- YAML、JSON、TOML、config key、path、port 错误归入 `config_error`。

## multi-event 验证说明

R10-4h 已验证同一日志窗口中的两个故障域可以独立生成 report/alert：

- `process_crash`：multi-event live smoke 通过；
- `container_k8s`：multi-event live smoke 通过。

R10-4f 发现远程日志 tail 输出被前缀截断，导致最新故障行未进入 detector。R10-4g 已修复日志 tail 场景的截断策略，截断时保留输出尾部。R10-4h 重启 daemon 后验证该修复生效。

## 报告与重复判断说明

每个事件可能生成人工升级报告与通知后报告。这是正常处理链路，不属于重复生成。

多次 smoke 的结果会保留在同一个 `outputs/` 目录中。判断是否属于同一次 smoke 或是否重复，需要结合：

- smoke id；
- `event_type`；
- `fingerprint`；
- 时间戳；
- raw evidence 中的故障对象名称。

## 运行测试

运行完整 core baseline：

```bash
scripts/run_core_tests.sh
```

运行 fault-domain regression：

```bash
.venv/bin/python -m pytest tests/test_fault_domain_regression.py -q
```

运行 R10 multi-event 相关测试：

```bash
.venv/bin/python -m pytest tests/test_remote_log_tail_truncation.py -q
.venv/bin/python -m pytest tests/test_multi_event_detector.py -q
.venv/bin/python -m pytest tests/test_stage6e_monitor_loop_multi_event.py -q
```

## core baseline 之外的内容

以下内容仍不属于默认 core baseline：

- D1-mini；
- D2；
- systemd lifecycle acceptance；
- long-running daemon/watch tests；
- live smoke。
