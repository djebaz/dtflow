"""Last desktop session (provider, endpoint and Generate form) in ``<state>/last_session.json``.

Durable user convenience, not cache: ``--no-cache`` and cache cleanup leave it alone. It is credential-free, versioned
independently of the application and the SQLite store, and written atomically. A missing, unreadable, malformed or
newer-schema file is treated as "nothing remembered" and is never rewritten by a read.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

SCHEMA_VERSION = 1
FILENAME = "last_session.json"

from dtflow.diagnostics import event as diagnostic_event

log = logging.getLogger(__name__)


def load_last_session(state: Path) -> dict | None:
    """The remembered document, or ``None`` when nothing usable is stored."""
    path = Path(state) / FILENAME
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        diagnostic_event(
            log,
            "app.session.ignored",
            level=logging.WARNING,
            data={
                "reason": "unreadable",
            },
        )
        return None
    if not isinstance(document, dict) or document.get("schema_version") != SCHEMA_VERSION:
        diagnostic_event(
            log,
            "app.session.ignored",
            level=logging.WARNING,
            data={
                "reason": "schema",
            },
        )
        return None
    return document


def save_last_session(state: Path, document: dict) -> Path:
    target = Path(state) / FILENAME
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=target.parent, prefix=".session-", suffix=".tmp", delete=False
        ) as handle:
            temporary = Path(handle.name)
            json.dump({"schema_version": SCHEMA_VERSION, **document}, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return target
