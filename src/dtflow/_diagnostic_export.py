"""Explicit, bounded derived diagnostics exports; not a second logging sink."""
from __future__ import annotations

import csv
import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from dtflow._diagnostic_reader import (
    EvidenceError, discover, query, read_detail, snapshot,
    PAGE_SIZE,
)
from dtflow._diagnostic_schema import sanitize

_CSV_COLUMNS = ("seq", "ts", "level", "component", "event", "kind",
                "msg", "data", "ctx", "agg", "error")


def _safe(record):
    """Defensive second-line string sanitizer for derived human-readable output."""
    def clean(obj, depth=0):
        if depth > 16:
            raise EvidenceError("export_depth_limit")
        if type(obj) is str:
            return sanitize(obj)
        if type(obj) is list:
            return [clean(value, depth + 1) for value in obj]
        if type(obj) is dict:
            return {sanitize(str(key)): clean(value, depth + 1)
                    for key, value in obj.items()}
        if obj is None or type(obj) in (bool, int, float):
            return obj
        raise EvidenceError("export_unsupported_value")
    return clean(record)


def export_matching(directory: Path, session_id: str, destination: Path,
                    *, fmt="jsonl", filters=None, sort="ts", descending=True,
                    metric=(), cancelled=None) -> dict:
    """Export matching records and publish a source-status manifest LAST.

    The caller owns a native destination picker and background worker.
    No copied source record is interpreted as a new session seal.
    """
    if fmt not in ("jsonl", "csv", "text"):
        raise ValueError("Unsupported export format")
    destination = Path(destination)
    if destination.suffix != (".jsonl" if fmt == "jsonl" else
                              ".csv" if fmt == "csv" else ".txt"):
        raise ValueError("Export extension does not match format")
    if destination.name.startswith("dtflow-") or destination.name.startswith("."):
        raise EvidenceError("canonical_export_name_forbidden")
    if not destination.parent.is_dir() or destination.parent.is_symlink():
        raise EvidenceError("invalid_export_directory")
    manifest_path = destination.with_name(destination.name + ".manifest.json")
    if destination.exists() or manifest_path.exists():
        raise FileExistsError("Export pair already exists")
    snapshot_before = snapshot(directory, session_id)
    temp_path = None
    bytes_written = 0
    digest = hashlib.sha256()
    count = 0
    start_seq = end_seq = None
    status = None
    export_id = uuid4().hex
    fd, name = tempfile.mkstemp(prefix=".diagnostic-export-", dir=destination.parent)
    temp_path = Path(name)

    def write(raw, out):
        nonlocal bytes_written
        if cancelled and cancelled():
            raise EvidenceError("cancelled")
        out.write(raw)
        digest.update(raw)
        bytes_written += len(raw)

    try:
        with os.fdopen(fd, "wb") as out:
            cursor = None
            while True:
                result, _total, page, next_cursor = query(
                    directory, session_id, filters=filters, sort=sort,
                    descending=descending, cursor=cursor, limit=PAGE_SIZE,
                    metric=metric, cancelled=cancelled,
                )
                if snapshot(directory, session_id) != snapshot_before:
                    raise EvidenceError("snapshot_changed")
                if result.state not in ("complete", "pending"):
                    raise EvidenceError("source_" + result.state)
                status = result
                if not page:
                    break
                for projection, ref in page:
                    record = _safe(read_detail(directory, ref))
                    if fmt == "jsonl":
                        raw = (json.dumps(record, ensure_ascii=False, allow_nan=False,
                                          separators=(",", ":")) + "\n").encode("utf-8")
                    elif fmt == "text":
                        raw = (f'{record["ts"]} {record["level"]} {record["event"]}: '
                               f'{record.get("msg", "")} '
                               + json.dumps(record.get("data", {}), ensure_ascii=False,
                                            allow_nan=False, separators=(",", ":"))
                               + "\n").encode("utf-8")
                    else:
                        # Constant columns, with nested JSON in bounded scalar cells.
                        import io
                        buf = io.StringIO(newline="")
                        writer = csv.DictWriter(buf, fieldnames=_CSV_COLUMNS)
                        if count == 0:
                            writer.writeheader()
                        writer.writerow({
                            key: (json.dumps(record[key], ensure_ascii=False,
                                             separators=(",", ":"), allow_nan=False)
                                  if key in ("data", "ctx", "agg", "error") and key in record
                                  else record.get(key, ""))
                            for key in _CSV_COLUMNS
                        })
                        raw = buf.getvalue().encode("utf-8")
                    write(raw, out)
                    start_seq = ref.seq if start_seq is None else min(start_seq, ref.seq)
                    end_seq = ref.seq if end_seq is None else max(end_seq, ref.seq)
                    count += 1
                if next_cursor is None:
                    break
                cursor = next_cursor
            out.flush()
            os.fsync(out.fileno())
        if snapshot(directory, session_id) != snapshot_before:
            raise EvidenceError("snapshot_changed")
        if status is None:
            raise EvidenceError("empty_export_snapshot")
        manifest = {
            "manifest_version": 1,
            "artifact_kind": "diagnostic_excerpt",
            "export_id": export_id,
            "exported_at": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "source_session_id": session_id,
            "record_version": 1,
            "source_evidence": {"state": status.state, "reasons": list(status.reasons)},
            "snapshot": [{"segment": s.path.name, "bytes": s.size}
                         for s in snapshot_before],
            "selection": {"format": fmt, "sort": sort, "descending": descending,
                          "exported_records": count, "query_policy": "v1",
                          "filters_redacted": bool(filters)},
            "artifact": {"basename": destination.name, "size_bytes": bytes_written,
                         "sha256": digest.hexdigest(), "first_seq": start_seq,
                         "last_seq": end_seq, "export_status": "complete"},
        }
        # Publish the data first without overwriting, and the completion
        # manifest last. Interrupted/failed operations may leave an orphan
        # data file, but it cannot be mistaken for a completed excerpt.
        os.link(temp_path, destination)
        mfd, mname = tempfile.mkstemp(prefix=".diagnostic-manifest-", dir=destination.parent)
        mtemp = Path(mname)
        try:
            with os.fdopen(mfd, "w", encoding="utf-8") as output:
                json.dump(manifest, output, ensure_ascii=False, indent=2)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.link(mtemp, manifest_path)
        finally:
            mtemp.unlink(missing_ok=True)
        return manifest
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
