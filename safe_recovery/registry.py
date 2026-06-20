from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SafeRecoveryFieldCandidate:
    field_path: str
    new_value: Any


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
        candidates=(SafeRecoveryFieldCandidate("metrics_port", 9101),),
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
            SafeRecoveryFieldCandidate("batch_size", 4),
            SafeRecoveryFieldCandidate("train_batch_size", 4),
            SafeRecoveryFieldCandidate("per_device_train_batch_size", 4),
            SafeRecoveryFieldCandidate("samples_per_gpu", 4),
            SafeRecoveryFieldCandidate("training.batch_size", 4),
            SafeRecoveryFieldCandidate("model.batch_size", 4),
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
            SafeRecoveryFieldCandidate("cache_enabled", False),
            SafeRecoveryFieldCandidate("feature_cache_enabled", False),
            SafeRecoveryFieldCandidate("cache.write_enabled", False),
            SafeRecoveryFieldCandidate("simulate_cache_write_failed", False),
            SafeRecoveryFieldCandidate("simulate_disk_full", False),
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
            SafeRecoveryFieldCandidate("optional_dependency_enabled", False),
            SafeRecoveryFieldCandidate(
                "optional_dependencies.internal_risk_sdk.enabled",
                False,
            ),
            SafeRecoveryFieldCandidate("plugins.internal_risk_sdk.enabled", False),
            SafeRecoveryFieldCandidate("risk_sdk_enabled", False),
            SafeRecoveryFieldCandidate("simulate_python_env_mismatch", False),
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
        event_type="worker_overload",
        issue_type="worker_overload",
        fix_id="fix-worker-1",
        relative_config_path="config.json",
        candidates=(
            SafeRecoveryFieldCandidate("worker_concurrency", 2),
            SafeRecoveryFieldCandidate("workers", 2),
            SafeRecoveryFieldCandidate("max_workers", 2),
            SafeRecoveryFieldCandidate("consumer_workers", 2),
            SafeRecoveryFieldCandidate("worker.concurrency", 2),
            SafeRecoveryFieldCandidate("server.workers", 2),
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
