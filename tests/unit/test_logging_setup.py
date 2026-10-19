"""Logging contract: JSONL lifecycle/correlation, bounded retention, sanitization and write-failure behavior."""

import json
import logging
import multiprocessing
import os
import sys
import threading
import time
from pathlib import Path

import pytest

from dtflow import _diagnostic_schema as schema
from dtflow import _diagnostic_writer as writer
from dtflow import logging_setup
from dtflow._diagnostic_ownership import ownership
from dtflow.diagnostics import event
from dtflow.logging_setup import ATTEMPT, LoggingSetupError, configure_logging, sanitize

BUILD = {"version": "9.9.9", "source_commit": "0123456789abcdef0123", "source_status": "clean"}


@pytest.fixture(autouse=True)
def isolated_logger(monkeypatch):
    logger = logging.getLogger("dtflow")
    previous_level, previous_propagation = logger.level, logger.propagate
    listeners = []
    start = writer.DiagnosticListener.start

    def remember(listener):
        listeners.append(listener)
        return start(listener)

    monkeypatch.setattr(writer.DiagnosticListener, "start", remember)
    try:
        yield
    finally:
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            if isinstance(handler, writer.CaptureHandler):
                with handler.admission.lock:
                    handler.admission.accepting = False
        for listener in listeners:
            listener.stop()
        logger.setLevel(previous_level)
        logger.propagate = previous_propagation


def inactive_session(directory, name):
    path = directory / f"dtflow-{name}.jsonl"
    path.write_text("retained evidence\n", encoding="utf-8")
    ownership(directory / f"dtflow-{name}.owner", create=True).close()
    return path


def hold_owner(path, ready, release):
    # Separate process: file age alone must never authorize deleting its file.
    with ownership(Path(path)):
        ready.set()
        if not release.wait(15):
            raise TimeoutError("Parent did not release the ownership fixture")


def payload(value=1):
    return {
        "ts": "2026-10-19T12:00:00.000Z",
        "level": "INFO",
        "component": "dtflow.test",
        "event": "test.observed",
        "data": {"value": value},
    }


def read(session):
    return session.path.read_text(encoding="utf-8")


def records(session):
    return [json.loads(line) for line in read(session).splitlines()]


def test_destination_levels_and_correlation(tmp_path):
    normal = configure_logging(directory=tmp_path / "normal", build=BUILD, backend="fake", session="s1")
    token = ATTEMPT.set("attempt-42")
    try:
        logging.getLogger("dtflow.services.test").info("lifecycle event")
        logging.getLogger("dtflow.services.test").debug("development-only detail")
    finally:
        ATTEMPT.reset(token)
    normal.close()
    rows = records(normal)
    assert normal.path.name == "dtflow-s1.jsonl"
    started = rows[0]
    assert started["event"] == "session.started" and started["data"]["session_id"] == "s1"
    assert (started["data"]["version"], started["data"]["source_commit"], started["data"]["backend"]) == (
        "9.9.9",
        "0123456789abcdef0123",
        "fake",
    )
    assert {
        "component": "dtflow.services.test",
        "msg": "lifecycle event",
        "ctx": {"attempt_id": "attempt-42"},
    }.items() <= next(row for row in rows if row.get("msg") == "lifecycle event").items()
    assert "development-only detail" not in read(normal) and rows[-1]["event"] == "session.closed"
    developer = configure_logging(directory=tmp_path / "dev", dev=True, session="s2")
    logging.getLogger("dtflow.services.test").debug("development-only detail")
    developer.close()
    assert "development-only detail" in read(developer)


def test_sessions_use_separate_files_one_at_a_time_and_retention_is_bounded(tmp_path):
    for index in range(25):
        old = inactive_session(tmp_path, f"old{index:02}")
        (tmp_path / f"{old.name}.1").write_text("x", encoding="utf-8")
        stamp = time.time() - 1000 + index
        for path in (old, tmp_path / f"{old.name}.1"):
            os.utime(path, (stamp, stamp))
    first = configure_logging(directory=tmp_path, session="a")
    with pytest.raises(LoggingSetupError):
        configure_logging(directory=tmp_path, session="b")  # one diagnostic session per process
    first.close()
    second = configure_logging(directory=tmp_path, session="b")
    second.close()
    with pytest.raises(LoggingSetupError):
        configure_logging(directory=tmp_path, session="b")  # never appends to an existing session
    sessions = sorted(path.name for path in tmp_path.glob("dtflow-*.jsonl"))
    assert first.path != second.path and {"dtflow-a.jsonl", "dtflow-b.jsonl"} <= set(sessions)
    assert len(sessions) <= logging_setup.RETAINED_SESSIONS  # pruning keeps newest, including the live one
    assert "dtflow-old00.jsonl" not in sessions and not (tmp_path / "dtflow-old00.jsonl.1").exists()


