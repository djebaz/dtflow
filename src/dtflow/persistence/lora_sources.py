"""Durable provenance of downloaded LoRA source files (table ``lora_sources`` in ``assets.sqlite3``).

The table is additive: ``AssetRegistry`` creates it with the other tables when a *writer* opens the database, so an
existing schema-6 database needs no migration and no version bump. Readers never create anything and read a missing
table as "no sources". Only bounded normalised fields are stored: never a token, a signed URL or a raw provider
response. The source file's bytes live in ``<state>/lora_sources/`` and are keyed by their SHA-256.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any

TABLE = "lora_sources"
FILE_PRESENT = "present"
FILE_DELETED = "deleted"
STATES = ("downloaded", "converted", "failed", "refused")
FAMILY_SOURCES = ("unknown", "provider", "file", "user")
MAX_JSON_CHARS = 4000

SCHEMA = f"""CREATE TABLE IF NOT EXISTS {TABLE} (
        sha256 TEXT PRIMARY KEY,
        size INTEGER NOT NULL,
        provider TEXT NOT NULL,
        model_id INTEGER,
        version_id INTEGER,
        file_id INTEGER,
        model_name TEXT NOT NULL DEFAULT '',
        version_name TEXT NOT NULL DEFAULT '',
        creator TEXT,
        original_filename TEXT,
        license TEXT NOT NULL DEFAULT '',
        tags TEXT NOT NULL DEFAULT '[]',
        trigger_words TEXT NOT NULL DEFAULT '[]',
        trigger_words_state TEXT NOT NULL DEFAULT 'missing',
        base_model_label TEXT,
        classified_family TEXT,
        classification_source TEXT NOT NULL DEFAULT 'unknown',
        classification_evidence TEXT NOT NULL DEFAULT '',
        declared_family TEXT,
        family TEXT,
        family_source TEXT NOT NULL DEFAULT 'unknown',
        hash_verified INTEGER NOT NULL DEFAULT 0,
        downloaded_at_utc TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT 'downloaded',
        state_detail TEXT NOT NULL DEFAULT '',
        file_state TEXT NOT NULL DEFAULT 'present',
        converted_sha256 TEXT,
        converted_size INTEGER,
        updated_at_utc TEXT NOT NULL,
        CHECK (state IN ('downloaded', 'converted', 'failed', 'refused')),
        CHECK (file_state IN ('present', 'deleted')),
        CHECK (classification_source IN ('unknown', 'provider', 'file')),
        CHECK (family_source IN ('unknown', 'provider', 'file', 'user'))
    )"""

_INDEXES = (
    f"CREATE INDEX IF NOT EXISTS {TABLE}_family ON {TABLE}(family)",
    f"CREATE INDEX IF NOT EXISTS {TABLE}_converted ON {TABLE}(converted_sha256)",
)
SCHEMA_STATEMENTS = (SCHEMA, *_INDEXES)


@dataclass(frozen=True, slots=True)
class SourceRecord:
    """Normalised provenance for one verified, downloaded file. All provider fields are optional."""

    sha256: str
    size: int
    provider: str
    downloaded_at_utc: str
    model_id: int | None = None
    version_id: int | None = None
    file_id: int | None = None
    model_name: str = ""
    version_name: str = ""
    creator: str | None = None
    original_filename: str | None = None
    license: dict = field(default_factory=dict)
    tags: tuple[str, ...] = ()
    trigger_words: tuple[str, ...] = ()
    trigger_words_state: str = "missing"
    base_model_label: str | None = None  # the provider's own label, never rewritten
    classified_family: str | None = None  # DTFlow's normalised family for that label, if any
    classification_source: str = "unknown"  # "provider" (label mapping), "file" (detector) or "unknown"
    classification_evidence: str = ""
    hash_verified: bool = False


def _bounded_json(value) -> str:
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    if len(text) > MAX_JSON_CHARS:
        raise ValueError("Source metadata field is too large to store.")
    return text


def has_table(db: sqlite3.Connection) -> bool:
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)).fetchone() is not None


def effective_family(declared: str | None, classified: str | None, source: str) -> tuple[str | None, str]:
    """A user correction wins without replacing the stored classification; unknown stays unknown."""
    if declared:
        return declared, "user"
    if classified:
        return classified, source
    return None, "unknown"


def _decode(row: sqlite3.Row) -> dict[str, Any]:
    value = {key: row[key] for key in row.keys()}
    for key in ("license", "tags", "trigger_words"):
        try:
            value[key] = json.loads(value[key]) if value[key] else None
        except ValueError:
            value[key] = None
    value["hash_verified"] = bool(value["hash_verified"])
    value["family_conflict"] = bool(
        value["declared_family"]
        and value["classified_family"]
        and value["declared_family"] != value["classified_family"]
    )
    return value


def upsert(db: sqlite3.Connection, record: SourceRecord, now: str) -> None:
    """Insert or refresh provenance. A user correction and the conversion state of an existing row are kept."""
    if record.classification_source not in ("unknown", "provider", "file"):
        raise ValueError("Unknown classification source.")
    existing = db.execute(f"SELECT declared_family FROM {TABLE} WHERE sha256=?", (record.sha256,)).fetchone()
    declared = existing["declared_family"] if existing else None
    family, family_source = effective_family(declared, record.classified_family, record.classification_source)
    values = (
        record.size,
        record.provider,
        record.model_id,
        record.version_id,
        record.file_id,
        record.model_name[:200],
        record.version_name[:200],
        record.creator,
        record.original_filename,
        _bounded_json(record.license),
        _bounded_json(list(record.tags)),
        _bounded_json(list(record.trigger_words)),
        record.trigger_words_state,
        record.base_model_label,
        record.classified_family,
        record.classification_source,
        record.classification_evidence[:500],
        family,
        family_source,
        int(record.hash_verified),
        now,
    )
    if existing is None:
        db.execute(
            f"INSERT INTO {TABLE} (sha256, size, provider, model_id, version_id, file_id, model_name, version_name, "
            "creator, original_filename, license, tags, trigger_words, trigger_words_state, base_model_label, "
            "classified_family, classification_source, classification_evidence, family, family_source, "
            "hash_verified, downloaded_at_utc, updated_at_utc) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (record.sha256, *values[:-1], record.downloaded_at_utc, now),
        )
    else:
        db.execute(
            f"UPDATE {TABLE} SET size=?, provider=?, model_id=?, version_id=?, file_id=?, model_name=?, "
            "version_name=?, creator=?, original_filename=?, license=?, tags=?, trigger_words=?, "
            "trigger_words_state=?, base_model_label=?, classified_family=?, classification_source=?, "
            "classification_evidence=?, family=?, family_source=?, hash_verified=?, updated_at_utc=? WHERE sha256=?",
            (*values, record.sha256),
        )


def select_all(db: sqlite3.Connection) -> tuple[dict[str, Any], ...]:
    if not has_table(db):
        return ()
    rows = db.execute(f"SELECT * FROM {TABLE} ORDER BY downloaded_at_utc, sha256").fetchall()
    return tuple(_decode(r) for r in rows)


def select_one(db: sqlite3.Connection, sha256: str) -> dict[str, Any] | None:
    if not has_table(db):
        return None
    row = db.execute(f"SELECT * FROM {TABLE} WHERE sha256=?", (sha256,)).fetchone()
    return None if row is None else _decode(row)


def set_state(
    db: sqlite3.Connection,
    sha256: str,
    now: str,
    *,
    state: str | None = None,
    detail: str | None = None,
    file_state: str | None = None,
    converted: tuple[str, int] | None = None,
) -> bool:
    if state is not None and state not in STATES:
        raise ValueError("Unknown source state.")
    sets, args = ["updated_at_utc=?"], [now]
    for column, value in (("state", state), ("state_detail", None if detail is None else detail[:500])):
        if value is not None:
            sets.append(f"{column}=?")
            args.append(value)
    if file_state is not None:
        sets.append("file_state=?")
        args.append(file_state)
    if converted is not None:
        sets.append("converted_sha256=?, converted_size=?")
        args.extend(converted)
    cursor = db.execute(f"UPDATE {TABLE} SET {', '.join(sets)} WHERE sha256=?", (*args, sha256))
    return cursor.rowcount == 1


def set_declared_family(db: sqlite3.Connection, sha256: str, family: str | None, now: str) -> bool:
    row = db.execute(
        f"SELECT classified_family, classification_source FROM {TABLE} WHERE sha256=?", (sha256,)
    ).fetchone()
    if row is None:
        return False
    effective, source = effective_family(family or None, row["classified_family"], row["classification_source"])
    db.execute(
        f"UPDATE {TABLE} SET declared_family=?, family=?, family_source=?, updated_at_utc=? WHERE sha256=?",
        (family or None, effective, source, now, sha256),
    )
    return True


def delete(db: sqlite3.Connection, sha256: str) -> bool:
    return db.execute(f"DELETE FROM {TABLE} WHERE sha256=?", (sha256,)).rowcount == 1
