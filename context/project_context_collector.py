from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ConfigFileInfo:
    path: str
    file_type: str
    keys: list[str] = field(default_factory=list)
    preview: str = ""
    parsed_json: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProjectContext:
    project_dir: str
    run_command: str = ""
    project_type: str = "unknown"
    files_summary: list[str] = field(default_factory=list)
    config_files: list[ConfigFileInfo] = field(default_factory=list)
    dependency_files: list[str] = field(default_factory=list)
    log_files: list[str] = field(default_factory=list)
    output_dirs: list[str] = field(default_factory=list)
    detected_features: list[str] = field(default_factory=list)
    fix_hints: list[str] = field(default_factory=list)

    def to_markdown(self) -> str:
        lines = [
            "# Project Context",
            "",
            f"- project_dir: `{self.project_dir}`",
            f"- run_command: `{self.run_command if self.run_command else '<not set>'}`",
            f"- project_type: `{self.project_type}`",
            "",
            "## Detected Features",
        ]

        if self.detected_features:
            for item in self.detected_features:
                lines.append(f"- {item}")
        else:
            lines.append("- <empty>")

        lines.append("")
        lines.append("## Files Summary")
        if self.files_summary:
            for item in self.files_summary:
                lines.append(f"- {item}")
        else:
            lines.append("- <empty>")

        lines.append("")
        lines.append("## Config Files")
        if self.config_files:
            for cfg in self.config_files:
                lines.append(f"### {cfg.path}")
                lines.append(f"- type: `{cfg.file_type}`")
                lines.append(f"- keys: `{cfg.keys}`")
                if cfg.preview:
                    lines.append("")
                    lines.append("```text")
                    lines.append(cfg.preview)
                    lines.append("```")
        else:
            lines.append("- <empty>")

        lines.append("")
        lines.append("## Dependency Files")
        if self.dependency_files:
            for item in self.dependency_files:
                lines.append(f"- {item}")
        else:
            lines.append("- <empty>")

        lines.append("")
        lines.append("## Log Files")
        if self.log_files:
            for item in self.log_files:
                lines.append(f"- {item}")
        else:
            lines.append("- <empty>")

        lines.append("")
        lines.append("## Output Directories")
        if self.output_dirs:
            for item in self.output_dirs:
                lines.append(f"- {item}")
        else:
            lines.append("- <empty>")

        lines.append("")
        lines.append("## Fix Hints")
        if self.fix_hints:
            for item in self.fix_hints:
                lines.append(f"- {item}")
        else:
            lines.append("- <empty>")

        return "\n".join(lines)


