"""Read-only aliases for WSL1-managed outputs recorded under a shared Windows state.

Never persist access aliases or use this function to authorize image removal.
Native paths and existing verified Windows-to-WSL mapping retain precedence.
"""

import os
import re
import stat
from pathlib import Path

from dtflow.services.artifact_paths import ArtifactReadPaths, foreign_windows_path
from dtflow.services.managed_original_provenance import (
    ensure_unredirected_path,
    matches_managed_state,
    verify_managed_sidecar,
)

_ATTEMPT = re.compile(r"[0-9a-f]{32}\Z")


def _managed_candidate(state: Path, row: dict) -> tuple[Path, Path]:
    attempt, ordinal, identity = row["attempt_id"], row["ordinal"], row["identity"]
    if (
        not isinstance(attempt, str)
        or not _ATTEMPT.fullmatch(attempt)
        or type(ordinal) is not int
        or ordinal < 0
        or identity != f"{attempt}:{ordinal}"
    ):
        raise OSError("Recorded output identity is inconsistent.")
    tail = ("outputs", attempt)
    if not matches_managed_state(state, row["path"], (*tail, f"final-{ordinal + 1}.png")) or not matches_managed_state(
        state, row["metadata_path"], (*tail, "metadata.json")
    ):
        raise OSError("Recorded output is not under this shared Windows state.")
    root = state.absolute()
    image = root.joinpath(*tail, f"final-{ordinal + 1}.png")
    sidecar = root.joinpath(*tail, "metadata.json")
    try:
        for component in (root, root / "outputs", image.parent, image, sidecar):
            ensure_unredirected_path(component)
        if root.resolve(strict=True) != root:
            raise OSError
        if image.resolve(strict=True) != image or sidecar.resolve(strict=True) != sidecar:
            raise OSError
        if not image.is_relative_to(root / "outputs"):
            raise OSError
        if any(not stat.S_ISREG(p.stat().st_mode) for p in (image, sidecar)):
            raise OSError
    except (OSError, ValueError) as exc:
        raise OSError("Managed output or sidecar is missing or redirected.") from exc
    return image, sidecar


def resolve_output_read(
    state: Path, row: dict, *, verify: bool = False, paths: ArtifactReadPaths | None = None, display: bool = False
) -> tuple[str, str, bool]:
    """Return image, sidecar and whether a state-derived read-only alias was used.

    ``display`` lets thumbnails reuse a digest already proven for an unchanged file.
    """
    paths = paths or ArtifactReadPaths.discover()
    try:
        native = paths.resolve(row["path"])
        if Path(native).is_file():
            if verify and foreign_windows_path(row["path"]):
                # Verified WSL drive mappings permit reading, but not trusting
                # an unrelated file or metadata under the recorded path.
                metadata = paths.resolve(row["metadata_path"])
                verify_managed_sidecar(Path(native), Path(metadata), row, display=display)
                if paths.resolve(row["path"]) != native or paths.resolve(row["metadata_path"]) != metadata:
                    raise OSError("Recorded Windows output mapping changed during verification.")
            else:
                try:
                    metadata = paths.resolve(row["metadata_path"])
                except OSError:
                    metadata = row["metadata_path"]
            return native, metadata, False
    except OSError:
        if verify and foreign_windows_path(row.get("path", "")):
            raise
    if os.name != "nt" or not isinstance(row.get("path"), str) or not row["path"].startswith("/"):
        raise OSError("Recorded output image is missing or its path is unsupported.")
    image, sidecar = _managed_candidate(Path(state), row)
    if verify:
        verify_managed_sidecar(image, sidecar, row, display=display)
        _managed_candidate(Path(state), row)
    return str(image), str(sidecar), True
