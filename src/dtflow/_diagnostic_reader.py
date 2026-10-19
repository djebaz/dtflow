"""Qt-free bounded access to DTFlow v1 diagnostic sessions.

Single record policy is owned by _diagnostic_schema.  This module never
writes, repairs, reinterprets, or deletes canonical log segments.
"""
from __future__ import annotations

import csv
import hashlib
import heapq
import json
import os
import re
import stat
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator

from dtflow._diagnostic_schema import MAX_RECORD_BYTES, CaptureRejected, sanitize, validate_record

_SEGMENT = re.compile(r"^dtflow-([A-Za-z0-9_-]{1,128})\.jsonl(?:\.([1-3]))?$")
_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
PAGE_SIZE = 500
MAX_WORKER_BYTES = 16 * 1024 * 1024
MAX_DISPLAY_BYTES = 32 * 1024 * 1024


class EvidenceError(ValueError):
    """Incompatible, incomplete, changed or unsafe diagnostic evidence."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Segment:
    path: Path
    device: int
    inode: int
    size: int
    age: int


@dataclass(frozen=True)
class RecordRef:
    session_id: str
    seq: int
    device: int
    inode: int
    offset: int
    length: int
    basename: str


@dataclass
class SessionStatus:
    session_id: str
    state: str
    reasons: tuple[str, ...]
    records: int
    segments: tuple[Segment, ...]

    @property
    def complete(self) -> bool:
        return self.state == "complete"


def discover(directory: Path) -> list[str]:
    """Discover only strict canonical v1 session names, never exports or old logs."""
    if not directory.is_dir() or directory.is_symlink():
        return []
    result = set()
    for entry in directory.iterdir():
        match = _SEGMENT.fullmatch(entry.name)
        if match:
            result.add(match[1])
    return sorted(result)


def snapshot(directory: Path, session_id: str) -> tuple[Segment, ...]:
    """Capture a bounded set of regular, unredirected, same-directory handles."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", session_id):
        raise EvidenceError("invalid_session")
    if directory.is_symlink() or not directory.is_dir():
        raise EvidenceError("invalid_log_directory")
    items = []
    for age in (3, 2, 1, 0):
        name = f"dtflow-{session_id}.jsonl" + (f".{age}" if age else "")
        path = directory / name
        try:
            st = path.lstat()
        except FileNotFoundError:
            continue
        if not stat.S_ISREG(st.st_mode):
            raise EvidenceError("redirected_or_nonregular_segment")
        items.append(Segment(path, st.st_dev, st.st_ino, st.st_size, age))
    if not items:
        raise EvidenceError("session_unavailable")
    return tuple(items)


def _opened(segment: Segment):
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(segment.path, flags)
    try:
        st = os.fstat(fd)
        if (not stat.S_ISREG(st.st_mode) or (st.st_dev, st.st_ino) !=
                (segment.device, segment.inode) or st.st_size < segment.size):
            raise EvidenceError("snapshot_changed")
        # No redirect after fd was opened.
        path_st = segment.path.lstat()
        if (path_st.st_dev, path_st.st_ino) != (st.st_dev, st.st_ino):
            raise EvidenceError("snapshot_changed")
        return os.fdopen(fd, "rb", buffering=0)
    except BaseException:
        os.close(fd)
        raise


def _read_lines(segment: Segment, cancelled: Callable[[], bool] | None) -> Iterator[tuple[dict, RecordRef]]:
    with _opened(segment) as stream:
        offset = 0
        seen = 0
        while offset < segment.size:
            if seen % 128 == 0 and cancelled and cancelled():
                raise EvidenceError("cancelled")
            # Limit physical line read BEFORE allocation. A long line is unusable evidence.
            limit = min(MAX_RECORD_BYTES + 1, segment.size - offset)
            raw = stream.readline(limit)
            if not raw:
                raise EvidenceError("snapshot_changed")
            length = len(raw)
            if length > MAX_RECORD_BYTES or (length == limit and not raw.endswith(b"\n") and
                                             offset + length < segment.size):
                raise EvidenceError("oversized_or_truncated_line")
            if not raw.endswith(b"\n"):
                # A partial active tail is not an accepted record.
                raise EvidenceError("partial_tail")
            try:
                record = json.loads(raw.decode("utf-8"))
                validate_record(record)
            except (UnicodeError, ValueError, TypeError, CaptureRejected) as exc:
                raise EvidenceError("invalid_record") from exc
            ref = RecordRef(segment.path.name.split(".jsonl", 1)[0][7:], record["seq"],
                            segment.device, segment.inode, offset, length, segment.path.name)
            yield record, ref
            offset += length
            seen += 1


