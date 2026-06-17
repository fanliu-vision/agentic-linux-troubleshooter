from __future__ import annotations

from datetime import datetime
from pathlib import Path


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class DaemonLogger:
    """
    Stage 6E daemon.log 简单日志器。

    不替代 logging 模块，只做最小可靠落盘：
    - 控制台输出
    - daemon.log 追加写入
    """

    def __init__(self, log_path: str, echo: bool = True) -> None:
        self.log_path = Path(log_path)
        self.echo = echo
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def info(self, message: str) -> None:
        self._write("INFO", message)

    def warning(self, message: str) -> None:
        self._write("WARN", message)

    def error(self, message: str) -> None:
        self._write("ERROR", message)

    def heartbeat(self, message: str) -> None:
        self._write("HEARTBEAT", message)

    def _write(self, level: str, message: str) -> None:
        line = f"{now_text()} [{level}] {message}"
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

        if self.echo:
            print(line)