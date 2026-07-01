__all__ = [
    "AutoRecoveryRunner",
    "AutoRecoveryResult",
]


def __getattr__(name: str):
    if name in __all__:
        from .auto_recovery_runner import AutoRecoveryResult, AutoRecoveryRunner

        return {
            "AutoRecoveryRunner": AutoRecoveryRunner,
            "AutoRecoveryResult": AutoRecoveryResult,
        }[name]

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
