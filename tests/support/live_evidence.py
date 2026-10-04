"""Credential-free live-run inventory evidence, separate from application cache."""

import hashlib
import json
from dataclasses import asdict
from pathlib import Path

# Inventory JSON format, independent of application and SQLite schema versions.
CACHE_VERSION = 1


def save_inventory(run: Path, kind: str, endpoint: str, snapshot) -> Path:
    endpoint_id = hashlib.sha256(endpoint.encode("utf-8")).hexdigest()[:16]
    target = run / "inventory" / f"{kind}_{endpoint_id}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps({"cache_version": CACHE_VERSION, "endpoint": endpoint, **asdict(snapshot)}, indent=2) + "\n",
        encoding="utf-8",
    )
    return target


def save_lora_inventory(run: Path, snapshot, *, lookup_identifier: str) -> Path:
    """Retain resolved LoRA availability evidence beside raw local/cloud inventories."""
    target = run / "inventory" / "lora_inventory.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {
                "cache_version": CACHE_VERSION,
                "dt_cloud_byom_lookup_identifier": lookup_identifier,
                **asdict(snapshot),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return target
