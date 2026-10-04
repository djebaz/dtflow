"""One version/build identity, without requiring Git at runtime."""

from importlib.metadata import PackageNotFoundError, version

# A later packaging step replaces these with its clean source identity.
SOURCE_COMMIT = "unknown"
SOURCE_STATUS = "unknown"


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
