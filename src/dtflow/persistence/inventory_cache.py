"""Disposable inventory caches under ``<state>/cache/inventory/``.

Raw local and cloud snapshots are display evidence only (a startup prefill of the model and LoRA lists); generation
always refreshes live. Every miss, whether missing, unreadable, another cache version, another endpoint or another
credential, is reported as ``None`` and nothing is migrated, repaired or deleted. The cloud entry records the credential
source and a salted hash of the selected key, never the key, so evidence is not reused across changed credentials.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from dataclasses import asdict
from pathlib import Path
from tempfile import NamedTemporaryFile

from dtflow.core.models import InventoryEntry, InventorySnapshot, LoRAInventorySnapshot
from dtflow.persistence.cache_coordination import CacheBusyError, cache_io, category_path, check_path

CACHE_VERSION = 2
KINDS = ("local", "cloud")


def _target(root: Path, name: str) -> Path:
    target = category_path(root, "inventory") / name
    check_path(target)
    return target


def _write_json(target: Path, payload: dict, prefix: str) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=target.parent, prefix=prefix, suffix=".tmp", delete=False
        ) as handle:
            temporary = Path(handle.name)
            json.dump(payload, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return target


@cache_io("inventory")
def save_lora_inventory(root: Path, snapshot: LoRAInventorySnapshot) -> Path:
    """Atomically persist the derived cross-backend LoRA inventory."""
    return _write_json(
        _target(root, "lora_inventory.json"), {"cache_version": CACHE_VERSION, **asdict(snapshot)}, ".lora-"
    )


def _fingerprint(salt: str, key: str) -> str:
    return hashlib.sha256(f"{salt}\0{key}".encode()).hexdigest()


@cache_io("inventory")
def save_inventory(
    root: Path, kind: str, snapshot: InventorySnapshot, *, credential: tuple[str, str] | None = None
) -> Path:
    """Persist one raw snapshot; ``credential`` is ``(source, key)`` and only its salted hash is written."""
    if kind not in KINDS:
        raise ValueError("Inventory kind must be local or cloud.")
    payload: dict = {"cache_version": CACHE_VERSION, **asdict(snapshot)}
    if credential is not None:
        source, key = credential
        salt = secrets.token_hex(16)
        payload["credential"] = {"source": source, "salt": salt, "fingerprint": _fingerprint(salt, key)}
    return _write_json(_target(root, f"{kind}_inventory.json"), payload, f".{kind}-")


BUSY = "busy"  # another cache operation held the shared lock for longer than one read waits


@cache_io("inventory")
def _read_inventory(
    root: Path, kind: str, *, endpoint: str | None = None, credential: tuple[str, str] | None = None
) -> tuple[InventorySnapshot | None, str | None]:
    """The cached snapshot and ``None``, or ``None`` and why there is none (a short development-diagnostic reason).

    ``endpoint`` (when given) must equal the recorded one; ``credential`` is ``(source, key)`` for the cloud entry.
    """
    if kind not in KINDS:
        raise ValueError("Inventory kind must be local or cloud.")
    try:
        document = json.loads(_target(root, f"{kind}_inventory.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, "file_missing"
    except (OSError, ValueError):
        return None, "unreadable"
    try:
        if type(document.get("cache_version")) is not int or document["cache_version"] != CACHE_VERSION:
            return None, "other_cache_version"
        if endpoint is not None and document.get("endpoint") != endpoint:
            return None, "other_endpoint"
        if credential is not None:
            recorded = document.get("credential")
            source, key = credential
            if not isinstance(recorded, dict) or recorded.get("source") != source:
                return None, "other_credential_source"
            if recorded.get("fingerprint") != _fingerprint(str(recorded.get("salt")), key):
                return None, "other_key"
        entries = tuple(InventoryEntry(**item) for item in document["entries"])
        snapshot = InventorySnapshot(
            document["backend_id"],
            document["server_identity"],
            document["observed_at_utc"],
            bool(document["refreshed"]),
            entries,
            tuple((str(name), str(value)) for name, value in document["diagnostics"]),
            endpoint=document.get("endpoint"),
        )
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        return None, f"entries_unreadable: {type(exc).__name__}: {str(exc)[:160]}"
    return snapshot, None


def load_inventory_with_reason(
    root: Path, kind: str, *, endpoint: str | None = None, credential: tuple[str, str] | None = None
) -> tuple[InventorySnapshot | None, str | None]:
    """One read: the snapshot, or ``None`` with the reason (``BUSY`` when the cache lock was held, so a caller may
    wait and read again)."""
    try:
        return _read_inventory(root, kind, endpoint=endpoint, credential=credential)
    except CacheBusyError:
        return None, BUSY
    except OSError as exc:
        return None, f"io_error: {type(exc).__name__}"


def load_inventory(
    root: Path, kind: str, *, endpoint: str | None = None, credential: tuple[str, str] | None = None
) -> InventorySnapshot | None:
    return load_inventory_with_reason(root, kind, endpoint=endpoint, credential=credential)[0]
