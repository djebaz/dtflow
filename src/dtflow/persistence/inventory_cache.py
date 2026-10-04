"""Disposable resolved LoRA availability cache."""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path

from dtflow.core.models import LoRAInventorySnapshot

CACHE_VERSION = 1


def save_lora_inventory(root: Path, snapshot: LoRAInventorySnapshot) -> Path:
    """Atomically persist the derived cross-backend LoRA inventory."""
    target = root / "cache" / "inventory" / "lora_inventory.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".json.tmp")
    payload = {"cache_version": CACHE_VERSION, **asdict(snapshot)}
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, target)
    return target
