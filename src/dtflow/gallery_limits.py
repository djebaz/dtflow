"""Qt-free parsing for the gallery's test-only batch and card limits."""

import argparse

DEFAULT_BATCH = 100
DEFAULT_CARDS = DEFAULT_BATCH * 3
MAX_READ = 200  # GalleryQuery's per-read cap; larger batches read in chunks


def resolve(batch: int | None, cards: int | None) -> tuple[int, int]:
    """Return (records per batch, max mounted cards) or raise ValueError.

    Paging and eviction move whole batches, so cards round down to a
    multiple of the batch (at least one batch).
    """
    batch = DEFAULT_BATCH if batch is None else batch
    if batch < 1:
        raise ValueError("-ml must be at least 1.")
    cards = batch * 3 if cards is None else cards
    if cards < 1:
        raise ValueError("-mc must be at least 1.")
    return batch, max(batch, cards - cards % batch)


def add_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "-ml",
        "--load",
        type=int,
        default=argparse.SUPPRESS,  # accepted before or after the gui subcommand
        help=f"Gallery records per batch and page size (default {DEFAULT_BATCH}); read in chunks of {MAX_READ}.",
    )
    command.add_argument(
        "-mc",
        "--cards",
        type=int,
        default=argparse.SUPPRESS,
        help="Most Grid/List cards kept mounted, rounded down to whole batches (default 3 x -ml).",
    )
