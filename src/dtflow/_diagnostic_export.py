"""Explicit, bounded derived diagnostics exports; not a second logging sink."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from dtflow._diagnostic_reader import (
    MAX_WORKER_BYTES,
    EvidenceError,
    read_located,
    segment_age,
    select,
    snapshot,
)
from dtflow._diagnostic_schema import sanitize

_CSV_COLUMNS = ("seq", "ts", "level", "component", "event", "kind", "msg", "data", "ctx", "agg", "error")


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
            return {sanitize(str(key)): clean(value, depth + 1) for key, value in obj.items()}
        if obj is None or type(obj) in (bool, int, float):
            return obj
        raise EvidenceError("export_unsupported_value")

    return clean(record)


def _batches(refs, *, limit=2000):
    """Consecutive ref groups bounded by count and by encoded bytes held in memory."""
    batch, size = [], 0
    for ref in refs:
        if batch and (len(batch) >= limit or size + ref.length > MAX_WORKER_BYTES // 2):
            yield batch
            batch, size = [], 0
        batch.append(ref)
        size += ref.length
    if batch:
        yield batch


def export_matching(
    directory: Path,
    session_id: str,
    destination: Path,
    *,
    fmt="jsonl",
    filters=None,
    sort="ts",
    descending=True,
    metric=(),
    cancelled=None,
    progress=None,
) -> dict:
    """Export matching records and publish a source-status manifest LAST.

    The caller owns a native destination picker and background worker.
    No copied source record is interpreted as a new session seal.

    The selection is fixed to the session as it stood when the export started: records
    appended later are left out, and rotation renames are followed by file identity, so
    the live session exports too. Incomplete or incompatible sources still export, with
    their evidence state and reasons disclosed in the manifest. `progress(done, total)`
    reports written records from the worker thread. If retention prunes a captured file
    before its records are copied, the export fails and publishes nothing.
    """
    if fmt not in ("jsonl", "csv", "text"):
        raise ValueError("Unsupported export format")
    destination = Path(destination)
    if destination.suffix != (".jsonl" if fmt == "jsonl" else ".csv" if fmt == "csv" else ".txt"):
        raise ValueError("Export extension does not match format")
    if destination.name.startswith("dtflow-") or destination.name.startswith("."):
        raise EvidenceError("canonical_export_name_forbidden")
    if not destination.parent.is_dir() or destination.parent.is_symlink():
        raise EvidenceError("invalid_export_directory")
    manifest_path = destination.with_name(destination.name + ".manifest.json")
    if destination.exists() or manifest_path.exists():
        raise FileExistsError("Export pair already exists")
    snapshot_before = snapshot(directory, session_id)
    shown = {(s.device, s.inode): (s.size, s.age) for s in snapshot_before}

    def within(ref):
        limit = shown.get((ref.device, ref.inode))
        return limit is not None and segment_age(ref.basename) >= limit[1] and ref.offset + ref.length <= limit[0]

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
            if fmt == "csv":
                # A zero-match filtered export is still a valid CSV document.
                # Write its schema once, even when no data row is selected.
                write((",".join(_CSV_COLUMNS) + "\r\n").encode("utf-8"), out)
            status, refs = select(
                directory,
                session_id,
                filters=filters,
                sort=sort,
                descending=descending,
                metric=metric,
                accept=within,
                cancelled=cancelled,
            )
            # Retention may prune a captured file while a live session rotates. Its records are
            # gone, so the export fails rather than claim a completed excerpt of the snapshot.
            present = {(segment.device, segment.inode) for segment in status.segments}
            if any(key not in present for key in shown):
                raise EvidenceError("session_rotated")
            total = len(refs)
            if progress:
                progress(0, total)
            done = 0
            for batch in _batches(refs):
                for ref, located in zip(batch, read_located(directory, batch), strict=True):
                    done += 1
                    if located is None:
                        raise EvidenceError("session_rotated")
                    record = _safe(located)
                    if fmt == "jsonl":
                        raw = (
                            json.dumps(record, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n"
                        ).encode("utf-8")
                    elif fmt == "text":
                        raw = (
                            f"{record['ts']} {record['level']} {record['event']}: "
                            f"{record.get('msg', '')} "
                            + json.dumps(
                                record.get("data", {}), ensure_ascii=False, allow_nan=False, separators=(",", ":")
                            )
                            + "\n"
                        ).encode("utf-8")
                    else:
                        # Constant columns, with nested JSON in bounded scalar cells.
                        buf = io.StringIO(newline="")
                        writer = csv.DictWriter(buf, fieldnames=_CSV_COLUMNS)
                        writer.writerow(
                            {
                                key: (
                                    json.dumps(record[key], ensure_ascii=False, separators=(",", ":"), allow_nan=False)
                                    if key in ("data", "ctx", "agg", "error") and key in record
                                    else record.get(key, "")
                                )
                                for key in _CSV_COLUMNS
                            }
                        )
                        raw = buf.getvalue().encode("utf-8")
                    write(raw, out)
                    start_seq = ref.seq if start_seq is None else min(start_seq, ref.seq)
                    end_seq = ref.seq if end_seq is None else max(end_seq, ref.seq)
                    count += 1
                if cancelled and cancelled():
                    raise EvidenceError("cancelled")
                if progress:
                    progress(done, total)
            out.flush()
            os.fsync(out.fileno())
        manifest = {
            "manifest_version": 1,
            "artifact_kind": "diagnostic_excerpt",
            "export_id": export_id,
            "exported_at": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "source_session_id": session_id,
            "record_version": 1,
            "source_evidence": {"state": status.state, "reasons": list(status.reasons)},
            # The fixed selection boundary: records wholly inside these files and sizes.
            "snapshot": [{"segment": s.path.name, "bytes": s.size} for s in snapshot_before],
            "snapshot_policy": "records_within_start_snapshot",
            "selection": {
                "format": fmt,
                "sort": sort,
                "descending": descending,
                "exported_records": count,
                "query_policy": "v1",
                "filters_redacted": bool(filters),
            },
            "artifact": {
                "basename": destination.name,
                "size_bytes": bytes_written,
                "sha256": digest.hexdigest(),
                "first_seq": start_seq,
                "last_seq": end_seq,
                "export_status": "complete",
            },
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
