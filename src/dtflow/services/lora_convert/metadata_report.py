"""Bulk report of the descriptive metadata of safetensors LoRAs (#414), without converting anything.

Used by ``python -m dtflow.services.lora_convert SOURCE REPORT_FOLDER --metadata``: one header read per file
(structure and metadata together, ``detect.inspect_source``), best effort, failures reported per file. The values are
what the file *declares*; they are never evidence for the family and never change a conversion.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from dtflow.services.lora_convert.detect import inspect_source
from dtflow.services.lora_convert.source_metadata import declared_family

METADATA_REPORT_SCHEMA_VERSION = 1


def build_item(path: Path, *, analyze: bool = False) -> dict:
    """The report entry of one file. Never raises: a failure becomes a failed entry with a short reason."""
    started = time.monotonic()
    item: dict = {"source": path.name}
    try:
        inspected = inspect_source(path)
        detection, metadata = inspected.detection, inspected.metadata
        if detection.outcome == "unreadable":
            item.update(status="failed", error_code="unreadable", error=detection.reason)
        else:
            record = metadata.record()
            item.update(
                status="succeeded",
                has_metadata=record is not None,
                fields=record["fields"] if record else {},
                top_tags=record["tags"] if record else [],
                trigger_suggestion=metadata.trigger,
                trigger_source=metadata.trigger_source,
                declared_family=declared_family(record),
            )
            if analyze:
                report = detection.as_report(None)
                item["source_family_detection"] = report
                found, declared = detection.family, item["declared_family"]
                item["declared_matches_detected"] = None if not (found and declared) else found == declared
    except Exception as error:  # a failed file must not stop the batch
        item.update(status="failed", error_code="unexpected_error", error=f"{type(error).__name__}: {error}")
    item["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return item


def build_report(source: Path, directory: Path, items: list[dict], started: str, *, analyze: bool) -> dict:
    return {
        "report": "lora_source_metadata",
        "schema_version": METADATA_REPORT_SCHEMA_VERSION,
        "started_at_utc": started,
        "completed_at_utc": datetime.now(UTC).isoformat(),
        "input": str(source),
        "report_folder": str(directory),
        "analysis_enabled": analyze,
        "total_files": len(items),
        "summary": {
            "succeeded": sum(item["status"] == "succeeded" for item in items),
            "failed": sum(item["status"] == "failed" for item in items),
            "with_metadata": sum(bool(item.get("has_metadata")) for item in items),
            "with_trigger_suggestion": sum(bool(item.get("trigger_suggestion")) for item in items),
        },
        "files": items,
    }


def write_report(directory: Path, report: dict) -> Path:
    """Write the JSON report under ``<directory>/reports`` with a unique timestamped name; never overwrites."""
    reports = directory / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    base = f"lora-metadata-v{report['schema_version']}-{stamp}"
    for index in range(1000):
        path = reports / f"{base}{'' if index == 0 else f'-{index}'}.json"
        try:
            with path.open("x", encoding="utf-8") as handle:
                json.dump(report, handle, indent=2, ensure_ascii=True)
                handle.write("\n")
            return path
        except FileExistsError:
            continue
    raise OSError("Could not allocate a unique metadata report filename.")


def run(files: list[Path], source: Path, directory: Path, *, analyze: bool) -> int:
    """Report every file, print one line each, write the report. Exit code: 1 when any file failed."""
    started = datetime.now(UTC).isoformat()
    items: list[dict] = []
    for index, path in enumerate(files, 1):
        item = build_item(path, analyze=analyze)
        items.append(item)
        detail = (
            item.get("error", "")
            if item["status"] == "failed"
            else "metadata found" + (", trigger suggested" if item.get("trigger_suggestion") else "")
            if item.get("has_metadata")
            else "no descriptive metadata"
        )
        print(f"[{index}/{len(files)}] {path.name}: {item['status']} ({detail})", flush=True)
    report = build_report(source, directory, items, started, analyze=analyze)
    try:
        path = write_report(directory, report)
    except OSError as error:
        print(f"error: Cannot save metadata report: {error}", file=sys.stderr)
        return 2
    summary = report["summary"]
    print(
        f"Metadata: {summary['succeeded']} read, {summary['failed']} failed, {summary['with_metadata']} with metadata, "
        f"{summary['with_trigger_suggestion']} with a trigger suggestion. Report: {path}"
    )
    return (2 if len(files) == 1 else 1) if summary["failed"] else 0