class ProjectContextCollector:
    """
    Read-only project context scanner.

    It scans a project directory and extracts useful context for troubleshooting:
    - config files
    - dependency files
    - log files
    - output dirs
    - key config values
    - possible fix hints
    """

    DEFAULT_IGNORE_DIRS = {
        ".git",
        ".venv",
        "venv",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        "node_modules",
        "dist",
        "build",
        ".idea",
        ".vscode",
    }

    CONFIG_SUFFIXES = {".json", ".yaml", ".yml", ".toml", ".env"}
    DEPENDENCY_NAMES = {
        "requirements.txt",
        "pyproject.toml",
        "setup.py",
        "environment.yml",
        "environment.yaml",
        "Pipfile",
        "poetry.lock",
    }
    LOG_SUFFIXES = {".log", ".out", ".err"}
    OUTPUT_DIR_NAMES = {"outputs", "output", "runs", "logs", "checkpoints"}

    def __init__(
        self,
        project_dir: str,
        run_command: str = "",
        max_files: int = 80,
        max_preview_chars: int = 2000,
    ) -> None:
        self.project_dir = Path(project_dir).expanduser().resolve()
        self.run_command = run_command
        self.max_files = max_files
        self.max_preview_chars = max_preview_chars

    def collect(self) -> ProjectContext:
        if not self.project_dir.exists() or not self.project_dir.is_dir():
            return ProjectContext(
                project_dir=str(self.project_dir),
                run_command=self.run_command,
                project_type="invalid_project_dir",
                fix_hints=["项目目录不存在或不是目录。"],
            )

        files = self._walk_files()

        context = ProjectContext(
            project_dir=str(self.project_dir),
            run_command=self.run_command,
            project_type=self._infer_project_type(files),
        )

        context.files_summary = self._summarize_files(files)
        context.config_files = self._collect_config_files(files)
        context.dependency_files = self._collect_dependency_files(files)
        context.log_files = self._collect_log_files(files)
        context.output_dirs = self._collect_output_dirs()
        context.detected_features = self._detect_features(context)
        context.fix_hints = self._build_fix_hints(context)

        return context

    def _walk_files(self) -> list[Path]:
        result: list[Path] = []

        for root, dirs, files in os.walk(self.project_dir):
            dirs[:] = [d for d in dirs if d not in self.DEFAULT_IGNORE_DIRS]

            root_path = Path(root)
            for name in files:
                path = root_path / name
                result.append(path)

                if len(result) >= self.max_files:
                    return result

        return result

    def _relative(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.project_dir))
        except ValueError:
            return str(path)

    def _summarize_files(self, files: list[Path]) -> list[str]:
        summary = []

        for path in files[: self.max_files]:
            rel = self._relative(path)
            try:
                size = path.stat().st_size
            except OSError:
                size = -1
            summary.append(f"{rel} ({size} bytes)")

        return summary

    def _infer_project_type(self, files: list[Path]) -> str:
        names = {path.name for path in files}

        if "pyproject.toml" in names or "requirements.txt" in names or any(p.suffix == ".py" for p in files):
            return "python_project"

        if "package.json" in names:
            return "node_project"

        if "pom.xml" in names or "build.gradle" in names:
            return "java_project"

        return "unknown"

    def _is_under_output_dir(self, path: Path) -> bool:
        try:
            rel_parts = path.relative_to(self.project_dir).parts
        except ValueError:
            return False

        return any(part in self.OUTPUT_DIR_NAMES for part in rel_parts[:-1])

    def _collect_config_files(self, files: list[Path]) -> list[ConfigFileInfo]:
        configs: list[ConfigFileInfo] = []

        for path in files:
            if path.suffix.lower() not in self.CONFIG_SUFFIXES and path.name not in {"config.json", ".env"}:
                continue

            rel = self._relative(path)
            preview = self._safe_preview(path)

            if self._is_under_output_dir(path):
                continue

            if path.suffix.lower() == ".json":
                parsed = self._safe_load_json(path)
                keys = list(parsed.keys()) if isinstance(parsed, dict) else []
                configs.append(
                    ConfigFileInfo(
                        path=rel,
                        file_type="json",
                        keys=keys,
                        preview=preview,
                        parsed_json=parsed if isinstance(parsed, dict) else {},
                    )
                )
            else:
                configs.append(
                    ConfigFileInfo(
                        path=rel,
                        file_type=path.suffix.lower().lstrip(".") or "env",
                        keys=[],
                        preview=preview,
                    )
                )

        return configs

    def _collect_dependency_files(self, files: list[Path]) -> list[str]:
        deps = []
        for path in files:
            if path.name in self.DEPENDENCY_NAMES:
                deps.append(self._relative(path))
        return deps

    def _collect_log_files(self, files: list[Path]) -> list[str]:
        logs = []
        for path in files:
            if path.suffix.lower() in self.LOG_SUFFIXES:
                logs.append(self._relative(path))
        return logs

    def _collect_output_dirs(self) -> list[str]:
        candidates = ["outputs", "logs", "runs", "checkpoints", "output", "tmp"]
        found = []

        for name in candidates:
            path = self.project_dir / name
            if path.exists() and path.is_dir():
                found.append(name)

        return found

    def _detect_features(self, context: ProjectContext) -> list[str]:
        features: list[str] = []

        if context.project_type == "python_project":
            features.append("Python project detected.")

        if context.dependency_files:
            features.append("Dependency file detected.")

        for cfg in context.config_files:
            data = cfg.parsed_json

            if "metrics_port" in data:
                features.append(f"Metrics port config detected: {cfg.path} metrics_port={data.get('metrics_port')}")

            if "batch_size" in data:
                features.append(f"Training batch size config detected: {cfg.path} batch_size={data.get('batch_size')}")

            if "precision" in data:
                features.append(f"Training precision config detected: {cfg.path} precision={data.get('precision')}")

            if "gradient_checkpointing" in data:
                features.append(
                    f"Gradient checkpointing config detected: {cfg.path} "
                    f"gradient_checkpointing={data.get('gradient_checkpointing')}"
                )

            if "cache_dir" in data:
                features.append(f"Cache dir config detected: {cfg.path} cache_dir={data.get('cache_dir')}")

            if "simulate_disk_full" in data:
                features.append(f"Disk simulation flag detected: {cfg.path} simulate_disk_full={data.get('simulate_disk_full')}")

            if "simulate_python_env_mismatch" in data:
                features.append(
                    f"Python env simulation flag detected: {cfg.path} "
                    f"simulate_python_env_mismatch={data.get('simulate_python_env_mismatch')}"
                )

        return features

    def _build_fix_hints(self, context: ProjectContext) -> list[str]:
        hints: list[str] = []

        for cfg in context.config_files:
            data = cfg.parsed_json

            if "metrics_port" in data:
                port = data.get("metrics_port")
                hints.append(
                    f"If network_port is the primary issue, consider editing {cfg.path}: metrics_port {port} -> 9101."
                )

            if "batch_size" in data:
                batch_size = data.get("batch_size")
                hints.append(
                    f"If GPU OOM is the primary issue, consider editing {cfg.path}: batch_size {batch_size} -> 4 or 8."
                )

            if "precision" in data:
                hints.append(
                    f"If GPU memory pressure remains high, consider editing {cfg.path}: precision -> bf16/fp16 if supported."
                )

            if "gradient_checkpointing" in data:
                hints.append(
                    f"If GPU memory pressure remains high, consider editing {cfg.path}: gradient_checkpointing -> true if supported."
                )

            if "cache_dir" in data:
                hints.append(
                    f"If disk/cache issue is relevant, check or edit {cfg.path}: cache_dir={data.get('cache_dir')}."
                )

            if "simulate_disk_full" in data:
                hints.append(
                    f"For this demo project, disk fallback can be disabled by editing {cfg.path}: simulate_disk_full -> false."
                )

            if "simulate_python_env_mismatch" in data:
                hints.append(
                    f"For this demo project, python env warning can be disabled by editing {cfg.path}: simulate_python_env_mismatch -> false."
                )

        return hints

    def _safe_preview(self, path: Path) -> str:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception as exc:
            return f"<failed to read: {type(exc).__name__}: {exc}>"

        if len(text) > self.max_preview_chars:
            return text[: self.max_preview_chars] + "\n[PREVIEW_TRUNCATED]"
        return text

    @staticmethod
    def _safe_load_json(path: Path) -> dict[str, Any]:
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}