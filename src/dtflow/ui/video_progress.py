"""Bottom-bar progress of one video run, in the spirit of the POC's console line.

Shows the observed phase (waiting for DT+, sampling step n of N, a second pass only when DT+ reports one, receiving
frames k/F with the MB received, finalizing) and the seconds since the request was sent, refreshed every second so a
silent wait never looks hung. Only observed events move it; nothing is estimated. The bar fill is unchanged: determinate
while sampling and receiving frames, indeterminate otherwise.
"""

from __future__ import annotations

import time

from dtflow.core.models import BackendEvent
from dtflow.ui.activity import Activity, RunState
from dtflow.ui.progress_stages import event_stage


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

    def observe(self, event: BackendEvent) -> bool:
        """Fold one backend event in; True when it is one a video run presents."""
        if event.kind == "preparing":
            if event.stage in ("waiting_backend_response", "backend_response_received"):
                self._wait()
            else:
                self.phase, self.text, self.done, self.total = (
                    "preparing",
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
                completed=self.done,
                total=self.total,
                detail=self.text,
                elapsed=elapsed,
                stage=self.text,
                wide=wide,
            )
        )
