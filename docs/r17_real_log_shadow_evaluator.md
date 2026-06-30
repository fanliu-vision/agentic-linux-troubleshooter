# R17 真实日志 Shadow Evaluator

## 范围

R17 进入真实 / 仿生产日志的只读 shadow 阶段。本阶段不扩大 safe recovery
域，不执行 live recovery。

Evaluator 只运行：

- `ErrorEventDetector.detect_all()` / `detect()`;
- 基于合成只读项目策略的 `RemediationPolicy.decide()`。

它不会调用 `MonitorLoop`、`AutoRecoveryRunner`、apply、rerun、rollback、
notification 或真实报告生成链路。

## 工具入口

```bash
.venv/bin/python scripts/r17_real_log_shadow_evaluate.py \
  --manifest tests/fixtures/r17_real_log_shadow/manifest.json \
  --output-dir outputs/r17_real_log_shadow/prod_like_fixture_20260630
```

未标注真实日志 baseline：

```bash
.venv/bin/python scripts/r17_real_log_shadow_evaluate.py \
  --log /home/lf/runtime_projects/enterprise_order_monitoring_service/outputs/service.log \
  --output-dir outputs/r17_real_log_shadow/runtime_service_log_unlabeled_20260630
```

生成的 Markdown 报告使用中文输出；JSON 字段保留英文，方便后续脚本解析。

## Manifest 格式

```json
{
  "schema_version": "r17.real_log_shadow.fixture.v1",
  "cases": [
    {
      "case_id": "prod_like_cache_write_safe",
      "log_file": "prod_like_cache_write_safe.log",
      "source_kind": "production_like_safe",
      "expected_event_types": ["cache_write_failed"]
    }
  ]
}
```

带 `expected_event_types` 的 case 用于计算 FP/FN。通过 `--log` 或 `--log-dir`
传入的日志按未标注样本处理，只统计检测量、manual escalation 量和串域数量。

## 统计指标

- `false_positive_count`：检测到但不在期望标签中的事件类型。
- `false_negative_count`：期望存在但未检测到的事件类型。
- `safe_swallowed_high_risk_count`：标注为高风险 / manual 的样本中，检测到了
  safe 事件，但期望的高风险事件缺失。
- `manual_escalation_noise_count`：不应出现 manual/high-risk 的样本中检测到了
  manual escalation 事件。
- `cross_domain_case_count`：相邻域同窗出现的样本数量，例如
  `worker_overload` + `queue_backpressure`。
- `safe_high_risk_overlap_count`：safe 域与 high-risk/manual 域同窗共现，但没有
  互相吞掉。该项用于提醒人工关注，不直接判 FAIL。
- `detected_event_instance_count`：原始检测事件实例数。
- `detected_event_count`：逐样本去重后的检测事件类型数之和。

## 当前 Baseline

仿生产标注集：

```text
outputs/r17_real_log_shadow/prod_like_fixture_20260630/R17_REAL_LOG_SHADOW_SUMMARY.md
```

结果：

| 指标 | 值 |
| --- | --- |
| conclusion | `PASS` |
| case_count | `13` |
| expected_event_count | `15` |
| true_positive_count | `15` |
| false_positive_count | `0` |
| false_negative_count | `0` |
| safe_swallowed_high_risk_count | `0` |
| safe_high_risk_overlap_count | `3` |
| manual_escalation_noise_count | `0` |
| cross_domain_case_count | `4` |

未标注 runtime service log：

```text
outputs/r17_real_log_shadow/runtime_service_log_unlabeled_20260630/R17_REAL_LOG_SHADOW_SUMMARY.md
```

结果：

| 指标 | 值 |
| --- | --- |
| conclusion | `PASS` |
| labeled_case_count | `0` |
| detected_event_instance_count | `50` |
| detected_event_count | `8` |
| manual_escalation_count | `25` |
| cross_domain_case_count | `1` |

由于该 runtime log 未标注，FP/FN 没有统计意义。它主要作为只读检测量、
manual escalation 量和串域 baseline。

## 验收边界

带标注仿生产样本满足以下条件时，可认为本阶段健康：

- `false_positive_count=0` or an understood, documented exception;
- high-risk/manual 域 `false_negative_count=0`；
- `safe_swallowed_high_risk_count=0`;
- manual escalation 噪声低到负责人可审阅；
- 串域样本在报告中可见，而不是被 detector suppress 掉。

只要 `safe_swallowed_high_risk_count > 0`，就应暂停扩大自动恢复范围，先审查
样本和 detector 行为。
