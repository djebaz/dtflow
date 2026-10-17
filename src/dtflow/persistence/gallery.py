"""Bounded read-only artifact discovery; never own, migrate or reconcile execution state."""

import json
import re
import sqlite3
import time
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path

from dtflow.core.gallery import SIZE_CLASSES, GalleryQuery
from dtflow.persistence.image_recycle import attach_read_only
from dtflow.persistence.store import SCHEMA_VERSION, PersistenceError, patched_sqlite

# The saver commits image paths followed by metadata.json only after every durable write succeeds.
# These references include non-default --out locations. Failed/orphaned files are not discovery sources.
MAX_EXPORT_AVAILABILITY_BATCH = 128
MAX_RECYCLE_PREFLIGHT_BATCH = 128
_MANAGED_OUTPUT_KEY = re.compile(r"outputs/([a-f0-9]{32})/final-[1-9][0-9]*\.png\Z")


def _candidate_attempts(keys: tuple[str, ...]) -> tuple[str, ...] | None:
    """Give SQLite a necessary attempt substring filter, not ownership authority."""
    matches = tuple(_MANAGED_OUTPUT_KEY.fullmatch(key) for key in keys)
    if not matches or any(match is None for match in matches):
        return None
    return tuple(dict.fromkeys(match[1] for match in matches if match is not None))


BASE = """
WITH outputs AS (
 SELECT a.attempt_id, a.request_id, a.backend_id, a.updated_at_utc AS saved_at_utc,
        CASE WHEN json_valid(a.request_json) THEN a.request_json ELSE '{}' END AS request_json,
        p.value AS path, CAST(p.key AS INTEGER) AS ordinal,
        json_extract(a.artifacts_json, '$[' || (json_array_length(a.artifacts_json)-1) || ']') AS metadata_path
 FROM attempts a, json_each(CASE WHEN json_valid(a.artifacts_json) THEN a.artifacts_json ELSE '[]' END) p
 WHERE a.state='succeeded' AND a.remote_outcome='succeeded' AND p.type='text'
   AND CAST(p.key AS INTEGER) < json_array_length(a.artifacts_json)-1
   AND NOT EXISTS (SELECT 1 FROM image_recycle.records r
                   WHERE r.identity = a.attempt_id || ':' || CAST(p.key AS INTEGER)
                     AND r.outcome IN ('recycled', 'restored', 'deleted', 'absent'))
), records AS (
 SELECT *, attempt_id || ':' || ordinal AS identity,
   json_extract(request_json, '$.model_id') AS model,
   json_extract(request_json, '$.prompt') AS prompt,
   json_extract(request_json, '$.negative_prompt') AS negative,
   CASE WHEN json_extract(request_json, '$.operation')='inpaint'
          AND json_extract(request_json, '$.image_inputs.preserve_outside')=1
        THEN json_extract(request_json, '$.image_inputs.fit.source_width')
        ELSE json_extract(request_json, '$.width_px') END AS width,
   CASE WHEN json_extract(request_json, '$.operation')='inpaint'
          AND json_extract(request_json, '$.image_inputs.preserve_outside')=1
        THEN json_extract(request_json, '$.image_inputs.fit.source_height')
        ELSE json_extract(request_json, '$.height_px') END AS height
 FROM outputs
)
"""


def lora_order(value: str) -> str:
    """Canonical sorted identifier/weight sequence; no LoRAs sort first, then casefolded identifiers."""
    try:
        loras = json.loads(value).get("loras", [])
        return json.dumps(sorted((x["identifier"].casefold(), float(x["weight"])) for x in loras))
    except (ValueError, TypeError, KeyError, AttributeError):
        return "[]"


@contextmanager
def reader(state: Path):
    """Read an existing supported store without initialization or the dispatch ownership lock."""
    path = Path(state).resolve() / "attempts.sqlite3"
    if not path.is_file():
        yield None
        return
    if not patched_sqlite(sqlite3.sqlite_version):
        raise PersistenceError("SQLite needs an official WAL-reset fix (3.51.3+ or a supported backport).")
    connection = None
    try:
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)
        connection.row_factory = sqlite3.Row
        attach_read_only(connection, state)
        connection.execute("PRAGMA query_only=ON")
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version not in (1, 2, SCHEMA_VERSION):
            raise PersistenceError("Unsupported execution-state schema; browsing made no migration or reset.")
        connection.create_function("fold", 1, lambda text: str(text or "").casefold(), deterministic=True)
        connection.create_function("lora_order", 1, lora_order, deterministic=True)
        deadline = time.monotonic() + 5
        connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
        connection.execute("BEGIN")
        yield connection
    except sqlite3.Error as exc:
        code = getattr(exc, "sqlite_errorname", "") or type(exc).__name__
        if code == "SQLITE_INTERRUPT":
            raise PersistenceError("Saved outputs could not be queried within the read budget.") from exc
        if code.startswith(("SQLITE_BUSY", "SQLITE_LOCKED")):
            # WAL locking is per operating system: a Windows reader cannot share a
            # store that a WSL1 process is writing (or the reverse). Retry later.
            raise PersistenceError(
                f"Saved outputs are busy, possibly in use by DTFlow on another system ({code})."
            ) from exc
        raise PersistenceError(f"Saved outputs could not be read ({code}).") from exc
    finally:
        if connection is not None:
            connection.close()


