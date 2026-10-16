"""Fresh-runtime bootstrap with fingerprinted readiness and dependency delta installs."""

from __future__ import annotations

import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import re
import shutil
import sys
from typing import Any

from .config import JobConfig, atomic_write_json, utc_now
from .logging_utils import run_logged_command
from .paths import ProjectPaths

_NAME_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.-]*)")


def is_colab_runtime() -> bool:
    try:
        import google.colab  # type: ignore[import-not-found]  # noqa: F401
    except ImportError:
        return False
    return True


def environment_fingerprint(project_root: Path, config: JobConfig) -> tuple[str, dict[str, Any]]:
    lock_path = project_root / "requirements.lock"
    lock_bytes = lock_path.read_bytes() if lock_path.exists() else b""
    payload = {
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "runtime_profile": "default",
        "project_revision": os.environ.get(
            "COLAB_PROJECT_RESOLVED_REVISION", config.project_revision
        ),
        "dependency_lock_sha256": hashlib.sha256(lock_bytes).hexdigest(),
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]
    return digest, payload


def _requirement_is_satisfied(requirement: str) -> bool:
    match = _NAME_RE.match(requirement)
    if not match:
        raise ValueError(f"Unsupported requirement line: {requirement!r}")
    name = match.group(1)
    try:
        installed = metadata.version(name)
    except metadata.PackageNotFoundError:
        return False
    try:
        from packaging.requirements import Requirement

        parsed = Requirement(requirement)
        return not parsed.specifier or parsed.specifier.contains(installed, prereleases=True)
    except ImportError:
        exact = requirement.split("==", 1)
        if len(exact) == 1:
            raise RuntimeError(
                "The packaging package is required to evaluate non-exact dependency constraints"
            )
        return installed == exact[1].strip()


def dependency_delta(lock_path: Path) -> list[str]:
    if not lock_path.exists():
        return []
    requirements = []
    for raw in lock_path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            requirements.append(line)
    return [requirement for requirement in requirements if not _requirement_is_satisfied(requirement)]


def prepare_runtime(paths: ProjectPaths, config: JobConfig, logger) -> dict[str, Any]:
    fingerprint, fingerprint_payload = environment_fingerprint(paths.project_root, config)
    marker = paths.runtime_root / f".production_colab_{fingerprint}.ready"
    if marker.exists():
        try:
            marker_payload = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            marker_payload = None
        if isinstance(marker_payload, dict) and marker_payload.get("fingerprint") == fingerprint:
            logger.info("STAGE REUSED: PREPARE_RUNTIME fingerprint=%s", fingerprint)
            return {"fingerprint": fingerprint, "reused": True, "installed": []}
        logger.warning("Ignoring invalid runtime-ready marker: %s", marker)

    logger.info("STAGE START: PREPARE_RUNTIME fingerprint=%s", fingerprint)
    shutil.rmtree("/content/sample_data", ignore_errors=True)
    if is_colab_runtime() and shutil.which("nvtop") is None:
        run_logged_command(["apt-get", "install", "-y", "nvtop"], logger)

    missing = dependency_delta(paths.project_root / "requirements.lock")
    if missing:
        logger.info("Installing missing or incompatible dependencies: %s", ", ".join(missing))
        run_logged_command([sys.executable, "-m", "pip", "install", *missing], logger)
    else:
        logger.info("Python dependency delta: none")

    manifest = {
        **fingerprint_payload,
        "fingerprint": fingerprint,
        "prepared_utc": utc_now(),
        "platform": platform.platform(),
        "installed_delta": missing,
    }
    atomic_write_json(paths.state_root / "environment_manifest.json", manifest)
    atomic_write_json(marker, manifest)
    logger.info("STAGE COMPLETE: PREPARE_RUNTIME installed=%s", len(missing))
    return {"fingerprint": fingerprint, "reused": False, "installed": missing}
