"""Text of the aggregate Activity Bar: candidate strings from richest to poorest, chosen by the painter by measured width.

Qt-free. The painter measures each candidate against the real text rectangle (``QFontMetrics.horizontalAdvance``) and
shows the first that fits, so this module never guesses pixel widths. Groups drop in a fixed order when space is short:
compact labels first, then the queue count and optional stage groups only as a last resort. The global
``settled/total`` and every shown sampling X/Y or receiving A/B are never dropped or truncated; whatever is left out
stays in ``full`` (the tooltip and accessible name).
"""

from __future__ import annotations

from dataclasses import dataclass

from dtflow.ui.progress_model import SOLO_QUEUE, ProgressSnapshot

SEP = " · "
GROUP_SEP = " | "
_LONG = {"W": "Waiting", "C": "DT+ Cloud", "E": "Enc", "V": "Save"}


@dataclass(frozen=True, slots=True)
class AggregateText:
    candidates: tuple[str, ...]  # richest first; the last is the global settled/total alone
    full: str  # everything, for the tooltip and accessible name


def _counts(snapshot: ProgressSnapshot) -> dict[str, int]:
    return dict(snapshot.stage_counts)


def _groups(snapshot: ProgressSnapshot, short: bool) -> tuple[list[str], list[str], list[str], list[str]]:
    """Core, waiting, sampling/receiving, and ending groups in lifecycle order."""
    counts = _counts(snapshot)
    core = [f"{snapshot.settled}/{snapshot.total}" + ("" if short else " items")]
    failed = dict(snapshot.outcomes).get("failed", 0)
    if failed:
        core.append(f"!{failed}" if short else f"{failed} failed")
    waiting = []
    for code in ("W", "C"):
        if counts.get(code):
            waiting.append(f"{code}{counts[code]}" if short else f"{_LONG[code]} {counts[code]}")
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
            ending.append(f"{code}{counts[code]}" if short else f"{_LONG[code]} {counts[code]}")
    xy = []
    pass_items = tuple(item for queue in snapshot.queues for item in queue.items)

    if any(counts.get(code) for code in ("S1", "W2", "S2")):
        p1, p2, pending = (counts.get(code, 0) for code in ("S1", "S2", "W2"))
        done = total = 0
        complete = True
        for item in pass_items:
            if item.stage not in ("S1", "S2", "W2"):
                continue
            first = item.pass_1_steps or (item.steps if item.stage == "S1" else None)
            if first is None:
                complete = False
            else:
                done += first[0]
                total += first[1]
            if item.stage == "S2":
                if item.steps is None:
                    done += item.observed_step or 0
                    complete = False
                else:
                    done += item.steps[0]
                    total += item.steps[1]
            elif item.second_pass_total is not None:
                total += item.second_pass_total
            else:
                complete = False
        counter = f"{done}/{total}" if complete and total else f"{done}/?"
        details = (
            f"P1 {p1}",
            f"P2 {p2}",
            f"W2 {pending}" if short else f"Waiting {pending}",
            f"{counter} steps",
        )
        passes = (("S" if short else "Sampling") + " [" + SEP.join(details) + "]",)
    else:
        passes = ()

    for code, word, unit, sums in (
        ("S", "Sampling", "steps", snapshot.steps),
        ("R", "Recv", "frames", snapshot.frames),
    ):
        if code == "R":
            xy.extend(passes)
        count = counts.get(code, 0)
        if not count:
            continue
        done, total, reporters = sums
        head = f"{code}{count}" if short else f"{word} {count}"
        if reporters:
            partial = "" if reporters >= count else f", {reporters} reporting"
            xy.append(f"{head} {done}/{total}{partial}" if short else f"{head} ({done}/{total} {unit}{partial})")
        else:
            xy.append(head)
    return core, waiting, xy, ending


def _line(snapshot, *, short, percent, times, wait, queues) -> str:
    core, waiting, xy, ending = _groups(snapshot, short)
    parts = []
    if queues and snapshot.queues and snapshot.queues[0].totals.queue_id != SOLO_QUEUE:
        count = len(snapshot.queues)
        parts.append(f"{count}q" if short else f"{count} {'queue' if count == 1 else 'queues'}")
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


def aggregate_text(snapshot: ProgressSnapshot, *, elapsed: str = "", eta: str = "") -> AggregateText:
    times = SEP.join(part for part in (elapsed, eta) if part)
    percent = f"{int(snapshot.fill * 100)}%" if snapshot.total else ""
    full = _line(snapshot, short=False, percent=percent, times=times, wait=True, queues=True)
    candidates: list[str] = []
    for wait, queues in ((True, True), (True, False), (False, False)):
        for short in (False, True):
            text = _line(
                snapshot,
                short=short,
                percent="",
                times="",
                wait=wait,
                queues=queues,
            )
            if text and text not in candidates:
                candidates.append(text)
    minimal = f"{snapshot.settled}/{snapshot.total}"
    if minimal not in candidates:
        candidates.append(minimal)
    return AggregateText(tuple(candidates), full)
