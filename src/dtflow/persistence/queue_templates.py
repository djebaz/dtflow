"""Independent, transactional durable recipe library; unrelated to execution and disposable cache."""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from dtflow.core.models import ValidationError

FILENAME = "queue_templates.sqlite3"
SCHEMA_VERSION = 1
MAX_TEMPLATES = 128
MAX_RECIPE_BYTES = 256 * 1024
MAX_LIBRARY_BYTES = 4 * 1024 * 1024


def _name(value):
    if not isinstance(value, str) or not 0 < len(value.strip()) <= 80 or any(ord(c) < 32 for c in value):
        raise ValidationError("Template names require 1–80 characters without control characters.")
    return value.strip()


@contextmanager
def _database(state, *, write=False):
    path = Path(state) / FILENAME
    db = None
    try:
        if path.is_symlink():
            raise ValidationError("Redirected template libraries are unsupported.")
        if not path.exists() and not write:
            yield None
            return
        if write:
            path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(path, timeout=1)
        db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_RECIPE_BYTES + 4096)
        db.row_factory = sqlite3.Row
        db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        version = db.execute("PRAGMA user_version").fetchone()[0]
        tables = db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        if version == 0 and not tables and write:
            db.execute(
                "CREATE TABLE templates (identity TEXT PRIMARY KEY, name TEXT NOT NULL, name_key TEXT UNIQUE NOT NULL, "
                "revision INTEGER NOT NULL, recipe TEXT NOT NULL)"
            )
            db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        elif version != SCHEMA_VERSION:
            raise ValidationError("Unsupported template library schema; library left unchanged.")
        yield db
        db.commit()
    except sqlite3.Error as exc:
        raise ValidationError("Template library is busy, corrupt or unreadable; library left unchanged.") from exc
    finally:
        if db is not None:
            db.close()  # rolls back unfinished transactions, including failed validation


def _rows(db, validate=None):
    if db is None:
        return ()
    rows = db.execute(
        "SELECT identity,name,revision,recipe FROM templates ORDER BY name_key LIMIT ?", (MAX_TEMPLATES + 1,)
    )
    result, total = [], 0
    for row in rows:
        if not isinstance(row["recipe"], str):
            raise ValidationError("Template recipe is unreadable; library left unchanged.")
        raw = row["recipe"].encode("utf-8")
        total += len(raw)
        if len(result) >= MAX_TEMPLATES or len(raw) > MAX_RECIPE_BYTES or total > MAX_LIBRARY_BYTES:
            raise ValidationError("Template library exceeds its safety limits; library left unchanged.")
        if type(row["revision"]) is not int or row["revision"] < 1 or not isinstance(row["identity"], str):
            raise ValidationError("Template library entry is invalid; library left unchanged.")
        try:
            recipe = json.loads(raw)
        except ValueError as exc:
            raise ValidationError("Template recipe is unreadable; library left unchanged.") from exc
        if validate is not None:
            validate(recipe)
        result.append(
            {"identity": row["identity"], "name": _name(row["name"]), "revision": row["revision"], "recipe": recipe}
        )
    return tuple(result)


def list_templates(state):
    with _database(state) as db:
        return _rows(db)


def _match(rows, identity, revision):
    found = next((row for row in rows if row["identity"] == identity), None)
    if found is None or found["revision"] != revision:
        raise ValidationError("Template changed or was removed; reload the library before editing.")
    return found


def save_template(state, name, recipe, *, identity=None, revision=None, validate=None):
    name = _name(name)
    try:
        raw = json.dumps(recipe, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except (ValueError, TypeError) as exc:
        raise ValidationError("Template recipe cannot be saved.") from exc
    if len(raw.encode("utf-8")) > MAX_RECIPE_BYTES:
        raise ValidationError("Template recipe exceeds 256 KiB.")
    with _database(state, write=True) as db:
        rows = _rows(db, validate)
        if any(row["name"].casefold() == name.casefold() and row["identity"] != identity for row in rows):
            raise ValidationError("A template with that name exists; choose another name or explicitly update it.")
        if identity is None:
            if len(rows) >= MAX_TEMPLATES:
                raise ValidationError("The template library is full (128 recipes).")
            identity, revision = str(uuid4()), 0
        else:
            _match(rows, identity, revision)
        total = sum(
            len(json.dumps(row["recipe"], ensure_ascii=False).encode("utf-8"))
            for row in rows
            if row["identity"] != identity
        )
        if total + len(raw.encode("utf-8")) > MAX_LIBRARY_BYTES:
            raise ValidationError("The template library exceeds 4 MiB.")
        db.execute(
            "INSERT INTO templates VALUES (?,?,?,?,?) ON CONFLICT(identity) DO UPDATE SET "
            "name=excluded.name,name_key=excluded.name_key,revision=excluded.revision,recipe=excluded.recipe",
            (identity, name, name.casefold(), revision + 1, raw),
        )
        return {"identity": identity, "name": name, "revision": revision + 1, "recipe": json.loads(raw)}


def rename_template(state, identity, revision, name, *, validate=None):
    name = _name(name)
    with _database(state, write=True) as db:
        rows = _rows(db, validate)
        found = _match(rows, identity, revision)
        if any(row["name"].casefold() == name.casefold() and row["identity"] != identity for row in rows):
            raise ValidationError("A template with that name already exists.")
        db.execute(
            "UPDATE templates SET name=?,name_key=?,revision=revision+1 WHERE identity=?",
            (name, name.casefold(), identity),
        )
        return {**found, "name": name, "revision": revision + 1}


def delete_template(state, identity, revision, *, validate=None):
    with _database(state, write=True) as db:
        _match(_rows(db, validate), identity, revision)
        db.execute("DELETE FROM templates WHERE identity=?", (identity,))
