from __future__ import annotations

from typing import Any


SEMANTIC_DISABLE_BOOL = "disable_bool"
SEMANTIC_LOWER_INT = "lower_int"
SEMANTIC_PORT_AVAILABLE = "port_available"
SEMANTIC_SAFE_ENUM_DOWNGRADE = "safe_enum_downgrade"
SEMANTIC_SET_LITERAL = "set_literal"

SAFE_ENUM_DOWNGRADE_VALUES = frozenset({"memory", "local", "file", "console"})


def evaluate_safe_transition(
    *,
    semantic_rule: str,
    old_value: Any,
    new_value: Any,
    port_available: bool | None = None,
) -> dict[str, Any]:
    if old_value == new_value:
        return _result(
            semantic_rule=semantic_rule,
            status="no_op",
            safe=True,
            actionable=False,
            no_op=True,
            reason="already_target_value",
        )

    if semantic_rule == SEMANTIC_DISABLE_BOOL:
        if old_value is True and new_value is False:
            return _result(
                semantic_rule=semantic_rule,
                status="actionable",
                safe=True,
                actionable=True,
                reason="boolean_true_to_false",
            )
        return _result(
            semantic_rule=semantic_rule,
            status="unsafe",
            reason="boolean_disable_requires_true_to_false",
        )

    if semantic_rule == SEMANTIC_LOWER_INT:
        if not _is_int_value(old_value) or not _is_int_value(new_value):
            return _result(
                semantic_rule=semantic_rule,
                status="unsafe",
                reason="integer_lowering_requires_int_values",
            )
        if old_value > new_value:
            return _result(
                semantic_rule=semantic_rule,
                status="actionable",
                safe=True,
                actionable=True,
                reason="integer_value_will_decrease",
            )
        return _result(
            semantic_rule=semantic_rule,
            status="unsafe",
            reason="integer_value_would_not_decrease",
        )

    if semantic_rule == SEMANTIC_PORT_AVAILABLE:
        if not _is_int_value(new_value):
            return _result(
                semantic_rule=semantic_rule,
                status="unsafe",
                reason="port_target_requires_int_value",
            )
        if port_available is None:
            return _result(
                semantic_rule=semantic_rule,
                status="deferred",
                safe=True,
                actionable=True,
                reason="port_availability_check_deferred",
            )
        if port_available:
            return _result(
                semantic_rule=semantic_rule,
                status="actionable",
                safe=True,
                actionable=True,
                reason="target_port_available",
            )
        return _result(
            semantic_rule=semantic_rule,
            status="unsafe",
            reason="target_port_not_available",
        )

    if semantic_rule == SEMANTIC_SAFE_ENUM_DOWNGRADE:
        if not isinstance(old_value, str) or not isinstance(new_value, str):
            return _result(
                semantic_rule=semantic_rule,
                status="unsafe",
                reason="safe_enum_downgrade_requires_string_values",
            )

        normalized_target = _normalize_enum_value(new_value)
        if normalized_target not in SAFE_ENUM_DOWNGRADE_VALUES:
            return _result(
                semantic_rule=semantic_rule,
                status="unsafe",
                reason="safe_enum_target_not_allowlisted",
            )

        return _result(
            semantic_rule=semantic_rule,
            status="actionable",
            safe=True,
            actionable=True,
            reason="safe_enum_downgrade_target_allowlisted",
        )

    return _result(
        semantic_rule=semantic_rule or SEMANTIC_SET_LITERAL,
        status="actionable",
        safe=True,
        actionable=True,
        reason="literal_set_allowed",
    )


def deferred_transition(semantic_rule: str) -> dict[str, Any]:
    return _result(
        semantic_rule=semantic_rule,
        status="deferred",
        safe=True,
        actionable=True,
        reason="semantic_check_deferred",
    )


def _result(
    *,
    semantic_rule: str,
    status: str,
    reason: str,
    safe: bool = False,
    actionable: bool = False,
    no_op: bool = False,
) -> dict[str, Any]:
    return {
        "semantic_rule": semantic_rule,
        "semantic_status": status,
        "semantic_safe": safe,
        "actionable": actionable,
        "no_op": no_op,
        "semantic_reason": reason,
    }


def _is_int_value(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _normalize_enum_value(value: str) -> str:
    return " ".join(value.strip().lower().split())
