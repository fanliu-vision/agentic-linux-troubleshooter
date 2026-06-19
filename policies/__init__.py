from __future__ import annotations

from typing import Any

__all__ = [
    "RemediationPolicy",
    "RemediationDecision",
]


def __getattr__(name: str) -> Any:
    if name in __all__:
        from .remediation_policy import RemediationDecision, RemediationPolicy

        exports = {
            "RemediationPolicy": RemediationPolicy,
            "RemediationDecision": RemediationDecision,
        }
        globals().update(exports)
        return exports[name]

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