def test_rotation_bounds_file_size(tmp_path, monkeypatch):
    monkeypatch.setattr(logging_setup, "MAX_BYTES", 400)
    session = configure_logging(directory=tmp_path, session="rot")
    for index in range(100):
        logging.getLogger("dtflow.t").info("line %03d %s", index, "y" * 40)
    session.close()
    files = sorted(tmp_path.glob("dtflow-rot.jsonl*"))
    assert 1 < len(files) <= logging_setup.BACKUPS + 1
    # A file stays within the bound unless a single record alone exceeds it.
    assert all(path.stat().st_size <= 400 or len(path.read_bytes().splitlines()) == 1 for path in files)


def test_secrets_never_reach_the_file_including_exceptions(tmp_path, monkeypatch):
    monkeypatch.setenv("DRAWTHINGS_API_KEY", "FAKE-API-KEY-SENTINEL")
    monkeypatch.setenv("DRAWTHINGS_SHARED_SECRET", "FAKE-SHARED-SENTINEL")
    session = configure_logging(directory=tmp_path, session="sec", dev=True)
    log = logging.getLogger("dtflow.t")
    log.info("key FAKE-API-KEY-SENTINEL and shared FAKE-SHARED-SENTINEL")
    log.info("Authorization: Bearer abc.def-ghi header and api_key=hunter2 and token: tok123")
    log.info("payload %s", "QUJD" * 100)
    try:
        raise RuntimeError("failed with FAKE-API-KEY-SENTINEL near /root/secrets Bearer zzz")
    except RuntimeError:
        log.exception("boom")
    session.close()
    text = read(session)
    for secret in (
        "FAKE-API-KEY-SENTINEL",
        "FAKE-SHARED-SENTINEL",
        "abc.def-ghi",
        "hunter2",
        "tok123",
        "QUJD" * 20,
        "zzz",
    ):
        assert secret not in text, secret
    assert "Traceback" in text and "[redacted" in text


@pytest.mark.parametrize("text", ["attempt succeeded outputs=1", "image/png", "3/4", "sd_xl/base.ckpt", "Trash/Delete"])
def test_sanitize_leaves_ordinary_text_alone(text):
    assert sanitize(text, {}) == text


def test_unusable_destination_is_reported_without_raising_into_generation(tmp_path, capsys):
    blocked = tmp_path / "file"
    blocked.write_text("not a directory")
    with pytest.raises(LoggingSetupError) as caught:
        configure_logging(directory=blocked / "logs")
    assert "Log destination" in str(caught.value) and str(blocked) not in str(caught.value)


