"""One version/build identity, without requiring Git at runtime."""

import re
import subprocess
import sys
from datetime import UTC, datetime
from functools import cache
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

# A packaged build has a stamped identity: tools/packaging/build_exe.py generates the ignored dtflow/_buildinfo.py
# module (full commit, clean/dirty), so a frozen executable needs no Git. Source CLI development startup may
# resolve the checkout identity explicitly; otherwise the newest package-file modification time is the fallback.
SOURCE_COMMIT = "unknown"
SOURCE_STATUS = "unknown"
STAMPED = False
_checkout_identity = ("unknown", "unknown")
try:
    from dtflow._buildinfo import SOURCE_COMMIT, SOURCE_STATUS  # type: ignore[import-not-found]  # noqa: F811

    STAMPED = True
except ImportError:
    pass

PACKAGE_DIR = Path(__file__).parent
REVISION_SUFFIXES = frozenset({".py", ".qss", ".json", ".ui"})


@cache
def source_modified() -> str | None:
    """UTC minute of the newest package source/resource file, or None when it cannot be read (about 0.3 ms)."""
    newest = 0
    try:
        for path in PACKAGE_DIR.rglob("*"):
            if path.suffix in REVISION_SUFFIXES and "__pycache__" not in path.parts:
                newest = max(newest, path.stat().st_mtime_ns)
    except OSError:
        return None
    if not newest:
        return None
    return datetime.fromtimestamp(newest / 1e9, UTC).strftime("%Y-%m-%dT%H:%MZ")


def _checkout_revision() -> tuple[str, str]:
    """Resolve a source checkout once, without making Git a runtime requirement."""
    if STAMPED or getattr(sys, "frozen", False):
        return "unknown", "unknown"
    try:
        root = PACKAGE_DIR.resolve().parents[1]
        if not (root / ".git").exists():
            return "unknown", "unknown"
    except (OSError, IndexError):
        return "unknown", "unknown"

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            check=True,
            capture_output=True,
            text=True,
            timeout=1,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout.strip()

    try:
        commit = git("rev-parse", "--verify", "HEAD")
    except (OSError, subprocess.SubprocessError, UnicodeError):
        return "unknown", "unknown"
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit):
        return "unknown", "unknown"
    try:
        status = "dirty" if git("status", "--porcelain", "--untracked-files=no") else "clean"
    except (OSError, subprocess.SubprocessError, UnicodeError):
        status = "unknown"
    return commit, status


def set_checkout_identity(enabled: bool) -> None:
    """Resolve once at explicit CLI startup, or clear the session snapshot without Git."""
    global _checkout_identity
    _checkout_identity = _checkout_revision() if enabled else ("unknown", "unknown")


def identity() -> dict[str, str]:
    try:
        application_version = version("dtflow")
    except PackageNotFoundError:
        application_version = "unknown"
    build = {"version": application_version, "source_commit": SOURCE_COMMIT, "source_status": SOURCE_STATUS}
    if not STAMPED:
        modified = source_modified()
        build["source_status"] = "dev" if modified else SOURCE_STATUS
        if modified:
            build["source_modified"] = modified
    if not STAMPED:
        commit, status = _checkout_identity
        if commit != "unknown":
            build["source_commit"] = commit
            build["source_status"] = status
    return build


def display_identity() -> str:
    build = identity()
    if build["source_commit"] != "unknown":
        revision = build["source_commit"][:7]
    elif "source_modified" in build:
        local = datetime.fromisoformat(build["source_modified"].replace("Z", "+00:00")).astimezone()
        revision = local.strftime("%Y-%m-%d %H:%M")
    else:
        revision = "unknown"
    return f"DTFlow {build['version']} | revision {revision} | source: {build['source_status']}"
