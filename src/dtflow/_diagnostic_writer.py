"""Owned queue/listener internals; configured only by logging_setup."""

import json
import logging
import logging.handlers
import queue
import threading
from collections import Counter
from time import monotonic

from dtflow._diagnostic_schema import (
    EVENT_NAME,
    MAX_RECORD_BYTES,
    QUEUE_BYTES,
    QUEUE_RECORDS,
    CaptureRejected,
    capture,
    utc_timestamp,
    validate_record,
)

_AGGREGATED = frozenset({"thumbnail_worker", "thumbnail_end_to_end", "scroll_intent", "prepare_slice", "gui_gap"})
_METRICS = frozenset({"elapsed_ms", "wall_ms", "cpu_ms", "items", "count", "samples", "mounted", "offset"})
_DIMENSIONS = ("direction", "source", "mode", "success")


class Admission:
    def __init__(self):
        self.queue = queue.Queue(maxsize=QUEUE_RECORDS)
        self.lock = threading.Lock()
        self.pending_bytes = 0
        self.capture_bytes = 0
        self.inflight = 0
        self.accepting = True
        self.counts = Counter(
            observed=0, admitted=0, rejected=0, dropped=0,
            writer_failures=0, serialization_failures=0, aggregate_source_samples=0,
            capture_rejected=0, processed_records=0, source_summary_samples=0,
        )
        self.losses = Counter()
        self.capture_rejections = {}
        self.stopping = threading.Event()
        self.expired = threading.Event()

    def loss(self, reason: str, level: str, measurement: bool, *, rejected=False) -> None:
        # Called with lock held; keys are from fixed policy categories, not events.
        self.counts["rejected" if rejected else "dropped"] += 1
        if reason not in {"queue_records", "queue_bytes", "capture_limit", "writer_error", "serialization_error", "shutdown_timeout"}:
            reason = "other"
        self.losses[reason, level, measurement] += 1

    def snapshot(self):
        with self.lock:
            return dict(self.counts), dict(self.losses)


class CaptureHandler(logging.handlers.QueueHandler):
    """Prepare bounded immutable-by-ownership data, never queue LogRecord graphs."""

    def __init__(self, admission: Admission):
        super().__init__(admission.queue)
        self.admission = admission

    def handle(self, record):
        # Do not serialize all producer capture behind Handler's emission lock.
        # Only the small accounting/put-nowait transaction uses admission.lock.
        if self.filter(record):
            self.emit(record)
        return True

    def emit(self, record):
        state = self.admission
        level = record.levelname if record.levelname in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"} else "ERROR"
        measured = getattr(record, "dtflow_kind", "event") in ("measurement", "aggregate")
        with state.lock:
            if not state.accepting:
                return
            state.counts["observed"] += 1
            if state.pending_bytes + state.capture_bytes + MAX_RECORD_BYTES + 1024 > QUEUE_BYTES:
                state.loss("queue_bytes", level, measured)
                return
            state.capture_bytes += MAX_RECORD_BYTES + 1024
            state.inflight += 1
        try:
            payload = None
            payload, size = capture(record)
        except Exception as exc:
            reason = exc.reason if isinstance(exc, CaptureRejected) else "unsupported_value"
            with state.lock:
                state.loss(reason, level, measured, rejected=True)
                state.counts["capture_rejected"] += 1
                name = getattr(record, "dtflow_event", "logging.message")
                if type(name) is not str or len(name) > 180 or not EVENT_NAME.fullmatch(name):
                    name = "unknown"
                bucket = ("capture_limit" if reason == "capture_limit" else "other", level, measured)
                previous = state.capture_rejections.get(bucket, (None, None, 0))[2]
                state.capture_rejections[bucket] = (name, getattr(exc, "size", None), previous + 1)
            return
        finally:
            # The handoff below must remain inside the inflight lifetime.
            # Rejection has no payload; success decrements after admission.
            if payload is None:
                with state.lock:
                    state.capture_bytes -= MAX_RECORD_BYTES + 1024
                    state.inflight -= 1
        try:
            with state.lock:
                state.capture_bytes -= MAX_RECORD_BYTES + 1024
                if state.pending_bytes + state.capture_bytes + size > QUEUE_BYTES:
                    state.loss("queue_bytes", level, measured)
                else:
                    try:
                        state.queue.put_nowait((payload, size))
                    except queue.Full:
                        state.loss("queue_records", level, measured)
                    else:
                        state.pending_bytes += size
                        state.counts["admitted"] += 1
        finally:
            with state.lock:
                state.inflight -= 1