def scan(directory: Path, session_id: str, *, cancelled=None,
         on_record: Callable[[dict, RecordRef], None] | None = None,
         attempts: int = 3) -> SessionStatus:
    """Read a stable snapshot, with bounded retries on rename/rotation races.

    Status never upgrades unsealed or lossy diagnostics to complete.
    """
    last_reason = "snapshot_changed"
    for attempt in range(min(max(attempts, 1), 3)):
        segments = snapshot(directory, session_id)
        count, previous, startup, closed = 0, 0, False, False
        reasons = []
        duplicate = {}
        try:
            for segment in segments:
                for record, loc in _read_lines(segment, cancelled):
                    seq = record["seq"]
                    identity = (loc.device, loc.inode, loc.offset, loc.length)
                    old = duplicate.get(seq)
                    if old is not None:
                        if old != (record, identity) and old[0] != record:
                            raise EvidenceError("conflicting_sequence")
                        continue
                    # O(1) per-seq memory is NOT safe for arbitrarily large sessions.
                    # Consecutive overlap is enough for limited current 4-segment rotation.
                    duplicate.clear()
                    duplicate[seq] = (record, identity)
                    if previous and seq != previous + 1:
                        reasons.append("sequence_gap")
                    if count == 0 and seq != 1:
                        reasons.append("retained_prefix_missing")
                    if record["event"] == "session.started":
                        startup = record["data"].get("session_id") == session_id
                        if not startup:
                            reasons.append("session_identity_mismatch")
                    if record["event"] == "session.closed":
                        closed = True
                        if record["data"].get("written_records") != seq:
                            reasons.append("seal_count_mismatch")
                        if record["data"].get("writer_failures", 0) or record["data"].get("serialization_failures", 0):
                            reasons.append("writer_loss")
                        if record["data"].get("dropped", 0) or record["data"].get("rejected", 0):
                            reasons.append("admission_loss")
                    if record["event"] in ("diagnostics.loss", "diagnostics.record_rejected"):
                        reasons.append("diagnostic_loss")
                    if on_record:
                        on_record(record, loc)
                    previous = seq
                    count += 1
            # Revalidate the paths AFTER scanning, so renamed/pruned files cannot
            # be mixed with newly reused basenames and certified clean.
            if snapshot(directory, session_id) != segments:
                raise EvidenceError("snapshot_changed")
        except EvidenceError as exc:
            last_reason = exc.reason
            if exc.reason == "snapshot_changed" and attempt < 2:
                continue
            if exc.reason == "cancelled":
                raise
            reasons.append(exc.reason)
        if not startup:
            reasons.append("startup_missing")
        if not closed:
            reasons.append("seal_missing")
        state = ("incompatible" if any(x in reasons for x in
                 ("invalid_record", "session_identity_mismatch", "conflicting_sequence"))
                 else "incomplete" if reasons else "complete")
        if reasons == ["seal_missing"] and segments[-1].age == 0:
            state = "pending"
        return SessionStatus(session_id, state, tuple(dict.fromkeys(reasons)), count, segments)
    raise EvidenceError(last_reason)


def _value(record: dict, path: tuple[str, ...]):
    obj = record
    for key in path:
        if not isinstance(obj, dict) or key not in obj:
            return False, None
        obj = obj[key]
    return True, obj


