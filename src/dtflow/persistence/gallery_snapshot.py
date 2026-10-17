"""Atomic, immutable browsing publication; never authoritative execution state.

Only committed gallery columns and removal availability are projected. No raw
database/WAL copies, image scans, provider calls or executable queue state.
"""

import json
import logging
import os
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from dtflow.persistence.cache_coordination import cache_access, category_path, check_path
from dtflow.persistence.original_io import recycle_batch_lock
from dtflow.persistence.store import PersistenceError

log = logging.getLogger(__name__)
FILENAME = "gallery_snapshot.sqlite3"
VERSION = 1
MAX_SNAPSHOT_BYTES = 2 * 1024**3
PUBLICATION_SECONDS = 10
_enabled = True
_pool = None
_pool_lock = threading.Lock()
_ATTEMPTS_SQL = """CREATE TABLE attempts (
    attempt_id TEXT PRIMARY KEY, request_id TEXT, backend_id TEXT,
    updated_at_utc TEXT, request_json TEXT, artifacts_json TEXT,
    state TEXT, remote_outcome TEXT)"""


def configure(*, no_cache: bool) -> None:
    """The process-wide cache-bypass launch option also bypasses this read model."""
    global _enabled
    _enabled = not no_cache


def path_for(state: Path) -> Path:
    if not _enabled:
        raise PersistenceError("Gallery snapshot bypassed for this run.")
    path = category_path(state, "derived") / FILENAME
    check_path(path)
    return path


@contextmanager
def reader(state: Path):
    with cache_access(state, timeout=0.5):
        with _reader(state) as result:
            yield result


@contextmanager
def _reader(state: Path):
    """One frozen connection for an entire page/filter/anchor operation."""
    from dtflow.persistence.gallery import lora_order

    path = path_for(state)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_SNAPSHOT_BYTES:
        raise PersistenceError("No supported gallery snapshot is available.")
    uri = path.as_uri() + "?mode=ro&immutable=1"
    db = sqlite3.connect(uri, uri=True, timeout=1)
    try:
        db.row_factory = sqlite3.Row
        if db.execute("PRAGMA user_version").fetchone()[0] != VERSION:
            raise PersistenceError("Unsupported gallery snapshot version.")
        info = db.execute("SELECT version,published_utc,signature FROM snapshot_info").fetchone()
        if info is None or info[0] != VERSION or len(info[2]) > 4096:
            raise PersistenceError("Unsupported gallery snapshot metadata.")
        stamp = datetime.fromisoformat(info[1])
        if stamp.tzinfo is None:
            raise PersistenceError("Invalid gallery snapshot timestamp.")
        # Attach this same connection's immutable main database, not a fresh
        # pathname that might already refer to a newer atomic publication.
        db.execute("ATTACH DATABASE ':memory:' AS image_recycle")
        db.execute("CREATE TABLE image_recycle.records AS SELECT * FROM main.records")
        db.execute("CREATE INDEX image_recycle.record_identity ON records(identity)")
        db.create_function("fold", 1, lambda text: str(text or "").casefold(), deterministic=True)
        db.create_function("lora_order", 1, lora_order, deterministic=True)
        db.execute("PRAGMA query_only=ON")
        deadline = time.monotonic() + 5
        db.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
        yield db, info[1]
    except (sqlite3.Error, ValueError, TypeError, IndexError) as exc:
        raise PersistenceError("Gallery snapshot could not be read.") from exc
    finally:
        db.close()


def _signature(db) -> str:
    attempts = db.execute("""SELECT count(*),max(updated_at_utc),
        coalesce(sum(length(request_json)+length(artifacts_json)),0)
        FROM attempts WHERE state='succeeded' AND remote_outcome='succeeded'""").fetchone()
    outcomes = db.execute("SELECT count(*),max(updated_utc) FROM image_recycle.records").fetchone()
    return json.dumps([VERSION, list(attempts), list(outcomes)])


@contextmanager
def _source(state):
    from dtflow.persistence.gallery import reader as live_reader

    with live_reader(state) as db:
        if db is not None:
            yield db
        else:
            # A clean close before any generation still publishes an empty
            # browsing model, without initializing the execution database.
            db = sqlite3.connect(":memory:")
            try:
                db.execute(_ATTEMPTS_SQL)
                db.execute("ATTACH DATABASE ':memory:' AS image_recycle")
                db.execute("CREATE TABLE image_recycle.records (identity TEXT,outcome TEXT,updated_utc TEXT)")
                yield db
            finally:
                db.close()


def _publication_budget(db, deadline):
    if time.monotonic() > deadline:
        raise TimeoutError("Gallery publication exceeded its work budget.")
    pages = db.execute("PRAGMA page_count").fetchone()[0]
    page_size = db.execute("PRAGMA page_size").fetchone()[0]
    if pages * page_size > MAX_SNAPSHOT_BYTES:
        raise OSError("Gallery publication exceeds the supported size.")


