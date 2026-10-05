"""Verify the canonical Windows build and write release checksums/manifest, without launching it."""

import argparse
import hashlib
import json
import os
import platform
import subprocess
from email.parser import BytesParser
from importlib.metadata import version
from pathlib import Path

from PyInstaller.archive.readers import CArchiveReader

ROOT = Path(__file__).resolve().parents[2]


def verify(commit: str, tag: str) -> dict:
    actual_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if actual_commit != commit:
        raise ValueError("Checkout no longer matches the selected source commit.")
    source_version = version("dtflow")
    if tag and tag != f"v{source_version}":
        raise ValueError("Release tag does not match the installed application version.")
    artifact = ROOT / "dist" / f"dtflow-{source_version}-g{commit[:7]}-windows-x64.exe"
    if not artifact.is_file() or artifact.stat().st_size == 0:
        raise ValueError("Expected stamped Windows x64 executable is missing or empty.")
    archive = CArchiveReader(str(artifact))
    pyz_name = next(name for name, entry in archive.toc.items() if entry[-1] == "z")
    stamp = archive.open_embedded_archive(pyz_name).extract("dtflow._buildinfo")
    # Read the code object's constants, never execute bundled application code.
    if commit not in stamp.co_consts or "clean" not in stamp.co_consts or "dirty" in stamp.co_consts:
        raise ValueError("Bundled source identity is not the selected clean commit.")
    metadata_name = next(
        name for name in archive.toc if f"dtflow-{source_version}.dist-info" in name and name.endswith("METADATA")
    )
    if BytesParser().parsebytes(archive.extract(metadata_name))["Version"] != source_version:
        raise ValueError("Bundled package version does not match the source.")
    with artifact.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {
        "artifact": artifact.name,
        "version": source_version,
        "source_commit": commit,
        "source_status": "clean",
        "size_bytes": artifact.stat().st_size,
        "sha256": digest,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "pyinstaller": version("pyinstaller"),
        "uv": subprocess.check_output(["uv", "--version"], text=True).strip(),
        "release_tag": tag or None,
        "source_pr": os.environ.get("DTFLOW_SOURCE_PR") or None,
        "source_ref": os.environ.get("DTFLOW_SOURCE_REF") or None,
        "workflow_ref": os.environ.get("GITHUB_WORKFLOW_REF"),
        "run_url": (
            f"{os.environ['GITHUB_SERVER_URL']}/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
            if os.environ.get("GITHUB_RUN_ID")
            else None
        ),
        "owner_smoke": "pending",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-c", "--commit", required=True, help="expected full checkout commit")
    parser.add_argument("-t", "--tag", default="", help="optional release tag matching the application version")
    args = parser.parse_args()
    record = verify(args.commit, args.tag)
    out = ROOT / "dist"
    (out / "SHA256SUMS.txt").write_text(f"{record['sha256']}  {record['artifact']}\n", encoding="utf-8")
    (out / "build-manifest.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as outputs:
            outputs.write(f"artifact={record['artifact']}\nversion={record['version']}\n")
    print(json.dumps(record, indent=2))


if __name__ == "__main__":
    main()