def clauses(query: GalleryQuery) -> tuple[str, list, str]:
    terms, values = [], []
    for field, text in (("prompt", query.prompt), ("negative", query.negative)):
        if text:
            terms.append(f"instr(fold({field}), ?) > 0")
            values.append(text.casefold())
    if query.model:
        terms.append("model=?")
        values.append(query.model)
    if query.lora:
        terms.append(
            "EXISTS (SELECT 1 FROM json_each(request_json, '$.loras') l WHERE json_extract(l.value, '$.identifier')=?)"
        )
        values.append(query.lora)
    for boundary, comparison in ((query.since, ">="), (query.until, "<")):
        if boundary:
            terms.append(f"julianday(saved_at_utc) {comparison} julianday(?)")
            values.append(boundary)
    for field, value in (("width", query.width), ("height", query.height)):
        if value is not None:
            terms.append(f"{field}=?")
            values.append(value)
    bounds = SIZE_CLASSES[query.size_class]
    if bounds:
        longer = "max(width, height)"
        terms.append(f"{longer} >= ?")
        values.append(bounds[0])
        if bounds[1] is not None:
            terms.append(f"{longer} < ?")
            values.append(bounds[1])
    if query.ratio in ("square", "portrait", "landscape"):
        comparison = {"square": "=", "portrait": "<", "landscape": ">"}[query.ratio]
        terms.append(f"width {comparison} height")
    elif query.ratio:
        a, b = map(int, query.ratio.split(":"))
        terms.append("width * ? = height * ?")
        values.extend((b, a))
    where = " AND ".join(terms) or "1"
    order = {
        "newest": "julianday(saved_at_utc) DESC",
        "oldest": "julianday(saved_at_utc) ASC",
        "model": "fold(model) ASC",
        "size": "width * height ASC, width ASC, height ASC",
        "loras": "lora_order(request_json) ASC",
    }[query.sort] + ", attempt_id ASC, ordinal ASC"
    return where, values, order


def query_records(state: Path, query: GalleryQuery) -> tuple[tuple[dict, ...], int]:
    """Count and return one bounded page from the same read snapshot."""
    where, values, order = clauses(query)
    with reader(state) as db:
        if db is None:
            return (), 0
        total = db.execute(BASE + "SELECT count(*) FROM records WHERE " + where, values).fetchone()[0]
        rows = db.execute(
            BASE + f"SELECT * FROM records WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?",
            [*values, query.limit, query.offset],
        )
        return tuple(dict(row) for row in rows), total


def locate_record(state: Path, query: GalleryQuery, identity: str) -> int | None:
    """Find an anchor's position in filtered results without materializing those results."""
    where, values, order = clauses(query)
    with reader(state) as db:
        if db is None:
            return None
        row = db.execute(
            BASE
            + f", ranked AS (SELECT identity, row_number() OVER (ORDER BY {order})-1 AS position FROM records WHERE {where}) SELECT position FROM ranked WHERE identity=?",
            [*values, identity],
        ).fetchone()
        return row[0] if row else None


def get_record(state: Path, identity: str) -> dict | None:
    with reader(state) as db:
        if db is None:
            return None
        row = db.execute(BASE + "SELECT * FROM records WHERE identity=?", (identity,)).fetchone()
        return dict(row) if row else None


