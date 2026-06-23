from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .semantics import (
    SEMANTIC_DISABLE_BOOL,
    SEMANTIC_LOWER_INT,
    SEMANTIC_PORT_AVAILABLE,
    SEMANTIC_SAFE_ENUM_DOWNGRADE,
    SEMANTIC_SET_LITERAL,
)


@dataclass(frozen=True)
class SafeRecoveryFieldCandidate:
    field_path: str
    new_value: Any
    semantic_rule: str = SEMANTIC_SET_LITERAL


@dataclass(frozen=True)
class SafeRecoverySpec:
    event_type: str
    issue_type: str
    fix_id: str
    relative_config_path: str
    candidates: tuple[SafeRecoveryFieldCandidate, ...]
    low_risk_reason: str
    action_description: str
    local_success_message: str
    remote_success_message: str
    remote_failure_message: str


SAFE_RECOVERY_SPECS: tuple[SafeRecoverySpec, ...] = (
    SafeRecoverySpec(
        event_type="network_port",
        issue_type="network_port",
        fix_id="fix-network-1",
        relative_config_path="config.json",
        candidates=(
            SafeRecoveryFieldCandidate(
                "metrics_port",
                9101,
                SEMANTIC_PORT_AVAILABLE,
            ),
        ),
        low_risk_reason="only edits the metrics port JSON field",
        action_description="safe JSON config edit: config.json metrics_port -> 9101",
        local_success_message="已尝试应用端口冲突修复：metrics_port 改为 9101。",
        remote_success_message="已远程应用端口冲突修复：metrics_port 改为 9101。",
        remote_failure_message=(
            "远程端口冲突修复失败：未找到受控 metrics_port 字段。"
        ),
    ),
    SafeRecoverySpec(
        event_type="gpu_oom",
        issue_type="gpu",
        fix_id="fix-gpu-1",
        relative_config_path="config.json",
        candidates=(
            SafeRecoveryFieldCandidate("batch_size", 4, SEMANTIC_LOWER_INT),
            SafeRecoveryFieldCandidate("train_batch_size", 4, SEMANTIC_LOWER_INT),
            SafeRecoveryFieldCandidate(
                "per_device_train_batch_size",
                4,
                SEMANTIC_LOWER_INT,
            ),
            SafeRecoveryFieldCandidate("samples_per_gpu", 4, SEMANTIC_LOWER_INT),
            SafeRecoveryFieldCandidate("training.batch_size", 4, SEMANTIC_LOWER_INT),
            SafeRecoveryFieldCandidate("model.batch_size", 4, SEMANTIC_LOWER_INT),
        ),
        low_risk_reason="only lowers an explicit batch-size style JSON field",
        action_description="safe JSON config edit: config.json batch_size -> 4",
        local_success_message="已尝试应用 GPU OOM 修复：降低 batch_size。",
        remote_success_message="已远程应用 GPU OOM 修复：降低受控 batch size 字段。",
        remote_failure_message=(
            "远程 GPU OOM 修复失败：未找到可修改的 batch size 字段。"
            "已检查 batch_size、train_batch_size、per_device_train_batch_size、"
            "samples_per_gpu、training.batch_size、model.batch_size。"
        ),
    ),
    SafeRecoverySpec(
        event_type="cache_write_failed",
        issue_type="cache",
        fix_id="fix-cache-1",
        relative_config_path="config.json",
        candidates=(
            SafeRecoveryFieldCandidate("cache_enabled", False, SEMANTIC_DISABLE_BOOL),
            SafeRecoveryFieldCandidate(
                "feature_cache_enabled",
                False,
                SEMANTIC_DISABLE_BOOL,
            ),
            SafeRecoveryFieldCandidate(
                "cache.write_enabled",
                False,
                SEMANTIC_DISABLE_BOOL,
            ),
            SafeRecoveryFieldCandidate(
                "simulate_cache_write_failed",
                False,
                SEMANTIC_DISABLE_BOOL,
            ),
            SafeRecoveryFieldCandidate(
                "simulate_disk_full",
                False,
                SEMANTIC_DISABLE_BOOL,
            ),
        ),
        low_risk_reason="only disables optional cache write behavior",
        action_description="safe JSON config edit: disable optional cache writes",
        local_success_message=(
            "已尝试应用缓存写入修复：关闭可选缓存写入或缓存故障模拟。"
        ),
        remote_success_message=(
            "已远程应用缓存写入修复：关闭可选缓存写入或缓存故障模拟。"
        ),
        remote_failure_message=(
            "远程缓存写入修复失败：未找到受控缓存开关字段。"
            "已检查 cache_enabled、feature_cache_enabled、cache.write_enabled、"
            "simulate_cache_write_failed、simulate_disk_full。"
        ),
    ),
    SafeRecoverySpec(
        event_type="optional_dependency_missing",
        issue_type="optional_dependency",
        fix_id="fix-optional-dep-1",
        relative_config_path="config.json",
        candidates=(
            SafeRecoveryFieldCandidate(
                "optional_dependency_enabled",
                False,
                SEMANTIC_DISABLE_BOOL,
            ),
            SafeRecoveryFieldCandidate(
                "optional_dependencies.internal_risk_sdk.enabled",
                False,
                SEMANTIC_DISABLE_BOOL,
            ),
            SafeRecoveryFieldCandidate(
                "plugins.internal_risk_sdk.enabled",
                False,
                SEMANTIC_DISABLE_BOOL,
            ),
            SafeRecoveryFieldCandidate(
                "risk_sdk_enabled",
                False,
                SEMANTIC_DISABLE_BOOL,
            ),
            SafeRecoveryFieldCandidate(
                "simulate_python_env_mismatch",
                False,
                SEMANTIC_DISABLE_BOOL,
            ),
        ),
        low_risk_reason="only disables an optional integration or demo warning flag",
        action_description=(
            "safe JSON config edit: disable optional dependency integration"
        ),
        local_success_message=(
            "已尝试应用可选依赖降级修复：关闭可选集成或相关告警模拟。"
        ),
        remote_success_message=(
            "已远程应用可选依赖降级修复：关闭可选集成或相关告警模拟。"
        ),
        remote_failure_message=(
            "远程可选依赖降级修复失败：未找到受控可选依赖开关字段。"
            "已检查 optional_dependency_enabled、optional_dependencies.internal_risk_sdk.enabled、"
            "plugins.internal_risk_sdk.enabled、risk_sdk_enabled、simulate_python_env_mismatch。"
        ),
    ),
    SafeRecoverySpec(
        event_type="optional_integration_failed",
        issue_type="optional_integration",
        fix_id="fix-optional-integration-1",
        relative_config_path="config.json",
        candidates=(
            SafeRecoveryFieldCandidate(
                "optional_webhook_enabled",
                False,
                SEMANTIC_DISABLE_BOOL,
            ),
            SafeRecoveryFieldCandidate(
                "risk_sdk_enabled",
                False,
                SEMANTIC_DISABLE_BOOL,
            ),
            SafeRecoveryFieldCandidate(
                "enrichment_client_enabled",
                False,
                SEMANTIC_DISABLE_BOOL,
            ),
            SafeRecoveryFieldCandidate(
                "optional_integrations.risk_sdk.enabled",
                False,
                SEMANTIC_DISABLE_BOOL,
            ),
            SafeRecoveryFieldCandidate(
                "optional_integrations.enrichment.enabled",
                False,
                SEMANTIC_DISABLE_BOOL,
            ),
            SafeRecoveryFieldCandidate(
                "simulate_optional_integration_failed",
                False,
                SEMANTIC_DISABLE_BOOL,
            ),
        ),
        low_risk_reason="only disables optional integration clients",
        action_description=(
            "safe JSON config edit: disable failed optional integration"
        ),
        local_success_message=(
            "已尝试应用可选集成降级修复：关闭失败的可选外部集成。"
        ),
        remote_success_message=(
            "已远程应用可选集成降级修复：关闭失败的可选外部集成。"
        ),
        remote_failure_message=(
            "远程可选集成降级修复失败：未找到受控可选集成开关字段。"
            "已检查 optional_webhook_enabled、risk_sdk_enabled、"
            "enrichment_client_enabled、optional_integrations.risk_sdk.enabled、"
            "optional_integrations.enrichment.enabled、simulate_optional_integration_failed。"
        ),
    ),
    SafeRecoverySpec(
        event_type="optional_cache_backend_failed",
        issue_type="optional_cache_backend",
        fix_id="fix-cache-backend-1",
        relative_config_path="config.json",
        candidates=(
            SafeRecoveryFieldCandidate(
                "cache.backend",
                "memory",
                SEMANTIC_SAFE_ENUM_DOWNGRADE,
            ),
            SafeRecoveryFieldCandidate(
                "cache.mode",
                "memory",
                SEMANTIC_SAFE_ENUM_DOWNGRADE,
            ),
            SafeRecoveryFieldCandidate(
                "cache.redis_enabled",
                False,
                SEMANTIC_DISABLE_BOOL,
            ),
            SafeRecoveryFieldCandidate(
                "cache.write_enabled",
                False,
                SEMANTIC_DISABLE_BOOL,
            ),
            SafeRecoveryFieldCandidate(
                "feature_cache_enabled",
                False,
                SEMANTIC_DISABLE_BOOL,
            ),
            SafeRecoveryFieldCandidate(
                "simulate_optional_cache_backend_failed",
                False,
                SEMANTIC_DISABLE_BOOL,
            ),
        ),
        low_risk_reason="only switches optional cache backend to memory or disables optional cache backend writes",
        action_description="safe JSON config edit: degrade optional cache backend to memory/local mode",
        local_success_message=(
            "已尝试应用可选缓存后端降级修复：切换到 memory/local 或关闭可选缓存后端。"
        ),
        remote_success_message=(
            "已远程应用可选缓存后端降级修复：切换到 memory/local 或关闭可选缓存后端。"
        ),
        remote_failure_message=(
            "远程可选缓存后端降级修复失败：未找到受控缓存后端字段。"
            "已检查 cache.backend、cache.mode、cache.redis_enabled、"
            "cache.write_enabled、feature_cache_enabled、simulate_optional_cache_backend_failed。"
        ),
    ),
    SafeRecoverySpec(
        event_type="optional_service_unavailable",
        issue_type="optional_service",
        fix_id="fix-optional-service-1",
        relative_config_path="config.json",
        candidates=(
            SafeRecoveryFieldCandidate(
                "optional_services.enrichment.enabled",
                False,
                SEMANTIC_DISABLE_BOOL,
            ),
            SafeRecoveryFieldCandidate(
                "optional_services.recommendation.enabled",
                False,
                SEMANTIC_DISABLE_BOOL,
            ),
            SafeRecoveryFieldCandidate(
                "optional_services.risk_scoring.enabled",
                False,
                SEMANTIC_DISABLE_BOOL,
            ),
            SafeRecoveryFieldCandidate(
                "enrichment_enabled",
                False,
                SEMANTIC_DISABLE_BOOL,
            ),
            SafeRecoveryFieldCandidate(
                "recommendation_enabled",
                False,
                SEMANTIC_DISABLE_BOOL,
            ),
            SafeRecoveryFieldCandidate(
                "external_risk_scoring_enabled",
                False,
                SEMANTIC_DISABLE_BOOL,
            ),
            SafeRecoveryFieldCandidate(
                "simulate_optional_service_unavailable",
                False,
                SEMANTIC_DISABLE_BOOL,
            ),
        ),
        low_risk_reason="only disables optional enrichment/recommendation/risk-scoring services",
        action_description="safe JSON config edit: disable unavailable optional service",
        local_success_message=(
            "已尝试应用可选服务降级修复：关闭不可用的 enrichment/recommendation/risk scoring。"
        ),
        remote_success_message=(
            "已远程应用可选服务降级修复：关闭不可用的 enrichment/recommendation/risk scoring。"
        ),
        remote_failure_message=(
            "远程可选服务降级修复失败：未找到受控可选服务开关字段。"
            "已检查 optional_services.enrichment.enabled、optional_services.recommendation.enabled、"
            "optional_services.risk_scoring.enabled、enrichment_enabled、recommendation_enabled、"
            "external_risk_scoring_enabled、simulate_optional_service_unavailable。"
        ),
    ),
    SafeRecoverySpec(
        event_type="notification_sink_failed",
        issue_type="notification_sink",
        fix_id="fix-notification-sink-1",
        relative_config_path="config.json",
        candidates=(
            SafeRecoveryFieldCandidate(
                "notification.webhook_enabled",
                False,
                SEMANTIC_DISABLE_BOOL,
            ),
            SafeRecoveryFieldCandidate(
                "notifications.webhook.enabled",
                False,
                SEMANTIC_DISABLE_BOOL,
            ),
            SafeRecoveryFieldCandidate(
                "notification.remote_sink_enabled",
                False,
                SEMANTIC_DISABLE_BOOL,
            ),
            SafeRecoveryFieldCandidate(
                "notification_sink.webhook_enabled",
                False,
                SEMANTIC_DISABLE_BOOL,
            ),
            SafeRecoveryFieldCandidate(
                "simulate_notification_sink_failed",
                False,
                SEMANTIC_DISABLE_BOOL,
            ),
        ),
        low_risk_reason="only disables optional remote notification sinks",
        action_description=(
            "safe JSON config edit: disable failed optional notification sink"
        ),
        local_success_message=(
            "已尝试应用通知后端降级修复：关闭失败的可选远程通知 sink。"
        ),
        remote_success_message=(
            "已远程应用通知后端降级修复：关闭失败的可选远程通知 sink。"
        ),
        remote_failure_message=(
            "远程通知后端降级修复失败：未找到受控通知 sink 开关字段。"
            "已检查 notification.webhook_enabled、notifications.webhook.enabled、"
            "notification.remote_sink_enabled、notification_sink.webhook_enabled、"
            "simulate_notification_sink_failed。"
        ),
    ),
    SafeRecoverySpec(
        event_type="observability_export_failed",
        issue_type="observability_export",
        fix_id="fix-observability-export-1",
        relative_config_path="config.json",
        candidates=(
            SafeRecoveryFieldCandidate(
                "observability.exporter_mode",
                "local",
                SEMANTIC_SAFE_ENUM_DOWNGRADE,
            ),
            SafeRecoveryFieldCandidate(
                "observability.metrics_sink",
                "file",
                SEMANTIC_SAFE_ENUM_DOWNGRADE,
            ),
            SafeRecoveryFieldCandidate(
                "observability.tracing_sink",
                "console",
                SEMANTIC_SAFE_ENUM_DOWNGRADE,
            ),
            SafeRecoveryFieldCandidate(
                "observability.remote_exporter_enabled",
                False,
                SEMANTIC_DISABLE_BOOL,
            ),
            SafeRecoveryFieldCandidate(
                "metrics.remote_exporter_enabled",
                False,
                SEMANTIC_DISABLE_BOOL,
            ),
            SafeRecoveryFieldCandidate(
                "tracing.remote_exporter_enabled",
                False,
                SEMANTIC_DISABLE_BOOL,
            ),
            SafeRecoveryFieldCandidate(
                "simulate_observability_export_failed",
                False,
                SEMANTIC_DISABLE_BOOL,
            ),
        ),
        low_risk_reason="only disables optional remote observability exporters or switches them to local/file/console",
        action_description="safe JSON config edit: degrade failed observability exporter to local/file/console",
        local_success_message=(
            "已尝试应用观测导出降级修复：关闭远程 exporter 或切换到 local/file/console。"
        ),
        remote_success_message=(
            "已远程应用观测导出降级修复：关闭远程 exporter 或切换到 local/file/console。"
        ),
        remote_failure_message=(
            "远程观测导出降级修复失败：未找到受控 exporter 字段。"
            "已检查 observability.exporter_mode、observability.metrics_sink、"
            "observability.tracing_sink、observability.remote_exporter_enabled、"
            "metrics.remote_exporter_enabled、tracing.remote_exporter_enabled、"
            "simulate_observability_export_failed。"
        ),
    ),
    SafeRecoverySpec(
        event_type="queue_backpressure",
        issue_type="queue_backpressure",
        fix_id="fix-queue-backpressure-1",
        relative_config_path="config.json",
        candidates=(
            SafeRecoveryFieldCandidate("prefetch_count", 2, SEMANTIC_LOWER_INT),
            SafeRecoveryFieldCandidate("max_inflight", 10, SEMANTIC_LOWER_INT),
            SafeRecoveryFieldCandidate("consumer_workers", 2, SEMANTIC_LOWER_INT),
            SafeRecoveryFieldCandidate("queue.prefetch_count", 2, SEMANTIC_LOWER_INT),
            SafeRecoveryFieldCandidate("queue.max_inflight", 10, SEMANTIC_LOWER_INT),
            SafeRecoveryFieldCandidate(
                "queue.consumer_workers",
                2,
                SEMANTIC_LOWER_INT,
            ),
        ),
        low_risk_reason="only lowers explicit queue consumer pressure parameters",
        action_description="safe JSON config edit: reduce queue prefetch and inflight limits",
        local_success_message=(
            "已尝试应用队列背压修复：降低受控 prefetch / inflight / consumer 参数。"
        ),
        remote_success_message=(
            "已远程应用队列背压修复：降低受控 prefetch / inflight / consumer 参数。"
        ),
        remote_failure_message=(
            "远程队列背压修复失败：未找到受控队列参数字段。"
            "已检查 prefetch_count、max_inflight、consumer_workers、"
            "queue.prefetch_count、queue.max_inflight、queue.consumer_workers。"
        ),
    ),
    SafeRecoverySpec(
        event_type="worker_overload",
        issue_type="worker_overload",
        fix_id="fix-worker-1",
        relative_config_path="config.json",
        candidates=(
            SafeRecoveryFieldCandidate("worker_concurrency", 2, SEMANTIC_LOWER_INT),
            SafeRecoveryFieldCandidate("workers", 2, SEMANTIC_LOWER_INT),
            SafeRecoveryFieldCandidate("max_workers", 2, SEMANTIC_LOWER_INT),
            SafeRecoveryFieldCandidate("consumer_workers", 2, SEMANTIC_LOWER_INT),
            SafeRecoveryFieldCandidate("worker.concurrency", 2, SEMANTIC_LOWER_INT),
            SafeRecoveryFieldCandidate("server.workers", 2, SEMANTIC_LOWER_INT),
        ),
        low_risk_reason="only lowers an explicit worker concurrency JSON field",
        action_description="safe JSON config edit: reduce worker concurrency",
        local_success_message=(
            "已尝试应用 worker 过载修复：降低受控并发配置。"
        ),
        remote_success_message=(
            "已远程应用 worker 过载修复：降低受控并发配置。"
        ),
        remote_failure_message=(
            "远程 worker 过载修复失败：未找到受控并发字段。"
            "已检查 worker_concurrency、workers、max_workers、consumer_workers、"
            "worker.concurrency、server.workers。"
        ),
    ),
)


