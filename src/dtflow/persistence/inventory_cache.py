"""Disposable resolved LoRA availability cache."""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path
from tempfile import NamedTemporaryFile

from dtflow.core.models import LoRAInventorySnapshot

CACHE_VERSION = 2


def save_lora_inventory(root: Path, snapshot: LoRAInventorySnapshot) -> Path:
    """Atomically persist the derived cross-backend LoRA inventory."""
    target = root / "cache" / "inventory" / "lora_inventory.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {"cache_version": CACHE_VERSION, **asdict(snapshot)}
    temporary = None
    try:
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=target.parent, prefix=".lora-", suffix=".tmp", delete=False
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
