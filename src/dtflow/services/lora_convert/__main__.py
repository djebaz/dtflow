"""Convert one LoRA or a folder of safetensors files to Draw Things checkpoints.

Examples:
    uv run python -m dtflow.services.lora_convert input.safetensors output.ckpt --family krea2
    uv run python -m dtflow.services.lora_convert "C:/LoRAs/*krea2*.safetensors" C:/Converted --family krea2 -w

Folder and wildcard inputs are non-recursive. Wildcards are case-insensitive filename
globs, not regular expressions. A batch creates a new versioned JSON report per run.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from dataclasses import replace
from datetime import UTC, datetime
from fnmatch import fnmatchcase
from pathlib import Path

from dtflow.services.lora_convert import (
    CONVERTER_VERSION,
    ConversionError,
    ConversionProgress,
    ConversionRequest,
    convert_lora,
    supported_families,
)

REPORT_SCHEMA_VERSION = 1


def _print_message(event: ConversionProgress) -> None:
    if event.message:
        print(event.message)


def _sources(source: Path) -> tuple[list[Path], bool]:
    """Select files from one file, one directory, or a case-insensitive filename glob."""
    wildcard = any(ch in source.name for ch in "*?[")
    if wildcard:
        if any(ch in str(source.parent) for ch in "*?["):
            raise ValueError("Wildcards are supported in the filename only, not in parent directories.")
        if not source.parent.is_dir():
            raise ValueError(f"Input folder does not exist: {source.parent}")
        files = [
            path
            for path in source.parent.iterdir()
            if path.is_file()
            and path.suffix.casefold() == ".safetensors"
            and fnmatchcase(path.name.casefold(), source.name.casefold())
        ]
    elif source.is_dir():
        files = [path for path in source.iterdir() if path.is_file() and path.suffix.casefold() == ".safetensors"]
    elif source.is_file():
        if source.suffix.casefold() != ".safetensors":
            raise ValueError(f"Expected a .safetensors source: {source}")
        return [source], False
    else:
        raise ValueError(f"No such input file or folder: {source}")
    return sorted(files, key=lambda path: (path.name.casefold(), path.name)), True


def _convert(source: Path, destination: Path, args: argparse.Namespace, *, progress=None):
    """Convert one file, replacing an existing result only after successful conversion."""
    request = ConversionRequest(
        source=source,
        destination=destination,
        family=args.family,
        scale=args.scale,
        allow_experimental=args.experimental,
        ignore_unmatched=args.ignore_unmatched,
        hash_source=False,
    )
    if not args.overwrite or not destination.exists():
        return convert_lora(request, progress=progress)

    # The service intentionally never overwrites. Convert beside the target first,
    # then atomically replace it; an error leaves the previous checkpoint untouched.
    with tempfile.TemporaryDirectory(prefix=".lora-convert-", dir=destination.parent) as temporary:
        staged = Path(temporary) / destination.name
        result = convert_lora(replace(request, destination=staged), progress=progress)
        os.replace(staged, destination)
        return replace(result, destination=destination)


def _write_report(directory: Path, report: dict) -> Path:
    """Preserve every run report under a unique timestamped, schema-versioned name."""
    reports = directory / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    base = f"lora-convert-v{REPORT_SCHEMA_VERSION}-{stamp}"
    for index in range(1000):
        suffix = "" if index == 0 else f"-{index}"
        path = reports / f"{base}{suffix}.json"
        try:
            with path.open("x", encoding="utf-8") as handle:
                json.dump(report, handle, indent=2, ensure_ascii=False)
                handle.write("\n")
            return path
        except FileExistsError:
            continue
    raise OSError("Could not allocate a unique batch report filename.")


def _batch(files: list[Path], destination: Path, source: Path, args: argparse.Namespace) -> int:
    if destination.exists() and not destination.is_dir():
        print(f"error: Batch output must be a folder: {destination}", file=sys.stderr)
        return 2
    try:
        destination.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        print(f"error: Cannot create output folder: {error}", file=sys.stderr)
        return 2

    started = datetime.now(UTC).isoformat()
    items: list[dict] = []
    reserved: set[str] = set()
    for index, path in enumerate(files, 1):
        output = destination / path.with_suffix(".ckpt").name
        item: dict = {"source": path.name, "destination": output.name}
        started_item = time.monotonic()
        print(f"[{index}/{len(files)}] {path.name}", flush=True)
        # Windows treats case-only filename variants as the same destination.
        # Never let multiple sources silently replace one another in one batch.
        key = output.name.casefold()
        if key in reserved:
            item.update(status="failed", error_code="destination_collision", error="Duplicate destination filename.")
        else:
            reserved.add(key)
            if output.exists() and not args.overwrite:
                item.update(status="skipped", reason="Destination already exists (use -w to replace it).")
            else:
                try:
                    result = _convert(path, output, args)
                    item.update(
                        status="succeeded",
                        tensors_written=result.tensors_written,
                        output_bytes=result.destination_size,
                        output_sha256=result.destination_sha256,
                        warnings=list(result.warnings),
                    )
                except ConversionError as error:
                    item.update(
                        status="failed",
                        error_code=error.code,
                        error=str(error),
                    )
                    if hasattr(error, "modules"):
                        item["unmatched_modules"] = list(error.modules)
                except Exception as error:
                    # A bad file must not prevent analysis of later files; preserve the
                    # unexpected failure distinctly instead of labelling it unsupported.
                    item.update(
                        status="failed", error_code="unexpected_error", error=f"{type(error).__name__}: {error}"
                    )
        item["elapsed_seconds"] = round(time.monotonic() - started_item, 3)
        items.append(item)
        print(f"  {item['status']}: {item.get('error', item.get('reason', output.name))}", flush=True)

    totals = {status: sum(item["status"] == status for item in items) for status in ("succeeded", "failed", "skipped")}
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "converter_version": CONVERTER_VERSION,
        "started_at_utc": started,
        "completed_at_utc": datetime.now(UTC).isoformat(),
        "input": str(source),
        "output_folder": str(destination),
        "family": args.family,
        "scale": args.scale,
        "overwrite": args.overwrite,
        "total_files": len(files),
        "summary": totals,
        "files": items,
    }
    try:
        report_path = _write_report(destination, report)
    except OSError as error:
        print(f"error: Cannot save batch report: {error}", file=sys.stderr)
        return 2
    print(
        f"Batch: {totals['succeeded']} succeeded, {totals['failed']} failed, "
        f"{totals['skipped']} skipped. Report: {report_path}"
    )
    return 1 if totals["failed"] else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m dtflow.services.lora_convert", description=__doc__.splitlines()[0])
    ap.add_argument(
        "source", type=Path, help="A .safetensors file, folder, or quoted filename glob (case-insensitive)"
    )
    ap.add_argument("destination", type=Path, help="Output .ckpt file or, for a batch, an output folder")
    ap.add_argument("--family", required=True, choices=sorted(spec.key for spec in supported_families()))
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument(
        "-w", "--overwrite", action="store_true", help="Replace existing .ckpt files after successful conversion"
    )
    ap.add_argument(
        "--ignore-unmatched",
        action="store_true",
        help="sd1 and sdxl only: skip LoRA tensors that have no place in the model, as Draw Things does",
    )
    ap.add_argument(
        "--experimental",
        action="store_true",
        help="allow families that are not verified against Draw Things yet (sdxl)",
    )
    args = ap.parse_args(argv)
    try:
        files, batch = _sources(args.source)
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    if not files:
        print(f"error: No matching .safetensors files found for {args.source}", file=sys.stderr)
        return 2
    if batch:
        return _batch(files, args.destination, args.source, args)

    output = (
        args.destination / files[0].with_suffix(".ckpt").name
        if args.destination.is_dir() or args.destination.suffix.casefold() != ".ckpt"
        else args.destination
    )
    try:
        _convert(files[0], output, args, progress=_print_message)
    except (ConversionError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(f"Converted {files[0].name} -> {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
