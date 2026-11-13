"""Explicit derived reports for the offline renderer, not application logging."""

from __future__ import annotations

import json
import sys
import time
from collections import Counter
from contextlib import contextmanager
from pathlib import Path

EXPECTED_QT = frozenset(
    (
        "This plugin does not support raise()",
        "This plugin does not support propagateSizeHints()",
        "This plugin does not support grabbing the keyboard",
    )
)
EXPECTED_FIXTURE = "gallery refresh failed reason=Offline first-query failure (SQLITE_IOERR_TRUNCATE)."


class _Console:
    def __init__(self, stream, reporter):
        self.stream, self.reporter, self.pending = stream, reporter, ""

    def write(self, text):
        self.reporter.log.write(text)
        self.pending += text
        while "\n" in self.pending:
            line, self.pending = self.pending.split("\n", 1)
            quiet = line == EXPECTED_FIXTURE
            if quiet:
                self.reporter.notices[line] += 1
            if self.reporter.verbose or not quiet:
                self.stream.write(line + "\n")
        self.flush()
        return len(text)

    def flush(self):
        self.reporter.log.flush()
        self.stream.flush()

    def finish(self):
        if self.pending:
            self.stream.write(self.pending)
            self.pending = ""
        self.flush()

    def __getattr__(self, name):
        return getattr(self.stream, name)


class Reporter:
    def __init__(self, out, selection, *, verbose=False, report_key="all"):
        self.out, self.selection, self.verbose = Path(out), selection, verbose
        self.out.mkdir(parents=True, exist_ok=True)
        self.started = self.previous = time.monotonic()
        self.manifest_at = 0.0
        self.records, self.notices = {}, Counter()
        self.unregistered = []
        # Existing Actions runs categories concurrently in the same output root.
        self.log_path = self.out / f"preview-details-{report_key}.log"
        self.manifest_path = self.out / f"preview-manifest-{report_key}.json"
        self.log = self.log_path.open("w", encoding="utf-8", newline="\n")

    @contextmanager
    def console(self):
        stdout, stderr = sys.stdout, sys.stderr
        wrapped_out, wrapped_err = _Console(stdout, self), _Console(stderr, self)
        sys.stdout, sys.stderr = wrapped_out, wrapped_err
        try:
            yield
        finally:
            wrapped_out.finish()
            wrapped_err.finish()
            sys.stdout, sys.stderr = stdout, stderr

    def diagnostic(self, message):
        self.log.write(message + "\n")
        self.log.flush()
        if self.verbose:
            print(message)

    def qt_message(self, kind, context, message):
        # Only exact known warnings from the offscreen platform are quieted.
        self.log.write(f"Qt {kind}: {message}\n")
        self.log.flush()
        if self.offscreen and message in EXPECTED_QT and kind == self.warning_kind:
            self.notices[message] += 1
            if self.verbose:
                print(message, file=sys.stderr)
        elif self.previous_qt is not None:
            self.previous_qt(kind, context, message)
        else:
            print(f"Qt {kind}: {message}", file=sys.stderr)

    def begin(self):
        self.previous = time.monotonic()

    def saved(self, target, *, blank):
        name = Path(target).stem
        state = self.selection.by_id.get(name)
        if name in self.records:
            raise RuntimeError(f"State {name} was rendered twice")
        now = time.monotonic()
        elapsed, duration = now - self.started, now - self.previous
        self.previous = now
        self.records[name] = {"seconds": round(elapsed, 3), "duration_seconds": round(duration, 3), "blank": blank}
        if state is None:
            # Rendered although missing from preview_catalog.json: shown, but not counted in the selection.
            self.unregistered.append(name)
            print(f"[unregistered] {name}  {duration:.2f}s  (add it to preview_catalog.json)", flush=True)
        else:
            completed = sum(s.id in self.records for s in self.selection.states if s.category == state.category)
            total = sum(s.category == state.category for s in self.selection.states)
            print(f"[{state.group}] {completed}/{total}  {state.id}  {duration:.2f}s", flush=True)
        if self.verbose:
            print(f"[{len(self.records):3}] +{elapsed:6.1f}s {target}")
        # A full run saves hundreds of states; the manifest is rewritten at most once a second while running.
        if now - self.manifest_at >= 1.0:
            self.manifest("running")

    def manifest(self, status, error=None):
        self.manifest_at = time.monotonic()
        data = {
            "schema_version": 1,
            "status": status,
            "elapsed_seconds": round(time.monotonic() - self.started, 3),
            "timing_policy": "Capture intervals include prerequisite work since the previous selected capture or fixture start.",
            "states": [
                {
                    **vars(state),
                    "status": "saved" if state.id in self.records else "not_saved",
                    **self.records.get(state.id, {}),
                }
                for state in self.selection.states
            ],
            "unregistered_states": self.unregistered,
            "expected_notices": dict(self.notices),
            "error": error,
        }
        target = self.manifest_path
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")
        temporary.replace(target)

    def finish(self, error=None):
        missing = [state.id for state in self.selection.states if state.id not in self.records]
        blank = [name for name, record in self.records.items() if record["blank"]]
        status = "failed" if error or blank or missing else "completed"
        self.manifest(status, error)
        for category in self.selection.categories:
            states = [state for state in self.selection.states if state.category == category]
            print(f"{category}: {sum(s.id in self.records for s in states)}/{len(states)} saved")
        print(f"Rendered {len(self.records)} states in {time.monotonic() - self.started:.1f}s.")
        if self.notices:
            print(f"Expected offline notices: {sum(self.notices.values())}; details in {self.log_path}")
        if self.unregistered:
            print(
                f"WARNING: {len(self.unregistered)} rendered states are not in preview_catalog.json: {', '.join(self.unregistered)}"
            )
        problems = []
        if blank:
            problems.append(f"Blank renders: {', '.join(blank)}")
        if missing and not error:
            # A selected state that was never produced means the catalogue and renderer disagree.
            problems.append(
                f"Selected states not saved ({len(missing)}): {', '.join(missing)}; see {self.manifest_path}"
            )
        if problems:
            raise SystemExit("\n".join(problems))

    def close(self):
        self.log.close()
