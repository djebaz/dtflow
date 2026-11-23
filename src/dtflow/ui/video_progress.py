"""Bottom-bar presentation of one video run, in the spirit of the POC's console line.

Shows the observed phase (DT+ Cloud processing, sampling step n of N, a second pass only when DT+ reports one, receiving
frames k/F with the MB received, finalizing) and the seconds since the request was sent, refreshed every second so a
silent wait never looks hung. The observed evidence and the whole-run estimate live in ``progress_model``; this module
only words them. The fill is an extrapolation, not a remaining time.
"""

from __future__ import annotations

import time

from dtflow.ui.activity import Activity, RunState
from dtflow.ui.progress_model import ItemProgress


def clock(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def present_video(item: ItemProgress, activity: Activity) -> None:
    fraction = round(item.estimate(), 2)  # whole percents: equal run states are not presented again
    elapsed = clock(time.monotonic() - (item.sent_at if item.sent_at is not None else item.started))
    wide = item.text
    if item.phase in ("receiving", "encoder_wait", "encoding", "verifying", "saving") and item.steps:
        wide = f"{item.text} · sampled {item.steps[1]} steps"
    activity.present_run(
        RunState(
            owner="generation",
            phase="running",
            completed=item.done,
            total=item.total,
            fraction=fraction,
            count=item.count_text,
            detail=item.text,
            elapsed=elapsed,
            stage=item.text,
            wide=wide,
        )
    )
