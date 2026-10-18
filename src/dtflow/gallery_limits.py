"""Qt-free validation for the gallery's test-only batch and card limits."""

import argparse

DEFAULT_BATCH = 100
DEFAULT_CARDS = DEFAULT_BATCH * 3
MIN_BATCH = 10
MAX_BATCH = 200  # GalleryQuery's per-read cap
MAX_CARDS = 3000


def resolve(batch: int | None, cards: int | None) -> tuple[int, int]:
    """Return (records per read, max mounted cards) or raise ValueError."""
    batch = DEFAULT_BATCH if batch is None else batch
    if not MIN_BATCH <= batch <= MAX_BATCH:
        raise ValueError(f"-ml must be between {MIN_BATCH} and {MAX_BATCH}.")
    cards = batch * 3 if cards is None else cards
    # Paging and eviction move whole batches, and eviction needs a second batch.
    if cards % batch or not 2 * batch <= cards <= MAX_CARDS:
        raise ValueError(f"-mc must be a multiple of -ml, at least twice -ml and at most {MAX_CARDS}.")
    return batch, cards


def add_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "-ml",
        "--load",
        type=int,
        help=f"Gallery records per read, {MIN_BATCH}-{MAX_BATCH} (default {DEFAULT_BATCH}); also the page size.",
    )
    command.add_argument(
        "-mc",
        "--cards",
        type=int,
        help="Most Grid/List cards kept mounted; a multiple of -ml, at least twice it (default 3 x -ml).",
    )
