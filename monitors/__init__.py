from __future__ import annotations

from typing import Any

__all__ = [
    "ProjectRegistry",
    "ProjectConfig",
    "MonitorLoop",
    "MonitorRunResult",
    "LocalLogWatcher",
    "RemoteLogWatcher",
    "WatchedLogChunk",
]


def __getattr__(name: str) -> Any:
    if name in {"ProjectRegistry", "ProjectConfig"}:
        from .project_registry import ProjectConfig, ProjectRegistry

        exports = {
            "ProjectRegistry": ProjectRegistry,
            "ProjectConfig": ProjectConfig,
        }
        globals().update(exports)
        return exports[name]

    if name in {"MonitorLoop", "MonitorRunResult"}:
        from .monitor_loop import MonitorLoop, MonitorRunResult

        exports = {
            "MonitorLoop": MonitorLoop,
            "MonitorRunResult": MonitorRunResult,
        }
        globals().update(exports)
        return exports[name]

    if name in {"LocalLogWatcher", "RemoteLogWatcher", "WatchedLogChunk"}:
        from .log_watcher import LocalLogWatcher, RemoteLogWatcher, WatchedLogChunk

        exports = {
            "LocalLogWatcher": LocalLogWatcher,
            "RemoteLogWatcher": RemoteLogWatcher,
            "WatchedLogChunk": WatchedLogChunk,
        }
        globals().update(exports)
        return exports[name]

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
