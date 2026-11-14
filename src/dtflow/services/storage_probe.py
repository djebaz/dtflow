"""The made-up file a DT+ storage lookup asks about: bigger than the key's free space, so DT+ answers with its list."""

from __future__ import annotations

import secrets

from dtflow.core.models import AssetContent

PROBE_NAME = "dtflow-storage-probe.ckpt"
PROBE_FALLBACK = (
    30 * 1024**3
)  # free space unknown: larger than any account headroom we expect (10 GiB worked, 30 GiB for margin)


def probe_content(free: int | None = None) -> AssetContent:
    """A lookup file sized just above ``free`` bytes, or the large fallback when the free space is not known.

    The hash is random per lookup and never stored; nothing is uploaded and any upload URL DT+ returns is ignored."""
    size = (free + 1024**2) if free is not None else PROBE_FALLBACK
    return AssetContent("lora", PROBE_NAME, secrets.token_hex(32), size, provenance="storage probe")
