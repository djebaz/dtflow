"""Logging contract: JSONL lifecycle/correlation, bounded retention, sanitization and write-failure behavior."""

import json
import logging
import os
import time

import pytest

from dtflow import logging_setup
from dtflow.logging_setup import ATTEMPT, LoggingSetupError, configure_logging, sanitize

BUILD = {"version": "9.9.9", "source_commit": "0123456789abcdef0123", "source_status": "clean"}


@pytest.fixture(autouse=True)
def isolated_logger():
    yield
    logger = logging.getLogger("dtflow")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
    logger.setLevel(logging.NOTSET)


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
        old = tmp_path / f"dtflow-old{index:02}.jsonl"
        old.write_text("x")
        (tmp_path / f"{old.name}.1").write_text("x")
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


def test_sanitize_leaves_ordinary_text_alone():
    assert sanitize("attempt succeeded outputs=1", {}) == "attempt succeeded outputs=1"


def test_unusable_destination_is_reported_without_raising_into_generation(tmp_path, capsys):
    blocked = tmp_path / "file"
    blocked.write_text("not a directory")
    with pytest.raises(LoggingSetupError) as caught:
        configure_logging(directory=blocked / "logs")
    assert "Log destination" in str(caught.value) and str(blocked) not in str(caught.value)


def test_runtime_write_failure_is_reported_once_and_never_raises(tmp_path, capsys, monkeypatch):
    class Broken:
        def tell(self):
            return 0

        def write(self, _):
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