def _index_by_fix_id() -> dict[str, SafeRecoverySpec]:
    return {spec.fix_id: spec for spec in SAFE_RECOVERY_SPECS}


def _index_by_event_type() -> dict[str, SafeRecoverySpec]:
    return {spec.event_type: spec for spec in SAFE_RECOVERY_SPECS}


def _index_by_issue_type() -> dict[str, SafeRecoverySpec]:
    return {spec.issue_type: spec for spec in SAFE_RECOVERY_SPECS}


SAFE_RECOVERY_SPECS_BY_FIX_ID = _index_by_fix_id()
SAFE_RECOVERY_SPECS_BY_EVENT_TYPE = _index_by_event_type()
SAFE_RECOVERY_SPECS_BY_ISSUE_TYPE = _index_by_issue_type()

SAFE_RECOVERY_FIX_IDS = frozenset(SAFE_RECOVERY_SPECS_BY_FIX_ID)
SAFE_RECOVERY_EVENT_TYPES = frozenset(SAFE_RECOVERY_SPECS_BY_EVENT_TYPE)
SAFE_FIX_BY_EVENT_TYPE = {
    spec.event_type: spec.fix_id for spec in SAFE_RECOVERY_SPECS
}
SAFE_FIX_BY_ISSUE_TYPE = {
    spec.issue_type: spec.fix_id for spec in SAFE_RECOVERY_SPECS
}
SAFE_ACTION_DESCRIPTIONS = {
    spec.fix_id: spec.action_description for spec in SAFE_RECOVERY_SPECS
}


def iter_safe_recovery_specs() -> tuple[SafeRecoverySpec, ...]:
    return SAFE_RECOVERY_SPECS


def get_safe_recovery_spec_by_fix_id(fix_id: str) -> SafeRecoverySpec | None:
    return SAFE_RECOVERY_SPECS_BY_FIX_ID.get(fix_id)


def get_safe_recovery_spec_for_event_type(
    event_type: str,
) -> SafeRecoverySpec | None:
    return SAFE_RECOVERY_SPECS_BY_EVENT_TYPE.get(event_type)


def safe_fix_id_for_event_type(event_type: str) -> str:
    spec = get_safe_recovery_spec_for_event_type(event_type)
    return spec.fix_id if spec is not None else ""


def safe_fix_id_for_issue_type(issue_type: str) -> str:
    spec = SAFE_RECOVERY_SPECS_BY_ISSUE_TYPE.get(issue_type)
    return spec.fix_id if spec is not None else ""