class JsonlHandler(logging.handlers.RotatingFileHandler):
    """Single binary UTF-8/LF sink, using existing stdlib rotation machinery."""

    def __init__(self, path, *, max_bytes, backups, report_error=None):
        super().__init__(path, maxBytes=max_bytes, backupCount=backups, delay=True)
        self.report_error = report_error
        self.failed = False
        self.reported = False
        self._report_lock = threading.Lock()
        self.seq = 0
        self.close_deadline = None

    def _open(self):
        return open(self.baseFilename, "ab")

    def warn(self):
        with self._report_lock:
            if self.reported:
                return
            self.reported = True
        message = "Diagnostic evidence is incomplete; logging failed or records were rejected."
        try:
            if self.report_error is not None:
                self.report_error(message)
            else:
                import sys

                print(message, file=sys.stderr)
        except Exception:
            # An error reporter is supplementary, never generation authority.
            pass

    def write_record(self, payload: dict) -> None:
        if self.failed:
            raise OSError("diagnostic sink disabled")
        record = {"v": 1, "seq": self.seq + 1, **payload}
        validate_record(record)
        line = (json.dumps(record, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n").encode("utf-8")
        if len(line) > MAX_RECORD_BYTES:
            raise CaptureRejected("record_too_large", len(line))
        try:
            closing = payload.get("event") == "session.closed"
            if closing and self.close_deadline is not None and monotonic() >= self.close_deadline:
                raise OSError("diagnostic close deadline")
            if self.stream is None:
                self.stream = self._open()
            if self.stream.tell() and self.stream.tell() + len(line) > self.maxBytes:
                self.doRollover()
            if self.stream is None:
                self.stream = self._open()
            if self.stream.write(line) != len(line):
                raise OSError("partial diagnostic write")
            self.stream.flush()
            if closing and self.close_deadline is not None and monotonic() >= self.close_deadline:
                # The worker may return from a slow write after the caller's
                # shutdown wait. Withdraw that late seal, not any data record.
                self.stream.seek(-len(line), 2)
                self.stream.truncate()
                self.stream.flush()
                raise OSError("diagnostic close deadline")
            self.seq += 1
        except OSError:
            self.failed = True
            self.warn()
            raise


class DiagnosticListener(logging.handlers.QueueListener):
    """Listener-owned ordering/aggregation/seal with out-of-band shutdown."""

    def __init__(self, admission: Admission, sink: JsonlHandler, startup: dict):
        super().__init__(admission.queue, sink)
        self.admission = admission
        self.sink = sink
        self.startup = startup
        self.batches = {}
        self.last_losses = {}
        self.last_capture_rejections = {}
        self._deadline = None
        self.failure = False

    def _write(self, payload: dict) -> None:
        try:
            self.sink.write_record(payload)
        except OSError:
            if not self.failure:
                with self.admission.lock:
                    self.admission.counts["writer_failures"] += 1
            self.failure = True
        except Exception as exc:
            reason = exc.reason if isinstance(exc, CaptureRejected) else "serialization_error"
            with self.admission.lock:
                self.admission.counts["serialization_failures"] += 1
                self.admission.loss(reason, payload.get("level", "ERROR"), payload.get("kind") in ("measurement", "aggregate"), rejected=True)
            self.sink.warn()
            marker = {
                "ts": payload.get("ts", utc_timestamp()), "level": "WARNING", "component": "dtflow.diagnostics",
                "event": "diagnostics.record_rejected",
                "data": {"original_event": payload.get("event", "logging.message"), "reason": reason,
                         "limit_bytes": MAX_RECORD_BYTES, "original_content_available": False},
            }
            if isinstance(exc, CaptureRejected) and exc.size is not None:
                marker["data"]["size_bytes"] = exc.size
            try:
                self.sink.write_record(marker)
            except Exception:
                self.failure = True

    def _control(self, name, data, *, level="INFO"):
        self._write({"ts": utc_timestamp(), "level": level, "component": "dtflow.diagnostics", "event": name, "data": data})

    def _flush_losses(self):
        _, losses = self.admission.snapshot()
        with self.admission.lock:
            capture_rejections = dict(self.admission.capture_rejections)
        for key, (original, estimate, count) in capture_rejections.items():
            delta = count - self.last_capture_rejections.get(key, 0)
            if not delta:
                continue
            marker = {
                "original_event": original,
                "reason": "capture_too_large" if key[0] == "capture_limit" else "unsupported_value",
                "count": delta, "representative_event_only": True,
                "limit_bytes": MAX_RECORD_BYTES, "original_content_available": False,
            }
            if estimate is not None:
                marker["estimated_bytes"] = estimate
            self._control("diagnostics.record_rejected", marker, level="WARNING")
            self.last_capture_rejections[key] = count
        for key, count in losses.items():
            delta = count - self.last_losses.get(key, 0)
            if not delta:
                continue
            reason, level, measurement = key
            self._control("diagnostics.loss", {"reason": reason, "count": delta, "level": level,
                                               "affects_measurements": measurement}, level="WARNING")
            self.last_losses[key] = count
            self.sink.warn()

    def _flush_batch(self, key):
        batch = self.batches.pop(key)
        phase, dimensions, attempt = key
        metrics = {}
        for name, values in batch["metrics"].items():
            values.sort()
            count = len(values)
            metrics[name] = {"count": count, "mean": sum(values) / count,
                             "p95": values[max(0, (95 * count + 99) // 100 - 1)],
                             "max": values[-1], "total": sum(values)}
        # Every accepted raw phase has at least one measured numeric field.
        payload = {
            "ts": batch["last"], "mono_ns": batch["last_mono"], "level": "DEBUG", "component": "dtflow.ui.gallery_perf",
            "event": "gallery.performance_aggregate", "kind": "aggregate", "data": {"phase": phase},
            "agg": {"samples": batch["samples"], "window_start": batch["first"], "window_end": batch["last"],
                    "dimensions": dict(dimensions), "metrics": metrics},
        }
        if attempt:
            payload["ctx"] = {"attempt_id": attempt}
        self._write(payload)

    def _consume(self, payload):
        if (payload["event"] == "gallery.source_summary" and payload.get("kind") == "aggregate"
                and type(payload.get("data", {}).get("samples")) is int):
            with self.admission.lock:
                self.admission.counts["source_summary_samples"] += payload["data"]["samples"]
        phase = payload["event"].removeprefix("gallery.")
        if payload.get("kind") != "measurement" or phase not in _AGGREGATED:
            self._write(payload)
            return
        fields = payload["data"]
        attempt = payload.get("ctx", {}).get("attempt_id")
        # Fixed cardinality/metric bounds, not an extensible-object cache.
        # Extra fields are preserved raw rather than silently discarded.
        if (any(key not in _METRICS and key not in _DIMENSIONS and key not in {"query_busy", "preparation_busy"} for key in fields)
                or any(type(fields[key]) not in (str, bool, int, float) for key in _DIMENSIONS if key in fields)
                or any(isinstance(fields.get(key), str) and len(fields[key]) > 128 for key in _DIMENSIONS)
                or (attempt is not None and (type(attempt) is not str or len(attempt) > 128))
                or not any(name in _METRICS and type(value) in (int, float) for name, value in fields.items())):
            self._write(payload)
            return
        # Wheel readiness and >=100ms outliers must remain individually observable.
        # This record is not a second observation in the aggregate population.
        if phase == "scroll_intent" or (phase == "gui_gap" and fields.get("elapsed_ms", 0) >= 100):
            self._write(payload)
        dimensions = tuple((key, fields[key]) for key in _DIMENSIONS if key in fields)
        key = (phase, dimensions, attempt)
        if key not in self.batches and len(self.batches) >= 64:
            self._flush_batch(next(iter(self.batches)))
        batch = self.batches.setdefault(key, {
            "samples": 0, "first": payload["ts"], "last": payload["ts"],
            "first_mono": payload["mono_ns"], "last_mono": payload["mono_ns"], "metrics": {},
        })
        batch["samples"] += 1
        batch["first"] = min(batch["first"], payload["ts"])
        batch["last"] = max(batch["last"], payload["ts"])
        batch["first_mono"] = min(batch["first_mono"], payload["mono_ns"])
        batch["last_mono"] = max(batch["last_mono"], payload["mono_ns"])
        with self.admission.lock:
            self.admission.counts["aggregate_source_samples"] += 1
        for name, value in fields.items():
            if name not in _METRICS or type(value) not in (int, float):
                continue
            # The former listener received one-decimal formatted measurements.
            # Retain that population for legacy aggregate/report parity, while
            # individual typed observations retain their full source value.
            number = round(value, 1) if type(value) is float else value
            batch["metrics"].setdefault(name, []).append(number)
        if batch["samples"] >= 64 or batch["last_mono"] - batch["first_mono"] >= 10_000_000_000:
            self._flush_batch(key)

    def _monitor(self):
        try:
            self._control("session.started", self.startup)
            while True:
                if self._deadline is not None and monotonic() >= self._deadline:
                    self.admission.expired.set()
                if self.admission.expired.is_set():
                    with self.admission.lock:
                        self.admission.loss("shutdown_timeout", "ERROR", True)
                    self._flush_losses()
                    break
                try:
                    payload, size = self.queue.get(timeout=0.05)
                except queue.Empty:
                    with self.admission.lock:
                        drained = self.admission.inflight == 0 and self.queue.empty()
                    if self.admission.stopping.is_set() and drained:
                        break
                else:
                    with self.admission.lock:
                        self.admission.pending_bytes -= size
                    self._consume(payload)
                    with self.admission.lock:
                        self.admission.counts["processed_records"] += 1
                    self.queue.task_done()
                self._flush_losses()
            for key in tuple(self.batches):
                self._flush_batch(key)
            self._flush_losses()
            counts, _ = self.admission.snapshot()
            if self._deadline is not None and monotonic() >= self._deadline:
                self.admission.expired.set()
                self.sink.warn()
            if not self.failure and not self.sink.failed and not self.admission.expired.is_set():
                # The seal reports loss honestly; readers must reject any nonzero
                # loss/rejection count even when closure itself succeeded.
                self._control("session.closed", {**counts, "written_records": self.sink.seq + 1,
                                                  "last_seq": self.sink.seq + 1})
        except Exception:
            with self.admission.lock:
                self.admission.counts["writer_failures"] += 1
            self.failure = True
            self.sink.warn()
        finally:
            try:
                self.sink.close()
            except Exception:
                with self.admission.lock:
                    self.admission.counts["writer_failures"] += 1
                self.sink.failed = True
                self.sink.warn()

    def stop(self, timeout=2.0):
        self._deadline = monotonic() + timeout
        self.sink.close_deadline = self._deadline
        self.admission.stopping.set()
        if self._thread is not None:
            self._thread.join(timeout)
            if self._thread.is_alive():
                self.admission.expired.set()
                self.sink.warn()
            else:
                self._thread = None
