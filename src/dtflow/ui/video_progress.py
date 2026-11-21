"""Bottom-bar progress of one video run, in the spirit of the POC's console line.

Shows the observed phase (waiting for DT+, sampling step n of N, a second pass only when DT+ reports one, receiving
frames k/F with the MB received, finalizing) and the seconds since the request was sent, refreshed every second so a
silent wait never looks hung. The bar fill is one monotonic estimate for the whole run: each phase owns a window of it,
filled by the observed step or frame count when DT+ reports one and by elapsed time (approaching the end of the window
but never reaching it before the next phase starts) when it does not. It is an extrapolation, not a remaining time.
"""

from __future__ import annotations

import math
import time

from dtflow.core.models import BackendEvent
from dtflow.ui.activity import Activity, RunState
from dtflow.ui.progress_stages import event_stage


# Window of the whole-run fraction owned by each phase, and the time constant (seconds) used while the phase reports
# no counter. Values are rough shares of a typical clip, not measurements.
WINDOWS = {
    "preparing": (0.00, 0.03, 6.0),
    "waiting": (0.03, 0.50, 60.0),
    "sampling": (0.50, 0.80, 120.0),
    "second_pass": (0.70, 0.80, 60.0),
    "receiving": (0.80, 0.93, 40.0),
    "encoding": (0.93, 0.98, 10.0),
    "saving": (0.98, 0.995, 3.0),
}


def clock(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


class VideoProgress:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.started = time.monotonic()
        self.sent_at: float | None = None
        self.phase = "preparing"
        self.text = "Preparing"
        self.done: int | None = None
        self.total: int | None = None
        self.bytes = 0
        self.steps: tuple[int, int] | None = None
        self.phase_started = self.started
        self._peak = 0.0

    def fraction(self, now: float | None = None) -> float:
        """Monotonic 0..0.99 estimate of the whole run: counters when reported, else elapsed time inside the window."""
        now = time.monotonic() if now is None else now
        low, high, tau = WINDOWS.get(self.phase, WINDOWS["preparing"])
        if self.done is not None and self.total:
            value = low + (high - low) * min(1.0, max(0.0, self.done / self.total))
        else:
            value = low + (high - low) * (1.0 - math.exp(-max(0.0, now - self.phase_started) / tau))
        self._peak = max(self._peak, min(0.99, value))
        return self._peak

    @property
    def count_text(self) -> str:
        """Short observed counter of the current phase, empty when it reports none."""
        if self.done is None or not self.total:
            return ""
        unit = {"sampling": "steps", "second_pass": "steps", "receiving": "frames"}.get(self.phase)
        return f"{self.done}/{self.total} {unit}" if unit else ""

    def observe(self, event: BackendEvent) -> bool:
        """Fold one backend event in; True when it is one a video run presents."""
        before = self.phase
        presented = self._observe(event)
        if self.phase != before:
            self.phase_started = time.monotonic()
        return presented

    def _observe(self, event: BackendEvent) -> bool:
        if event.kind == "preparing":
            if event.stage in ("waiting_backend_response", "backend_response_received"):
                self._wait()
            else:
                self.phase, self.text, self.done, self.total = (
                    "waiting" if event.stage == "waiting_video_resources" else "preparing",
                    event_stage(event) or "Preparing",
                    None,
                    None,
                )
        elif event.kind == "submission_possible":
            self.sent_at = time.monotonic()
            self._wait()
        elif event.kind == "progress" and event.stage == "sampling":
            self.phase, self.done, self.total = "sampling", event.completed, event.total
            self.steps = (event.completed or 0, event.total or 0)
            self.text = f"Sampling · step {event.completed} of {event.total}"
        elif event.kind == "progress" and event.stage == "receiving_frames":
            self.phase, self.done, self.total = "receiving", event.completed, event.total
            self.bytes = event.received_bytes or self.bytes
            self.text = f"Receiving frames · {event.completed}/{event.total} · {self.bytes / 1_048_576:.1f} MB received"
        elif event.kind == "progress" and event.stage == "encoding_video":
            self.phase, self.text, self.done, self.total = (
                "encoding",
                "Finalizing video · adding audio, verifying MP4",
                None,
                None,
            )
        elif event.kind == "progress" and event.stage and event.stage.startswith("second_pass"):
            self.phase, self.text = "second_pass", "Second-pass sampling"
            self.done, self.total = event.completed, event.total
        elif event.kind == "progress" and event.stage:
            self.text = "DT+: " + event.stage.replace("_", " ")
        elif event.kind == "terminal":
            self.phase, self.text, self.done, self.total = "saving", "Saving the video and its poster", None, None
        else:
            return False
        return True

    def _wait(self) -> None:
        self.phase, self.done, self.total = "waiting", None, None
        self.text = "Waiting for DT+"

    def present(self, activity: Activity) -> None:
        origin = self.sent_at if self.sent_at is not None else self.started
        elapsed = clock(time.monotonic() - origin)
        wide = self.text
        if self.phase in ("receiving", "encoding", "saving") and self.steps:
            wide = f"{self.text} · sampled {self.steps[1]} steps"
        activity.present_run(
            RunState(
                owner="generation",
                phase="running",
                completed=round(self.fraction() * 1000),
                total=1000,
                count=self.count_text,
                detail=self.text,
                elapsed=elapsed,
                stage=self.text,
                wide=wide,
            )
        )
