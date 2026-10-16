"""Small reusable content-addressed JSON cache with explicit provenance."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

CACHE_ENVELOPE_VERSION = "json-cache-envelope-v1"


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def cache_key(stage: str, implementation_version: str, semantic_inputs: dict[str, Any]) -> str:
    payload = {
        "stage": stage,
        "implementation_version": implementation_version,
        "semantic_inputs": semantic_inputs,
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def content_fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class CacheLookup:
    status: str
    key: str
    value: Any | None = None
    reason: str = ""
    path: str = ""

    def event(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "key": self.key,
            "reason": self.reason,
            "path": self.path,
        }


class JsonStageCache:
    def __init__(self, root: Path, stage: str, implementation_version: str):
        self.root = root / stage
        self.stage = stage
        self.implementation_version = implementation_version

    def key(self, semantic_inputs: dict[str, Any]) -> str:
        return cache_key(self.stage, self.implementation_version, semantic_inputs)

    def path_for(self, key: str) -> Path:
        return self.root / f"{key}.json"

    def load(self, semantic_inputs: dict[str, Any]) -> CacheLookup:
        key = self.key(semantic_inputs)
        path = self.path_for(key)
        if not path.exists():
            return CacheLookup("miss", key, reason="not_found", path=str(path))
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
            if envelope.get("envelope_version") != CACHE_ENVELOPE_VERSION:
                raise ValueError("envelope_version_changed")
            if envelope.get("stage") != self.stage:
                raise ValueError("stage_changed")
            if envelope.get("implementation_version") != self.implementation_version:
                raise ValueError("implementation_version_changed")
            if envelope.get("key") != key:
                raise ValueError("key_mismatch")
            return CacheLookup("hit", key, value=envelope["value"], path=str(path))
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            quarantine = path.with_suffix(
                path.suffix
                + ".corrupt-"
                + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            )
            try:
                path.replace(quarantine)
            except OSError:
                quarantine = path
            return CacheLookup(
                "miss",
                key,
                reason=f"corrupt:{type(exc).__name__}",
                path=str(quarantine),
            )

    def save(self, semantic_inputs: dict[str, Any], value: Any) -> CacheLookup:
        key = self.key(semantic_inputs)
        path = self.path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        envelope = {
            "envelope_version": CACHE_ENVELOPE_VERSION,
            "stage": self.stage,
            "implementation_version": self.implementation_version,
            "key": key,
            "semantic_inputs": semantic_inputs,
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "value": value,
        }
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(envelope, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(path)
        return CacheLookup("generated", key, value=value, path=str(path))
