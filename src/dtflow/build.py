"""One version/build identity, without requiring Git at runtime."""

from importlib.metadata import PackageNotFoundError, version

# Source runs have no stamped identity. tools/packaging/build_exe.py generates the ignored
# dtflow/_buildinfo.py module (full commit, clean/dirty) so a frozen build needs no Git at runtime.
SOURCE_COMMIT = "unknown"
SOURCE_STATUS = "unknown"
try:
    from dtflow._buildinfo import SOURCE_COMMIT, SOURCE_STATUS  # type: ignore[import-not-found]  # noqa: F811
except ImportError:
    pass


def identity() -> dict[str, str]:
    try:
        application_version = version("dtflow")
    except PackageNotFoundError:
        application_version = "unknown"
    return {"version": application_version, "source_commit": SOURCE_COMMIT, "source_status": SOURCE_STATUS}


def display_identity() -> str:
    build = identity()
    revision = "g" + build["source_commit"][:7] if build["source_commit"] != "unknown" else "revision unknown"
    return f"DTFlow {build['version']} ({revision}; source status {build['source_status']})"
