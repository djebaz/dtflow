"""Centralized runtime, persistent, cache, run, log, and shallow output paths."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from .config import JobConfig


@dataclass(frozen=True)
class ProjectPaths:
    project_root: Path
    runtime_root: Path
    persistent_root: Path
    output_root: Path
    global_log_root: Path
    persistent_log_root: Path
    state_root: Path
    cache_root: Path
    runs_root: Path
    exports_root: Path

    @classmethod
    def create(cls, project_root: Path, config: JobConfig | None = None) -> "ProjectPaths":
        config = config or JobConfig()
        runtime_root = Path(os.environ.get("COLAB_RUNTIME_ROOT", config.runtime_root)).expanduser()
        persistent_root = Path(
            os.environ.get("COLAB_PERSISTENT_ROOT", config.persistent_root)
        ).expanduser()
        output_root = Path(os.environ.get("COLAB_OUTPUT_ROOT", "/content/outputs")).expanduser()
        paths = cls(
            project_root=project_root,
            runtime_root=runtime_root,
            persistent_root=persistent_root,
            output_root=output_root,
            global_log_root=runtime_root / "logs",
            persistent_log_root=persistent_root / "logs",
            state_root=persistent_root / "state",
            cache_root=persistent_root / "cache",
            runs_root=persistent_root / "runs",
            exports_root=persistent_root / "exports",
        )
        for directory in (
            paths.runtime_root,
            paths.persistent_root,
            paths.output_root,
            paths.global_log_root,
            paths.persistent_log_root,
            paths.state_root,
            paths.cache_root,
            paths.runs_root,
            paths.exports_root,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        return paths