def match(record: dict, *, search: str = "", level: str = "", component: str = "",
          event: str = "", family: str = "", from_ts: str = "", to_ts: str = "",
          field: tuple[str, ...] = (), op: str = "=", value=None) -> bool:
    """Typed AND predicates; never coerce numeric strings or equate booleans/numbers."""
    if level and record["level"] != level:
        return False
    if component and record["component"] != component:
        return False
    name = record["event"]
    if event and name != event:
        return False
    if family and name != family and not name.startswith(family + "."):
        return False
    if from_ts and record["ts"] < from_ts:
        return False
    if to_ts and record["ts"] > to_ts:
        return False
    if search:
        def atoms(node):
            if type(node) is dict:
                for k, v in node.items():
                    yield k
                    yield from atoms(v)
            elif type(node) is list:
                for value in node:
                    yield from atoms(value)
            elif node is None:
                yield "null"
            elif type(node) is bool:
                yield str(node).lower()
            else:
                yield str(node)
        if not any(search.casefold() in s.casefold() for s in atoms(record)):
            return False
    if field:
        present, actual = _value(record, field)
        if not present:
            return False
        if op not in ("=", "!=", "<", "<=", ">", ">=", "contains"):
            raise ValueError("Unsupported operator")
        if op == "contains":
            return type(actual) is str and type(value) is str and value.casefold() in actual.casefold()
        if actual is None or value is None:
            return op in ("=", "!=") and ((actual is None) == (value is None) if op == "=" else
                                         (actual is None) != (value is None))
        numeric = type(actual) in (int, float) and type(value) in (int, float)
        compatible = numeric or type(actual) is type(value)
        if not compatible or type(actual) in (dict, list) or type(value) in (dict, list):
            return False
        if op == "=":
            return actual == value
        if op == "!=":
            return actual != value
        if not numeric:
            return False
        return {"<":actual < value, "<=":actual <= value, ">":actual > value,
                ">=":actual >= value}[op]
    return True


def iter_records(directory: Path, session_id: str, *, cancelled=None,
                 require_complete: bool = False):
    """Stream a bounded-size snapshot only; callers must inspect session status."""
    buffer = []
    def collect(record, ref):
        # No unlimited materialization: this convenience method is for small
        # diagnostic consumers only and fails closed above a bounded limit.
        if len(buffer) >= 10000:
            raise EvidenceError("consumer_record_limit")
        buffer.append((record, ref))
    status = scan(directory, session_id, cancelled=cancelled, on_record=collect)
    if require_complete and not status.complete:
        raise EvidenceError("session_" + status.state)
    return status, tuple(buffer)


def query(directory: Path, session_id: str, *, filters=None, sort="ts",
          descending=True, offset=0, limit=PAGE_SIZE, cancelled=None):
    """Whole-session count and bounded page, never cache all matching record payloads.

    Offset pages re-scan the stable snapshot; offset itself does not grow memory.
    """
    if offset < 0 or limit < 1 or limit > PAGE_SIZE:
        raise ValueError("Invalid page size or offset")
    filters = filters or {}
    if sort not in ("ts", "event", "component", "level", "seq"):
        raise ValueError("Unsupported sort")
    selected = []
    count = 0
    def projected(record, ref):
        nonlocal count
        if not match(record, **filters):
            return
        count += 1
        value = _LEVELS.index(record["level"]) if sort == "level" else record[sort]
        # Bounded projection (no error tracebacks or nested large JSON in the heap).
        tiny = {key:record[key] for key in ("seq","ts","level","component","event")}
        data = (value, record["seq"], ref, tiny)
        selected.append(data)
        if len(selected) > offset + limit:
            raise EvidenceError("unbounded_offset_query")
    # Only the initial page fits here; advanced keyset paging is required before
    # the full UI integration. Never silently return incomplete counts.
    if offset + limit > PAGE_SIZE:
        raise EvidenceError("keyset_required")
    status = scan(directory, session_id, cancelled=cancelled, on_record=projected)
    selected.sort(key=lambda x:(x[0],x[1]), reverse=descending)
    return status, count, [(x[3],x[2]) for x in selected[offset:offset+limit]]


def read_detail(directory: Path, ref: RecordRef) -> dict:
    """Hydrate a selected record only when path, handle and sequence still agree."""
    parts = snapshot(directory, ref.session_id)
    item = next((x for x in parts if x.path.name == ref.basename and
                 (x.device,x.inode)==(ref.device,ref.inode)), None)
    if item is None or ref.offset + ref.length > item.size:
        raise EvidenceError("detail_unavailable")
    with _opened(item) as stream:
        stream.seek(ref.offset)
        raw = stream.read(ref.length)
    if len(raw) > MAX_RECORD_BYTES or not raw.endswith(b"\n"):
        raise EvidenceError("detail_unavailable")
    try:
        record = json.loads(raw)
        validate_record(record)
    except (ValueError, TypeError, CaptureRejected) as exc:
        raise EvidenceError("detail_unavailable") from exc
    if record["seq"] != ref.seq:
        raise EvidenceError("detail_unavailable")
    return record
