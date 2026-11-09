"""Proof of concept: convert a LoRA .safetensors file to a Draw Things .ckpt.

This calls Draw Things' official ``media-generation-kit-cli lora convert`` command as a
separate executable (LGPLv3 stays at arm's length; nothing is linked into dtFlow).

Examples:
    python tools/drawthings/lora_convert.py my_lora.safetensors
    python tools/drawthings/lora_convert.py my_lora.safetensors --output-dir out --scale 0.8
    python tools/drawthings/lora_convert.py my_lora.safetensors --cli C:/tools/media-generation-kit-cli.exe
    python tools/drawthings/lora_convert.py --build   # clone the pinned revision and swift build it

The CLI is looked up in this order: ``--cli``, ``DT_MGK_CLI``, the local build made by
``--build``, then ``PATH``.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_URL = "https://github.com/drawthingsai/media-generation-kit.git"
PINNED_REVISION = "37e0b70092ad1a0c1d4c6b24f16e17b73f1b1fb3"
CLI_NAME = "media-generation-kit-cli"
DEFAULT_BUILD_DIR = Path(__file__).resolve().parent / ".mgk-build"
CHUNK_SIZE = 1024 * 1024


def exe_name() -> str:
    """Return the CLI file name for the current platform."""
    return CLI_NAME + (".exe" if os.name == "nt" else "")


def sha256_and_size(path: Path) -> tuple[int, str]:
    """Return the byte size and SHA-256 digest of a file."""
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK_SIZE):
            total += len(chunk)
            digest.update(chunk)
    return total, digest.hexdigest()


def run(command: list[str], cwd: Path | None = None) -> int:
    """Run a command, streaming its output, and return its exit code."""
    print("$", " ".join(command), flush=True)
    return subprocess.run(command, cwd=cwd, check=False).returncode


def build_cli(build_dir: Path) -> Path | None:
    """Clone the pinned revision and build the CLI with SwiftPM."""
    if shutil.which("git") is None or shutil.which("swift") is None:
        print("error: --build needs both `git` and `swift` on PATH.", file=sys.stderr)
        return None
    source = build_dir / "media-generation-kit"
    if not source.exists():
        build_dir.mkdir(parents=True, exist_ok=True)
        if run(["git", "clone", REPO_URL, str(source)]) != 0:
            return None
    if run(["git", "checkout", PINNED_REVISION], cwd=source) != 0:
        return None
    if run(["swift", "build", "-c", "release", "--product", CLI_NAME], cwd=source) != 0:
        print("error: swift build failed; see the output above.", file=sys.stderr)
        return None
    return find_built_cli(build_dir)


def find_built_cli(build_dir: Path) -> Path | None:
    """Return the CLI built by ``--build`` if it exists."""
    release = build_dir / "media-generation-kit" / ".build" / "release" / exe_name()
    return release if release.is_file() else None


def locate_cli(explicit: Path | None, build_dir: Path) -> Path | None:
    """Find the CLI executable."""
    candidates: list[Path] = []
    if explicit is not None:
        candidates.append(explicit)
    if env := os.environ.get("DT_MGK_CLI"):
        candidates.append(Path(env))
    if built := find_built_cli(build_dir):
        candidates.append(built)
    if found := shutil.which(CLI_NAME):
        candidates.append(Path(found))
    for candidate in candidates:
        if candidate.expanduser().is_file():
            return candidate.expanduser()
    return None


def convert(cli: Path, source: Path, output_dir: Path, scale: float | None) -> Path | None:
    """Run ``lora convert`` and return the newest .ckpt it wrote."""
    output_dir.mkdir(parents=True, exist_ok=True)
    before = {p: p.stat().st_mtime_ns for p in output_dir.glob("*.ckpt")}
    command = [str(cli), "lora", "convert", "--input", str(source), "--output-dir", str(output_dir)]
    if scale is not None:
        command += ["--scale", str(scale)]
    code = run(command)
    if code != 0:
        print(f"error: the converter exited with code {code}.", file=sys.stderr)
        return None
    changed = [p for p in output_dir.glob("*.ckpt") if before.get(p) != p.stat().st_mtime_ns]
    if not changed:
        print("error: the converter reported success but wrote no .ckpt file.", file=sys.stderr)
        return None
    return max(changed, key=lambda p: p.stat().st_mtime_ns)


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser."""
    parser = argparse.ArgumentParser(description="Convert a LoRA .safetensors file to a Draw Things .ckpt.")
    parser.add_argument("input", type=Path, nargs="?", help="Path to the LoRA .safetensors file.")
    parser.add_argument("--output-dir", type=Path, default=Path("lora_out"), help="Where to write the .ckpt.")
    parser.add_argument("--scale", type=float, default=None, help="Optional LoRA scale passed to the converter.")
    parser.add_argument("--cli", type=Path, default=None, help="Path to media-generation-kit-cli.")
    parser.add_argument("--build", action="store_true", help="Clone the pinned revision and build the CLI first.")
    parser.add_argument("--build-dir", type=Path, default=DEFAULT_BUILD_DIR, help="Where --build clones and builds.")
    return parser


def main() -> int:
    """Run the converter."""
    args = build_parser().parse_args()
    cli: Path | None = None
    if args.build:
        cli = build_cli(args.build_dir)
        if cli is None:
            return 1
        print(f"built: {cli}")
        if args.input is None:
            return 0
    if args.input is None:
        print("error: give the LoRA .safetensors file to convert.", file=sys.stderr)
        return 2
    source = args.input.expanduser()
    if not source.is_file():
        print(f"error: input not found: {source}", file=sys.stderr)
        return 2
    if source.suffix.lower() != ".safetensors":
        print(f"warning: {source.name} does not end in .safetensors.", file=sys.stderr)
    cli = cli or locate_cli(args.cli, args.build_dir)
    if cli is None:
        print(
            f"error: {exe_name()} not found. Pass --cli, set DT_MGK_CLI, put it on PATH, or run with --build.",
            file=sys.stderr,
        )
        return 1
    result = convert(cli, source, args.output_dir.expanduser(), args.scale)
    if result is None:
        return 1
    size, digest = sha256_and_size(result)
    print(f"output: {result.resolve()}")
    print(f"size:   {size} bytes")
    print(f"sha256: {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
