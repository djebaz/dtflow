"""Create a disposable, deterministic saved-output gallery for native Windows profiling.

This is synthetic *non-inference* data. It writes only to a new named folder
inside the repository's ignored tmp/ directory and refuses existing folders.
It does not open the user's .dtflow state or contact a provider.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import struct
import zlib
from datetime import UTC, datetime
from pathlib import Path

from dtflow.core.models import RemoteOutcome, ResolvedGenerationRequest, SubmissionStage
from dtflow.persistence.store import SQLiteAttempts

ROOT = Path(__file__).resolve().parents[2]
TMP = ROOT / "tmp"


def _png(seed: int, side: int = 128) -> bytes:
    """Encode seeded noise as an RGB PNG using only the standard library."""
    rng = random.Random(seed)
    pixels = rng.randbytes(side * side * 3)
    rows = b"".join(b"\x00" + pixels[n : n + side * 3] for n in range(0, len(pixels), side * 3))

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">2I5B", side, side, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows, level=3))
        + chunk(b"IEND", b"")
    )


async def seed_gallery(state: Path, count: int, seed: int) -> None:
    store = SQLiteAttempts(state)
    try:
        await store.open()
        for index in range(count):
            # Each synthetic attempt has a real committed artifact reference,
            # but this fixture is never a real generation or provider result.
            attempt_id = f"{index:032x}"
            request = ResolvedGenerationRequest(
                f"synthetic-{index:06d}", f"Offline gallery fixture {index:06d}", "synthetic-model", (seed + index) % 4294967296
            )
            folder = state / "outputs" / attempt_id
            folder.mkdir(parents=True)
            image = folder / "final-1.png"
            metadata = folder / "metadata.json"
            image.write_bytes(_png(seed + index))
            metadata.write_text(
                json.dumps({"fixture": True, "not_generated": True, "seed": seed + index}),
                encoding="utf-8",
            )
            await store.create(attempt_id, "synthetic-offline", request)
            await store.transition(
                attempt_id,
                ("prepared",),
                "succeeded",
                SubmissionStage.TERMINAL_OBSERVED,
                RemoteOutcome.SUCCEEDED,
                artifacts=(str(image), str(metadata)),
            )
    finally:
        await store.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a disposable seeded DTFlow gallery under tmp/; no provider calls.")
    parser.add_argument("--count", type=int, default=2000, help="Number of synthetic records (default: 2000).")
    parser.add_argument("--seed", type=int, default=42, help="Deterministic image seed (default: 42).")
    parser.add_argument("--name", default="gallery-perf-2000", help="New directory name under repository tmp/.")
    args = parser.parse_args()
    if not 1 <= args.count <= 5000:
        parser.error("--count must be between 1 and 5000")
    if not args.name.startswith("gallery-perf-") or not all(ch.isalnum() or ch == "-" for ch in args.name):
        parser.error("--name must be a safe name beginning gallery-perf-")
    TMP.mkdir(exist_ok=True)
    state = (TMP / args.name).resolve()
    if state.parent != TMP.resolve() or state.exists():
        parser.error("destination must be a new direct subdirectory of repository tmp/")
    state.mkdir()
    (state / "SYNTHETIC_FIXTURE.json").write_text(
        json.dumps(
            {
                "synthetic": True,
                "not_generated": True,
                "count": args.count,
                "seed": args.seed,
                "created_at_utc": datetime.now(UTC).isoformat(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    asyncio.run(seed_gallery(state, args.count, args.seed))
    print(f"Synthetic gallery created: {args.count} records at {state}")


if __name__ == "__main__":
    main()
