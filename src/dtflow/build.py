"""One version/build identity, without requiring Git at runtime."""

from datetime import UTC, datetime
from functools import cache
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

# A packaged build has a stamped identity: tools/packaging/build_exe.py generates the ignored dtflow/_buildinfo.py
# module (full commit, clean/dirty), so a frozen executable needs no Git. A source run has no stamp; it is a "dev"
# build whose revision is the newest modification time among the package's own files.
SOURCE_COMMIT = "unknown"
SOURCE_STATUS = "unknown"
STAMPED = False
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
    return build


def display_identity() -> str:
    build = identity()
    if build["source_commit"] != "unknown":
        revision = "g" + build["source_commit"][:7]
    elif "source_modified" in build:
        local = datetime.fromisoformat(build["source_modified"].replace("Z", "+00:00")).astimezone()
        revision = "revision " + local.strftime("%Y-%m-%d %H:%M")
    else:
        revision = "revision unknown"
    return f"DTFlow {build['version']} ({revision}; source status {build['source_status']})"
