"""Text of the aggregate Activity Bar: candidate strings from richest to poorest, chosen by the painter by measured width.

Qt-free. The painter measures each candidate against the real text rectangle (``QFontMetrics.horizontalAdvance``) and
shows the first that fits, so this module never guesses pixel widths. Groups drop in a fixed order when space is short:
the percentage, elapsed/ETA text, waiting/cloud/encoding/saving/paused counts, then the queue count. The global
``settled/total`` and every shown sampling X/Y or receiving A/B are never dropped or truncated; whatever is left out
stays in ``full`` (the tooltip and accessible name).
"""

from __future__ import annotations

from dataclasses import dataclass

from dtflow.ui.progress_model import ProgressSnapshot

SEP = " · "
_LONG = {"W": "waiting", "C": "in DT+ Cloud", "E": "encoding", "V": "saving"}


@dataclass(frozen=True, slots=True)
class AggregateText:
    candidates: tuple[str, ...]  # richest first; the last is the global settled/total alone
    timed: int  # the leading candidates that may be shown beside elapsed/ETA (those still holding every count group)
    full: str  # everything, for the tooltip and accessible name


def _counts(snapshot: ProgressSnapshot) -> dict[str, int]:
    return dict(snapshot.stage_counts)


def _groups(snapshot: ProgressSnapshot, short: bool) -> tuple[list[str], list[str], list[str]]:
    """(core groups that never drop, optional waiting groups, X/Y groups) in display order."""
    counts = _counts(snapshot)
    core = [f"{snapshot.settled}/{snapshot.total}" + ("" if short else " items")]
    failed = dict(snapshot.outcomes).get("failed", 0)
    if failed:
        core.append(f"!{failed}" if short else f"{failed} failed")
    waiting = []
    for code in ("W", "C"):
        if counts.get(code):
            waiting.append(f"{code}{counts[code]}" if short else f"{counts[code]} {_LONG[code]}")
    paused = sum(1 for queue in snapshot.queues if queue.totals.paused)
    if paused:
        waiting.append(f"P{paused}" if short else f"{paused} paused")
    ending = []
    for code in ("E", "V"):
        if counts.get(code):
            ending.append(f"{code}{counts[code]}" if short else f"{counts[code]} {_LONG[code]}")
    xy = []
    for code, word, unit, sums in (
        ("S", "sampling", "steps", snapshot.steps),
        ("R", "receiving", "frames", snapshot.frames),
    ):
        count = counts.get(code, 0)
        if not count:
            continue
        done, total, reporters = sums
        head = f"{code}{count}" if short else f"{count} {word}"
        if reporters:
            xy.append(f"{head} {done}/{total}" if short else f"{head} ({done}/{total} {unit})")
        else:
            xy.append(head)
    return core, waiting + ending, xy


def _line(snapshot, *, short, percent, times, wait, queues) -> str:
    core, optional, xy = _groups(snapshot, short)
    parts = []
    if queues and not short and snapshot.queues:
        parts.append(f"{len(snapshot.queues)} {'queue' if len(snapshot.queues) == 1 else 'queues'}")
    parts += core
    if percent and not short:
        parts.append(percent)
    if wait:
        parts += optional
    parts += xy
    if times:
        parts.append(times)
    return SEP.join(parts)


def aggregate_text(snapshot: ProgressSnapshot, *, elapsed: str = "", eta: str = "") -> AggregateText:
    times = SEP.join(part for part in (elapsed, eta) if part)
    percent = f"~{int(snapshot.fill * 100)}%" if snapshot.total else ""
    full = _line(snapshot, short=False, percent=percent, times=times, wait=True, queues=True)
    candidates: list[str] = []
    timed = 0
    for level in range(5):
        for short in (False, True):
            text = _line(
                snapshot,
                short=short,
                percent=percent if level == 0 else "",
                times=times if level <= 1 else "",
                wait=level <= 2,
                queues=level <= 3,
            )
            if text and text not in candidates:
                candidates.append(text)
        if level == 1:
            timed = len(candidates)
    minimal = f"{snapshot.settled}/{snapshot.total}"
    if minimal not in candidates:
        candidates.append(minimal)
    return AggregateText(tuple(candidates), timed, full)