@pytest.mark.parametrize("failure", ["disk", "partial", "closed"])
def test_runtime_write_failure_is_reported_once_and_never_raises(tmp_path, capsys, monkeypatch, failure):
    class Broken:
        def tell(self):
            return 0

        def write(self, line):
            if failure == "partial":
                return len(line) - 1
            if failure == "closed":
                raise ValueError("closed stream FAKE-API-KEY-SENTINEL")
            raise OSError("disk full FAKE-API-KEY-SENTINEL")

        def flush(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(logging_setup.JsonlHandler, "_open", lambda self: Broken())
    session = configure_logging(directory=tmp_path, session="w")
    for _ in range(3):
        logging.getLogger("dtflow.t").info("message")
    session.close()
    err = capsys.readouterr().err
    assert err.count("Diagnostic evidence is incomplete") == 1 and "SENTINEL" not in err
    counts, _ = session.capture_handler.admission.snapshot()
    assert session.handler.failed and counts["writer_failures"] == 1
    assert counts["unwritten_records"] == 3 and counts["serialization_failures"] == 0
    assert not any(row["event"] == "session.closed" for row in records(session))


def test_rotation_collision_retries_without_growth_or_false_clean_evidence(tmp_path, monkeypatch):
    clock, attempts, notices = [100.0], [], []
    monkeypatch.setattr(writer, "monotonic", lambda: clock[0])
    sink = writer.JsonlHandler(tmp_path / "rotation.jsonl", max_bytes=10000, backups=3, report_error=notices.append)
    listener = writer.DiagnosticListener(writer.Admission(), sink, {})
    rollover = sink.doRollover

    def collide_once():
        attempts.append(clock[0])
        if len(attempts) == 1:
            raise PermissionError("reader holds file PRIVATE-SENTINEL")
        rollover()

    monkeypatch.setattr(sink, "doRollover", collide_once)
    try:
        assert listener._write(payload(1))
        size = Path(sink.baseFilename).stat().st_size
        sink.maxBytes = size + 1
        assert not listener._write(payload(2))
        assert not listener._write(payload(3))
        listener._flush_losses()  # failed controls must not acknowledge lost evidence
        assert listener.last_losses == {} and attempts == [100.0]
        assert Path(sink.baseFilename).stat().st_size == size and not sink.failed
        clock[0] += 0.2
        listener._flush_losses()
        assert listener.last_losses["writer_error", "INFO", False] == 2
        assert listener._write(payload(4)) and not listener.failure
        counts, _ = listener.admission.snapshot()
        assert counts["unwritten_records"] == 2 and counts["writer_failures"] > 0
        assert listener._control("session.closed", {**counts, "written_records": sink.seq + 1})
    finally:
        sink.close()
    rows = [json.loads(line) for path in tmp_path.glob("rotation.jsonl*") for line in path.read_bytes().splitlines()]
    assert sorted(row["seq"] for row in rows) == list(range(1, len(rows) + 1))
    assert any(row["event"] == "session.closed" and row["data"]["writer_failures"] > 0 for row in rows)
    assert len(notices) == 1 and "SENTINEL" not in notices[0]


@pytest.mark.parametrize("failure", ["disappeared", "locked"])
def test_retention_io_errors_do_not_disable_the_new_session(tmp_path, monkeypatch, failure):
    stale = inactive_session(tmp_path, "old")
    monkeypatch.setattr(logging_setup, "RETAINED_SESSIONS", 1)
    if failure == "disappeared":
        original = Path.lstat

        def lstat(path, *args, **kwargs):
            if path == stale:
                raise FileNotFoundError("concurrent prune")
            return original(path, *args, **kwargs)

        monkeypatch.setattr(Path, "lstat", lstat)
    else:
        original = Path.unlink

        def unlink(path, *args, **kwargs):
            if path == stale:
                raise PermissionError("reader has stale segment open")
            return original(path, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", unlink)
    session = configure_logging(directory=tmp_path, session="new")
    session.close()
    assert records(session)[-1]["event"] == "session.closed" and not session.handler.failed


def test_retention_preserves_unknown_owners_and_historical_files(tmp_path, monkeypatch):
    monkeypatch.setattr(logging_setup, "RETAINED_SESSIONS", 1)
    preserved = [tmp_path / name for name in ("dtflow-unknown.jsonl", "dtflow-old.log", "dtflow-old-gallery.csv")]
    for path in preserved:
        path.write_bytes(b"private historical evidence")
    stale = inactive_session(tmp_path, "owned")
    session = configure_logging(directory=tmp_path, session="new")
    session.close()
    assert all(path.read_bytes() == b"private historical evidence" for path in preserved)
    assert not stale.exists() and (tmp_path / "dtflow-owned.owner").is_file()


def test_retention_skips_an_idle_owner_in_another_process(tmp_path, monkeypatch):
    stale = inactive_session(tmp_path, "idle")
    os.utime(stale, (1, 1))
    monkeypatch.setattr(logging_setup, "RETAINED_SESSIONS", 1)
    context = multiprocessing.get_context("spawn")
    ready, release = context.Event(), context.Event()
    process = context.Process(target=hold_owner, args=(str(tmp_path / "dtflow-idle.owner"), ready, release))
    process.start()
    try:
        assert ready.wait(5), "Ownership fixture did not become ready"
        session = configure_logging(directory=tmp_path, session="new")
        session.close()
        assert stale.is_file() and records(session)[-1]["event"] == "session.closed"
    finally:
        release.set()
        process.join(5)
        if process.is_alive():
            process.terminate()
            process.join(5)
    assert process.exitcode == 0
    logging_setup._prune(tmp_path, "new")
    assert not stale.exists() and (tmp_path / "dtflow-idle.owner").is_file()


def test_capture_formats_safe_primitives_before_redaction_and_refreshes_secrets(monkeypatch):
    record = logging.LogRecord(
        "dtflow.test", logging.INFO, __file__, 1, "progress %d/%d: %s", (3, 4, "image/png"), None
    )
    captured, _ = schema.capture(record)
    assert captured["msg"] == "progress 3/4: image/png"
    record.msg, record.args = "value %(value)s", {"value": "NEW-SECRET-SENTINEL"}
    monkeypatch.setenv("DRAWTHINGS_API_KEY", "OLD-SECRET-SENTINEL")
    assert "NEW-SECRET-SENTINEL" in schema.capture(record)[0]["msg"]
    monkeypatch.setenv("DRAWTHINGS_API_KEY", "NEW-SECRET-SENTINEL")
    assert "NEW-SECRET-SENTINEL" not in schema.capture(record)[0]["msg"]
    assert "[redacted]" in schema.capture(record)[0]["msg"]


@pytest.mark.parametrize(
    "template,args", [("%(x)999999999s", {"x": "a"}), ("%.999999999f", (1.0,)), ("%*s", (100, "a"))]
)
def test_capture_rejects_unbounded_format_expansion(template, args):
    record = logging.LogRecord("dtflow.test", logging.INFO, __file__, 1, "placeholder", (), None)
    record.msg, record.args = template, args
    with pytest.raises(schema.CaptureRejected):
        schema.capture(record)


@pytest.mark.parametrize("path", [r"D:\Shared\My Pictures\private.png", "/srv/shared/My Pictures/private.png"])
def test_spaced_private_paths_are_redacted_without_leaking_suffixes(path):
    text = sanitize(f'failed reading "{path}"', {})
    assert "[redacted-path]" in text and "Pictures" not in text and "private.png" not in text


def test_tracebacks_keep_package_location_without_private_root_or_locals():
    location = str(Path(schema.__file__).parent / "f9_trace_fixture.py")
    try:
        exec(
            compile("private_local = 'PRIVATE-LOCAL-SENTINEL'\nraise ValueError('fixture failure')", location, "exec"),
            {},
        )
    except ValueError:
        record = logging.LogRecord("dtflow.test", logging.ERROR, __file__, 1, "failed", (), sys.exc_info())
    captured, _ = schema.capture(record)
    trace = captured["error"]["traceback"]
    assert "dtflow/f9_trace_fixture.py" in trace and "line 2" in trace
    assert str(Path(schema.__file__).parent.parent) not in trace and "PRIVATE-LOCAL-SENTINEL" not in trace


def test_capture_byte_pressure_releases_reservations_without_misclassifying_rejection(monkeypatch):
    monkeypatch.setattr(writer, "QUEUE_BYTES", 2048)
    state = writer.Admission()
    handler = writer.CaptureHandler(state)
    try:
        handler.emit(logging.LogRecord("dtflow.test", logging.INFO, __file__, 1, "x" * 1024, (), None))
        counts, losses = state.snapshot()
        assert counts["dropped"] == 1 and counts["capture_rejected"] == 0 and counts["admitted"] == 0
        assert losses["queue_bytes", "INFO", False] == 1
        assert state.capture_bytes == state.pending_bytes == state.inflight == 0 and state.queue.empty()
    finally:
        handler.close()


def test_small_concurrent_captures_do_not_reserve_a_megabyte_each(monkeypatch):
    state = writer.Admission()
    handler = writer.CaptureHandler(state)
    barrier = threading.Barrier(12, timeout=5)
    capture = writer.capture

    def capture_together(record, **kwargs):
        result = capture(record, **kwargs)
        barrier.wait()
        return result

    monkeypatch.setattr(writer, "capture", capture_together)
    threads = [
        threading.Thread(
            target=handler.emit,
            args=(logging.LogRecord("dtflow.test", logging.INFO, __file__, 1, "small", (), None),),
            daemon=True,
        )
        for _ in range(12)
    ]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(6)
        counts, _ = state.snapshot()
        assert all(not thread.is_alive() for thread in threads)
        assert counts["admitted"] == 12 and counts["dropped"] == counts["capture_rejected"] == 0
        assert state.capture_bytes == state.inflight == 0 and 0 < state.pending_bytes < writer.QUEUE_BYTES
        assert state.queue.qsize() == 12
    finally:
        barrier.abort()
        for thread in threads:
            thread.join(6)
        handler.close()


def test_idle_listener_flushes_source_window_without_waiting_for_close(tmp_path, monkeypatch):
    clock, written, flushed = [100.0], threading.Event(), threading.Event()
    monkeypatch.setattr(writer, "monotonic", lambda: clock[0])
    write = writer.JsonlHandler.write_record

    def observe(sink, item):
        write(sink, item)
        if item["event"] == "gallery.scroll_intent":
            written.set()
        elif item["event"] == "gallery.performance_aggregate":
            flushed.set()

    monkeypatch.setattr(writer.JsonlHandler, "write_record", observe)
    session = configure_logging(directory=tmp_path, session="idle", dev=True)
    try:
        event(
            logging.getLogger("dtflow.test"),
            "gallery.scroll_intent",
            level=logging.DEBUG,
            kind="measurement",
            source_mono_ns=100_000_000_000,
            data={"direction": "forward", "source": "wheel", "offset": 20, "mounted": 100},
        )
        assert written.wait(3), "Individual wheel receipt missing"
        assert not flushed.is_set()
        clock[0] = 110.1
        assert flushed.wait(3), "Idle aggregate was not flushed before close"
    finally:
        session.close()
    rows = records(session)
    aggregate = next(row for row in rows if row["event"] == "gallery.performance_aggregate")
    assert aggregate["data"]["phase"] == "scroll_intent"
    assert aggregate["agg"]["samples"] == 1 and aggregate["agg"]["window_start"] == aggregate["agg"]["window_end"]
    assert rows[-1]["data"]["aggregate_source_samples"] == 1 and rows[-1]["data"]["writer_failures"] == 0


def test_sink_close_is_serialized_with_an_in_progress_write(tmp_path, monkeypatch):
    sink = writer.JsonlHandler(tmp_path / "close.jsonl", max_bytes=10000, backups=3)
    entered, release, close_started, closed = (threading.Event() for _ in range(4))
    errors = []
    stream = open(sink.baseFilename, "ab")

    class BlockingStream:
        def tell(self):
            return stream.tell()

        def write(self, line):
            entered.set()
            if not release.wait(5):
                raise TimeoutError("Write fixture was not released")
            return stream.write(line)

        def flush(self):
            stream.flush()

        def close(self):
            stream.close()

    sink.stream = BlockingStream()

    def write():
        try:
            sink.write_record(payload())
        except Exception as exc:
            errors.append(exc)

    def close():
        close_started.set()
        try:
            sink.close()
        except Exception as exc:
            errors.append(exc)
        finally:
            closed.set()

    writing, closing = threading.Thread(target=write, daemon=True), threading.Thread(target=close, daemon=True)
    writing.start()
    try:
        assert entered.wait(3)
        closing.start()
        assert close_started.wait(3)
        assert not closed.wait(0.05), "Close bypassed the writer's handler lock"
    finally:
        release.set()
        writing.join(6)
        if closing.ident is not None:
            closing.join(6)
        sink.close()
    assert not writing.is_alive() and not closing.is_alive() and not errors
    assert not sink.failed and json.loads(Path(sink.baseFilename).read_bytes())["event"] == "test.observed"


def test_shutdown_timeout_never_certifies_an_unfinished_session(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    write = writer.JsonlHandler.write_record

    def delayed(sink, item):
        if item["event"] == "session.started":
            entered.set()
            if not release.wait(5):
                raise TimeoutError("Startup fixture was not released")
        return write(sink, item)

    monkeypatch.setattr(writer.JsonlHandler, "write_record", delayed)
    session = configure_logging(directory=tmp_path, session="timeout", report_error=lambda _message: None)
    stop = session.listener.stop
    monkeypatch.setattr(session.listener, "stop", lambda: stop(timeout=0.01))
    try:
        assert entered.wait(3)
        session.close()
        assert session.capture_handler.admission.expired.is_set()
        assert session.listener._thread is not None
    finally:
        release.set()
        stop(timeout=2)
    assert session.listener._thread is None
    assert not any(row["event"] == "session.closed" for row in records(session))
