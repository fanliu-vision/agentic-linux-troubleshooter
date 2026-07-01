# R16 阶段 5：domain policy registry 治理

本阶段目标是把 registry governance 从“发现 safe fix 漂移”升级为“禁止 domain 漂移”。新增故障域时只能先进入 `safe_recovery.registry` 的 registry domain policy，然后由 registry 派生 detector、compatibility adapter、runtime gate、precheck、guarded dry-run、executor 和 regression fixture 的行为。

## 治理范围

每个 registry domain 必须满足：

- `event_type` 全局唯一，`issue_type`、`strategy_layer`、`risk_level`、`reason` 非空；
- `strategy_layer` 只能是 `safe_auto_recover`、`manual_escalation` 或 `diagnose_only`；
- detector 中出现的 event 必须已经在 registry 注册，且 `issue_type` 与 registry 一致；
- regression fixture 中出现的 event 必须已经在 registry 注册；
- runtime gate 的 event 集合必须精确等于 registry domain 集合；
- runtime gate 的 safe/manual/diagnose 策略、风险等级、operator/precheck/rollback 要求必须与 registry 一致；
- compatibility remediation adapter 的 fix mapping 和 manual escalation 兼容别名必须由 registry 派生，不能有 registry 外条目。

safe domain 作为可执行投影，还必须满足：

- `SafeRecoverySpec` 与 `RecoveryDomainSpec` 的 `event_type` / `fix_id` 一致；
- `SAFE_CANDIDATE_EVENT_TYPES` 等 release-cycle 兼容导出精确等于 registry safe domain 集合；
- runtime gate 对 safe event 使用 `safe_auto_recover`，且只允许对应 fix_id；
- runtime gate 必须要求 precheck、rollback、audit，风险等级必须为 low；
- `SAFE_FIX_SAFETY_SPECS` 精确等于 registry safe fix 集合；
- guarded dry-run 候选精确等于 registry safe event/fix 映射；
- local 与 remote executor 广告的 safe fix 集合精确等于 registry safe fix 集合。

`unknown` 是 synthetic fallback domain，不作为 detector catch-all。这样可以避免未知规则吞掉 benign 日志，也能保留“未注册事件默认 diagnose/manual”的保守路径。

## 防止的漂移类型

- detector 新增了 event，但 registry 没有注册；
- regression fixture 期望了某个 event，但 registry 不知道该 domain；
- runtime gate 硬编码了 registry 外 event，或遗漏了 registry domain；
- safe/manual/diagnose 策略在 registry domain policy 与 runtime gate 之间不一致；
- compatibility adapter 维护了 registry 外 fix_id，或 manual escalation 兼容别名和 registry manual domain 漂移；
- precheck / guarded dry-run / local executor / remote executor 支持了 registry safe 投影之外的 fix；
- registry 中新增了可检测 domain，但没有补 detector 和 regression fixture。

## 新增域接入协议

后续新增任何故障域时，流程必须是：

1. 先在 `safe_recovery.registry` 增加或更新 `RecoveryDomainSpec`。
2. 如果该域可以自动低风险修复，再增加对应 `SafeRecoverySpec`；manual/diagnose 域不要强行塞入 safe fix spec。
3. 增加 detector 规则，并补 regression log case；synthetic fallback 域必须明确说明为什么不进入 detector。
4. 确认 runtime gate、compatibility adapter 兼容别名、precheck、guarded dry-run、local/remote executor 全部由 registry 派生。
5. 运行 registry governance 测试和核心测试基线。

## 验证入口

治理检查由 `safe_recovery.registry_governance.validate_safe_recovery_registry_governance()` 统一完成，测试负责把各模块当前暴露的契约输入进去。

核心入口：

```bash
scripts/run_core_tests.sh
```

局部入口：

```bash
.venv/bin/python -m pytest tests/test_safe_recovery_registry_governance.py -q
```