def available_export_identities(state: Path, identities: tuple[str, ...]) -> frozenset[str]:
    """Read only committed, non-recycled identities using selected attempt primary keys.

    The exporter holds the original-file lease until these bounded skip outcomes
    are counted. No original files, requests or metadata payloads are loaded.
    """
    if len(identities) > MAX_EXPORT_AVAILABILITY_BATCH:
        raise ValueError("Export availability batch exceeds its bounded limit.")
    selected = []
    for identity in identities:
        if not isinstance(identity, str):
            continue
        attempt, separator, output = identity.partition(":")
        if not separator or not output.isascii() or not output.isdecimal():
            continue
        ordinal = int(output)
        if output != str(ordinal):
            continue
        selected.append((attempt, ordinal, identity))
    if not selected:
        return frozenset()
    with reader(state) as db:
        if db is None:
            return frozenset()
        rows = db.execute(
            """WITH selected AS (
                 SELECT json_extract(value, '$[0]') AS attempt_id,
                        json_extract(value, '$[1]') AS ordinal,
                        json_extract(value, '$[2]') AS identity
                 FROM json_each(?)
               )
               SELECT s.identity FROM selected s JOIN attempts a ON a.attempt_id=s.attempt_id
               WHERE a.state='succeeded' AND a.remote_outcome='succeeded'
                 AND s.ordinal < json_array_length(CASE WHEN json_valid(a.artifacts_json)
                                                       THEN a.artifacts_json ELSE '[]' END)-1
                 AND json_type(CASE WHEN json_valid(a.artifacts_json) THEN a.artifacts_json ELSE '[]' END,
                               '$[' || s.ordinal || ']')='text'
                 AND NOT EXISTS (SELECT 1 FROM image_recycle.records r
                                 WHERE r.identity=s.identity AND r.outcome IN ('recycled', 'restored', 'deleted', 'absent'))""",
            (json.dumps(selected),),
        )
        return frozenset(row[0] for row in rows)


