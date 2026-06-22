# R16 阶段 3：safe fix 注册一致性治理

本阶段目标不是新增执行能力，也不是扩大 safe_auto_recover 的风险边界，而是把 R16-0 引入的 `SafeRecoverySpec` registry 固化成跨模块注册协议，避免新增企业故障域时出现“某一层放行、另一层不支持”的漂移。

## 治理范围

每个 registry 中的 safe event 必须同时满足：

- detector 有对应 `event_type`，且 `issue_type` 与 registry 一致；
- `SAFE_CANDIDATE_EVENT_TYPES` 精确等于 registry event 集合；
- `RemediationPolicy.DEFAULT_FIX_MAPPING` 能把 event 映射到 registry fix_id；
- runtime gate policy 对该 event 使用 `safe_auto_recover`，且只允许对应 fix_id；
- runtime gate policy 必须要求 precheck、rollback、audit，风险等级必须为 low；
- `SAFE_FIX_SAFETY_SPECS` 精确等于 registry fix 集合；
- guarded dry-run 候选精确等于 registry event/fix 映射；
- local 与 remote executor 广告的 safe fix 集合精确等于 registry fix 集合；
- 回归 fixture 至少包含该 event 的 detector 期望样例。

这些检查由 `safe_recovery.registry_governance.validate_safe_recovery_registry_governance()` 统一完成，测试负责把各模块当前暴露的契约输入进去。

## 防止的漂移类型

- policy 把一个 event 标为 safe，但 executor 并不支持对应 fix；
- precheck specs 中出现了 fix，但 runtime gate 没有放行对应 event；
- runtime gate 放行了 event，但 legacy remediation mapping 没有产生相同 fix_id；
- detector 的 `issue_type` 与 registry 定义不一致，导致 legacy issue fallback 走错；
- guarded dry-run 候选、local apply、remote apply 与 registry 不一致；
- registry 中新增了 safe event，但没有补 detector 回归证据。

## 非目标

- 不新增新的企业 safe 子域；
- 不改变现有 5 个 safe 域的执行行为；
- 不放宽 R16 安全边界：仍只允许项目内 JSON 配置变更，不允许重启、删除、安装依赖、证书修改、K8s 变更、清磁盘或提权。

## 新增 safe 域接入协议

后续新增企业 safe 域时，推荐流程：

1. 在 `safe_recovery.registry` 注册 `SafeRecoverySpec`。
2. 增加 detector 规则和 regression log case。
3. 为候选字段选择语义规则，例如 `disable_bool`、`lower_int`、`port_available`。
4. 确认 runtime policy、precheck、guarded dry-run、local/remote executor 全部由 registry 派生。
5. 运行 registry governance 测试和 R15/R16 回归。

阶段 3 的关键收益是：新增域时不再靠人工记忆同步多个散点，而是由 registry governance 测试集中证明“检测、策略、gate、precheck、执行、证据”在同一条安全链路上。
