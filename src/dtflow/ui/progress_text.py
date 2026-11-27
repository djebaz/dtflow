"""Text of the aggregate Activity Bar: candidate strings from richest to poorest, chosen by the painter by measured width.

Qt-free. The painter measures each candidate against the real text rectangle (``QFontMetrics.horizontalAdvance``) and
shows the first that fits, so this module never guesses pixel widths. Groups drop in a fixed order when space is short:
the percentage, elapsed/ETA text, waiting/cloud/encoding/saving/paused counts, then the queue count. The global
``settled/total`` and every shown sampling X/Y or receiving A/B are never dropped or truncated; whatever is left out
stays in ``full`` (the tooltip and accessible name).
"""

from __future__ import annotations

from dataclasses import dataclass

from dtflow.ui.progress_model import SOLO_QUEUE, ProgressSnapshot

SEP = " · "
GROUP_SEP = " | "
_LONG = {"W": "waiting", "C": "in DT+ Cloud", "E": "encoding", "V": "saving"}


@dataclass(frozen=True, slots=True)
class AggregateText:
    candidates: tuple[str, ...]  # richest first; the last is the global settled/total alone
    timed: int  # the leading candidates that may be shown beside elapsed/ETA (those still holding every count group)
    full: str  # everything, for the tooltip and accessible name


def _counts(snapshot: ProgressSnapshot) -> dict[str, int]:
    return dict(snapshot.stage_counts)


def _groups(
    snapshot: ProgressSnapshot, short: bool, hires: bool = False
) -> tuple[list[str], list[str], list[str], list[str]]:
    """Core, waiting, sampling/receiving, and ending groups in lifecycle order."""
    counts = _counts(snapshot)
    core = [f"{snapshot.settled}/{snapshot.total}" + ("" if short else " items")]
    failed = dict(snapshot.outcomes).get("failed", 0)
    if failed:
        core.append(f"!{failed}" if short else f"{failed} failed")
    waiting = []
    for code in ("W", "C"):
        if counts.get(code):
            waiting.append(f"{code}{counts[code]}" if short else f"{counts[code]} {_LONG[code]}")
    if not short:
        outcomes = dict(snapshot.outcomes)
        for name in ("cancelled", "unknown"):
            if outcomes.get(name):
                waiting.append(f"{outcomes[name]} {name}")
    paused = sum(1 for queue in snapshot.queues if queue.totals.paused)
    if paused:
        waiting.append(f"P{paused}" if short else f"{paused} paused")
    ending = []
    for code in ("E", "V"):
        if counts.get(code):
            ending.append(f"{code}{counts[code]}" if short else f"{counts[code]} {_LONG[code]}")
    xy = []
    pass_items = tuple(item for queue in snapshot.queues for item in queue.items)

    if hires or any(counts.get(code) for code in ("S1", "W2", "S2")):
        # Keep both pass groups visible, including zero counts. A missing denominator is not zero.
        def pass_group(code: str, label: str, waiting_count: int | None = None) -> str:
            count = counts.get(code, 0)
            items = [item for item in pass_items if item.stage == code]
            pairs = [item.steps for item in items if item.steps is not None]
            observed = [item.observed_step for item in items if item.observed_step is not None]
            partial = ""
            if count and len(pairs) == count:
                counter = f"{sum(p[0] for p in pairs)}/{sum(p[1] for p in pairs)}"
            elif observed:
                counter = str(sum(observed))
                if len(observed) < count:
                    partial = f", {len(observed)} reporting"
            elif pairs:
                counter = f"{sum(p[0] for p in pairs)}/{sum(p[1] for p in pairs)}"
                partial = f", {len(pairs)} reporting"
            else:
                counter = "—" if count else "0"
            parts = []
            if waiting_count is not None:
                parts.append(f"W{waiting_count}" if short else f"{waiting_count} waiting")
            parts.append(f"S{count}" if short else f"{count} sampling")
            parts.append(f"{counter}{partial}" if short else f"{counter} steps{partial}")
            return f"{label} [{SEP.join(parts)}]"

        passes = (pass_group("S1", "P1"), pass_group("S2", "P2", counts.get("W2", 0)))
    else:
        passes = ()

    for code, word, unit, sums in (
        ("S", "sampling", "steps", snapshot.steps),
        ("R", "receiving", "frames", snapshot.frames),
    ):
        if code == "R":
            xy.extend(passes)
        count = counts.get(code, 0)
        if not count:
            continue
        done, total, reporters = sums
        head = f"{code}{count}" if short else f"{count} {word}"
        if reporters:
            partial = "" if reporters >= count else f", {reporters} reporting"
            xy.append(f"{head} {done}/{total}" if short else f"{head} ({done}/{total} {unit}{partial})")
        else:
            xy.append(head)
    return core, waiting, xy, ending


def _line(snapshot, *, short, percent, times, wait, queues, hires=False) -> str:
    core, waiting, xy, ending = _groups(snapshot, short, hires)
    parts = []
    if queues and not short and snapshot.queues and snapshot.queues[0].totals.queue_id != SOLO_QUEUE:
        parts.append(f"{len(snapshot.queues)} {'queue' if len(snapshot.queues) == 1 else 'queues'}")
    parts += core
    if percent and not short:
        parts.append(percent)
    # Keep the queue summary together, then follow the observed generation lifecycle.
    groups = [SEP.join(parts)]
    if wait:
        groups += waiting
    groups += xy
    if wait:
        groups += ending
    if times:
        groups.append(times)
    return GROUP_SEP.join(groups)


def aggregate_text(
    snapshot: ProgressSnapshot, *, elapsed: str = "", eta: str = "", hires: bool = False
) -> AggregateText:
    times = SEP.join(part for part in (elapsed, eta) if part)
    percent = f"~{int(snapshot.fill * 100)}%" if snapshot.total else ""
    full = _line(snapshot, short=False, percent=percent, times=times, wait=True, queues=True, hires=hires)
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
                hires=hires,
            )
            if text and text not in candidates:
                candidates.append(text)
        if level == 1:
            timed = len(candidates)
    minimal = f"{snapshot.settled}/{snapshot.total}"
    if minimal not in candidates:
        candidates.append(minimal)
    return AggregateText(tuple(candidates), timed, full)