def facets(state: Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """At most 1000 exact model/LoRA choices; editable controls support identifiers beyond this list."""
    with reader(state) as db:
        if db is None:
            return (), ()
        models = tuple(
            row[0]
            for row in db.execute(
                BASE
                + "SELECT DISTINCT model FROM records WHERE model IS NOT NULL ORDER BY fold(model), model LIMIT 1000"
            )
        )
        loras = tuple(
            row[0]
            for row in db.execute(
                BASE
                + "SELECT DISTINCT json_extract(l.value, '$.identifier') AS id FROM records, json_each(request_json, '$.loras') l WHERE id IS NOT NULL ORDER BY fold(id), id LIMIT 1000"
            )
        )
        return models, loras


def export_identities(state: Path, query: GalleryQuery, maximum: int) -> tuple[str, ...]:
    """Snapshot filtered identities only; close the read transaction before image conversion.

    A maximum+1 sentinel lets the export service reject over-budget selections without
    silently exporting a partial page. Query offset/limit do not restrict bulk export.
    """
    where, values, order = clauses(query)
    with reader(state) as db:
        if db is None:
            return ()
        rows = db.execute(
            BASE + f"SELECT identity FROM records WHERE {where} ORDER BY {order} LIMIT ?",
            [*values, maximum + 1],
        )
        return tuple(row[0] for row in rows)


def selection_candidates(
    state: Path,
    query: GalleryQuery,
    checked: tuple[str, ...],
    *,
    add: bool = False,
    maximum: int = 10_000,
) -> tuple[tuple[str, ...], int, int, tuple[str, ...]]:
    """Return new matching identities, hidden checked count and omitted matches.

    Read only stable IDs in a single bounded SQLite snapshot. Existing checked
    IDs keep their place; additions follow the current deterministic query sort.
    This does not silently limit or replace existing selections.
    """
    where, values, order = clauses(query)
    existing = set(checked)
    capacity = max(0, maximum - len(existing)) if add else 0
    additions: list[str] = []
    matched_checked: list[str] = []
    omitted = 0
    with reader(state) as db:
        if db is None:
            return (), len(existing), 0, ()
        rows = db.execute(BASE + f"SELECT identity FROM records WHERE {where} ORDER BY {order}", values)
        for row in rows:
            identity = row[0]
            if identity in existing:
                matched_checked.append(identity)
            elif add:
                if len(additions) < capacity:
                    additions.append(identity)
                else:
                    omitted += 1
    return tuple(additions), len(existing) - len(matched_checked), omitted, tuple(matched_checked)


def selection_paths(
    state: Path,
    identities: tuple[str, ...],
    *,
    read_path: Callable[[str], str] | None = None,
    read_record: Callable[[dict], str] | None = None,
) -> tuple[tuple[str, ...], int]:
    """Read source paths for checked recorded originals without Qt or image decodes."""
    targets = set(identities)
    if not targets:
        return (), 0
    found = {}
    with reader(state) as db:
        if db is None:
            return (), len(targets)
        for row in db.execute(BASE + "SELECT identity, attempt_id, ordinal, path, metadata_path FROM records"):
            if row["identity"] in targets:
                try:
                    source = Path(
                        read_record(dict(row))
                        if read_record is not None
                        else read_path(row["path"])
                        if read_path is not None
                        else row["path"]
                    )
                    if source.is_file() and not source.is_symlink():
                        found[row["identity"]] = str(source)
                except OSError:
                    continue
    return tuple(found[identity] for identity in identities if identity in found), len(targets) - len(found)


def recycle_preflight_batch(
    state: Path, identities: tuple[str, ...], *, path_key: Callable[[str], str] | None = None
) -> tuple[dict[str, dict], dict[str, int]]:
    """Read selected records and committed path ownership in one bounded snapshot.

    Path counts include recycled references, just like execution revalidation.
    Do not load request/metadata payloads or inspect original files here.
    """
    if len(identities) > MAX_RECYCLE_PREFLIGHT_BATCH:
        raise ValueError("Recycle preflight batch exceeds its bounded limit.")
    if not identities:
        return {}, {}
    attempts = tuple(dict.fromkeys(identity.partition(":")[0] for identity in identities))
    attempt_slots = ",".join("?" for _ in attempts)
    identity_slots = ",".join("?" for _ in identities)
    with reader(state) as db:
        if db is None:
            return {}, {}
        records = {
            row["identity"]: dict(row)
            for row in db.execute(
                BASE
                + "SELECT identity,attempt_id,ordinal,path,metadata_path FROM records "
                + f"WHERE attempt_id IN ({attempt_slots}) AND identity IN ({identity_slots})",
                (*attempts, *identities),
            )
        }
        paths = tuple(dict.fromkeys(record["path"] for record in records.values()))
        if not paths:
            return records, {}
        if path_key is not None:
            # Normalize only for reference counting, never persist access aliases.
            db.create_function("original_key", 1, path_key)
            keys = tuple(dict.fromkeys(path_key(path) for path in paths))
            attempts = _candidate_attempts(keys)
            prefilter = (
                " AND EXISTS (SELECT 1 FROM json_each(?) allowed WHERE instr(lower(p.value), allowed.value) > 0)"
                if attempts is not None
                else ""
            )
            parameters = (json.dumps(keys), json.dumps(attempts)) if attempts is not None else (json.dumps(keys),)
            counts = dict(
                db.execute(
                    """SELECT original_key(p.value),count(*) FROM attempts a,
                         json_each(CASE WHEN json_valid(a.artifacts_json)
                           THEN a.artifacts_json ELSE '[]' END) p
                   WHERE a.state='succeeded' AND a.remote_outcome='succeeded'
                     AND p.type='text' AND original_key(p.value) IN (SELECT value FROM json_each(?))
                     AND CAST(p.key AS INTEGER) < json_array_length(a.artifacts_json)-1"""
                    + prefilter
                    + " GROUP BY original_key(p.value)",
                    parameters,
                )
            )
            return records, counts
        counts = {
            row[0]: int(row[1])
            for row in db.execute(
                """SELECT p.value,count(*) FROM attempts a,
                         json_each(CASE WHEN json_valid(a.artifacts_json)
                           THEN a.artifacts_json ELSE '[]' END) p
                   WHERE a.state='succeeded' AND a.remote_outcome='succeeded'
                     AND p.type='text' AND p.value IN (SELECT value FROM json_each(?))
                     AND CAST(p.key AS INTEGER) < json_array_length(a.artifacts_json)-1
                   GROUP BY p.value""",
                (json.dumps(paths),),
            )
        }
        return records, counts


def path_reference_count(state: Path, path: str, *, path_key: Callable[[str], str] | None = None) -> int:
    """Count committed final-output references regardless of recycle state."""
    with reader(state) as db:
        if db is None:
            return 0
        if path_key is not None:
            db.create_function("original_key", 1, path_key)
            key = path_key(path)
            attempts = _candidate_attempts((key,))
            prefilter = " AND instr(lower(p.value), ?) > 0" if attempts is not None else ""
            parameters = (key, attempts[0]) if attempts is not None else (key,)
            return int(
                db.execute(
                    """SELECT count(*) FROM attempts a,
                         json_each(CASE WHEN json_valid(a.artifacts_json)
                           THEN a.artifacts_json ELSE '[]' END) p
                   WHERE a.state='succeeded' AND a.remote_outcome='succeeded'
                     AND p.type='text' AND original_key(p.value)=?
                     AND CAST(p.key AS INTEGER) < json_array_length(a.artifacts_json)-1"""
                    + prefilter,
                    parameters,
                ).fetchone()[0]
            )
        row = db.execute(
            """SELECT count(*) FROM attempts a,
                     json_each(CASE WHEN json_valid(a.artifacts_json)
                       THEN a.artifacts_json ELSE '[]' END) p
               WHERE a.state='succeeded' AND a.remote_outcome='succeeded'
                 AND p.type='text' AND p.value=?
                 AND CAST(p.key AS INTEGER) < json_array_length(a.artifacts_json)-1""",
            (path,),
        ).fetchone()
        return int(row[0])