def publish_if_changed(state: Path) -> bool:
    """Best-effort worker publication after a session/clean close; retain old on failure."""
    if not _enabled:
        return False

    temporary = None
    started = time.monotonic()
    deadline = started + PUBLICATION_SECONDS
    stage = "source"
    try:
        # Prevent removal/reconciliation from changing the availability projection
        # mid-publication. Do not hold the original-file lease or delay paid saves.
        with recycle_batch_lock(state, timeout=0.25), cache_access(state), _source(state) as source:
            destination = path_for(state)
            temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
            destination.parent.mkdir(parents=True, exist_ok=True)
            # Old empty compatibility ledgers have no updated_utc column.
            columns = {row[1] for row in source.execute("PRAGMA image_recycle.table_info(records)")}
            if "updated_utc" in columns:
                signature = _signature(source)
            else:
                attempts = source.execute("""SELECT count(*),max(updated_at_utc),
                    coalesce(sum(length(request_json)+length(artifacts_json)),0)
                    FROM attempts WHERE state='succeeded' AND remote_outcome='succeeded'""").fetchone()
                signature = json.dumps([VERSION, list(attempts), [0, None]])
            if destination.is_symlink():
                raise PersistenceError("Redirected gallery publication refused.")
            try:
                if destination.is_file():
                    old = sqlite3.connect(destination.as_uri() + "?mode=ro&immutable=1", uri=True)
                    try:
                        if old.execute("PRAGMA user_version").fetchone()[0] != VERSION:
                            raise PersistenceError("Unsupported existing gallery snapshot was preserved.")
                        info = old.execute("SELECT version,signature FROM snapshot_info").fetchone()
                    finally:
                        old.close()
                    if info is not None and info[0] == VERSION and info[1] == signature:
                        log.debug(
                            "gallery snapshot unchanged; publication skipped elapsed_ms=%d",
                            (time.monotonic() - started) * 1000,
                        )
                        return False
            except (OSError, sqlite3.Error):
                pass  # First publication or unsupported old projection; never touch source state.
            stage = "projection"
            with temporary.open("xb"):
                pass
            output = sqlite3.connect(temporary)
            try:
                output.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
                _publication_budget(output, deadline)
                output.execute("PRAGMA journal_mode=DELETE")
                output.execute("PRAGMA synchronous=FULL")
                output.execute(_ATTEMPTS_SQL)
                rows = source.execute("""SELECT attempt_id,request_id,backend_id,updated_at_utc,
                    request_json,artifacts_json,state,remote_outcome FROM attempts
                    WHERE state='succeeded' AND remote_outcome='succeeded'""")
                count = 0
                while batch := rows.fetchmany(128):
                    _publication_budget(output, deadline)
                    output.executemany("INSERT INTO attempts VALUES (?,?,?,?,?,?,?,?)", batch)
                    _publication_budget(output, deadline)
                    count += len(batch)
                output.execute("CREATE TABLE records (identity TEXT PRIMARY KEY, outcome TEXT NOT NULL)")
                outcomes = source.execute("SELECT identity,outcome FROM image_recycle.records")
                while batch := outcomes.fetchmany(128):
                    _publication_budget(output, deadline)
                    output.executemany("INSERT INTO records VALUES (?,?)", batch)
                    _publication_budget(output, deadline)
                output.execute("CREATE TABLE snapshot_info (version INTEGER,published_utc TEXT,signature TEXT)")
                output.execute(
                    "INSERT INTO snapshot_info VALUES (?,?,?)", (VERSION, datetime.now(UTC).isoformat(), signature)
                )
                output.execute(f"PRAGMA user_version={VERSION}")
                _publication_budget(output, deadline)
                output.commit()
                _publication_budget(output, deadline)
            finally:
                output.close()
            stage = "flush"
            # Windows _commit/FlushFileBuffers requires a writable descriptor.
            with temporary.open("r+b") as stream:
                if os.fstat(stream.fileno()).st_size > MAX_SNAPSHOT_BYTES:
                    raise OSError("Gallery publication exceeds the supported size.")
                os.fsync(stream.fileno())
            if time.monotonic() > deadline:
                raise TimeoutError("Gallery publication exceeded its finalization budget.")
            stage = "replace"
            os.replace(temporary, destination)
        log.info("gallery snapshot published attempts=%d elapsed_ms=%d", count, (time.monotonic() - started) * 1000)
        return True
    except Exception as exc:
        # Publication is advisory and must not change successful generation,
        # invoke retries, expose private paths or delete the previous snapshot.
        log.warning(
            "gallery snapshot not published stage=%s reason=%s errno=%s winerror=%s",
            stage,
            type(exc).__name__,
            getattr(exc, "errno", None),
            getattr(exc, "winerror", None),
        )
        return False
    finally:
        try:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        except OSError:
            log.debug("gallery snapshot scratch cleanup unavailable")


def schedule_publish(state: Path):
    """One background worker; signature checks skip redundant queued requests."""
    global _pool
    if not _enabled:
        return None
    with _pool_lock:
        if _pool is None:
            _pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dtflow-gallery-publication")
        try:
            return _pool.submit(publish_if_changed, Path(state))
        except RuntimeError:
            log.warning("gallery publication worker unavailable during shutdown")
            return None


def finish_at_close(state: Path) -> None:
    """Called after Qt exits and generation settlement, never inside its event loop."""
    future = schedule_publish(state)
    if future is not None:
        try:
            future.result()
        except Exception as exc:
            log.warning("gallery close publication unavailable reason=%s", type(exc).__name__)
