"""Shallow output allocation, persistent copies, previews, and resilient downloads."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
import zipfile


def allocate_output_path(output_root: Path, output_name: str) -> Path:
    output_root.mkdir(parents=True, exist_ok=True)
    requested = output_root / output_name
    if not requested.exists():
        return requested
    stem, suffix = requested.stem, requested.suffix
    index = 2
    while True:
        candidate = output_root / f"{stem}-{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def create_minimal_bundle(
    output_path: Path,
    manifest_path: Path,
    submitted_job_path: Path,
    run_log_path: Path,
) -> Path:
    bundle_path = allocate_output_path(
        output_path.parent,
        f"{output_path.stem}-minimal.zip",
    )
    temporary = bundle_path.with_suffix(bundle_path.suffix + ".tmp")
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, archive_name in (
            (output_path, output_path.name),
            (manifest_path, "run_manifest.json"),
            (submitted_job_path, "submitted_job.json"),
            (run_log_path, "logs/full_run.log"),
        ):
            archive.write(path, arcname=archive_name)
    temporary.replace(bundle_path)
    return bundle_path


def _show_file_link(path: Path, label: str) -> None:
    print(f"{label}: {path}")
    if not path.is_file():
        return
    try:
        from IPython.display import FileLink, display

        display(FileLink(str(path), result_html_prefix=f"{label}: "))
    except Exception as exc:
        print(f"{label} link unavailable in this frontend: {exc}")


def show_result_panel(run_result: dict[str, Any], *, auto_download: bool) -> dict[str, Any]:
    output_path = Path(run_result["output_path"])
    print("RUN COMPLETE")
    _show_file_link(output_path, "Final output")
    if run_result.get("persistent_output_path"):
        print(f"Persistent copy: {run_result['persistent_output_path']}")
    if run_result.get("minimal_bundle_path"):
        _show_file_link(Path(run_result["minimal_bundle_path"]), "Minimal bundle")
    _show_file_link(Path(run_result["manifest_path"]), "Run manifest")
    _show_file_link(Path(run_result["run_log"]), "Full run log")

    manual_link = str(output_path)

    if output_path.suffix.lower() == ".json" and output_path.stat().st_size <= 1_000_000:
        try:
            preview = json.loads(output_path.read_text(encoding="utf-8"))
            print("Preview:")
            print(json.dumps(preview, indent=2)[:4000])
        except (OSError, ValueError):
            pass

    download_status = "disabled"
    if auto_download:
        try:
            from google.colab import files  # type: ignore[import-not-found]

            files.download(str(output_path))
            download_status = "requested"
        except Exception as exc:
            download_status = "blocked"
            print(f"Automatic download could not start; the saved result is still valid: {exc}")
    return {"manual_link": manual_link, "automatic_download": download_status}
