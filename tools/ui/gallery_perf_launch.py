"""Apply a validated performance profile before launching the real Windows DTFlow GUI.

This is an explicit development-only harness. It does not change production
defaults or persist these overrides in the user's viewer settings.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
from functools import partial
from pathlib import Path

DEFAULTS = {
    "thumbnail_workers": 6,
    "lookahead_screens": 3,
    "preparation_budget_ms": 4.0,
    "mount_budget_ms": 4.0,
    "thumbnail_side_px": 600,
    "pending_cache_writes": 256,
    "cache_entries": 20000,
    "cache_prune_to": 16000,
    "cache_prune_every": 500,
    "refresh_ms": 5000,
    "full_refresh_s": 30,
    "filter_debounce_ms": 300,
    "save_debounce_ms": 250,
    "page_jump_debounce_ms": 40,
}
RANGES = {
    "thumbnail_workers": (1, 12),
    "lookahead_screens": (1, 6),
    "preparation_budget_ms": (1, 12),
    "mount_budget_ms": (1, 12),
    "thumbnail_side_px": (160, 1200),
    "pending_cache_writes": (16, 1024),
    "cache_entries": (1000, 40000),
    "cache_prune_to": (500, 35000),
    "cache_prune_every": (20, 2000),
    "refresh_ms": (1000, 30000),
    "full_refresh_s": (5, 120),
    "filter_debounce_ms": (50, 1000),
    "save_debounce_ms": (50, 1000),
    "page_jump_debounce_ms": (10, 200),
}


def validate_profile(overrides: dict) -> dict:
    """Reject unknown or unsafe values; use unchanged product defaults otherwise."""

    if not isinstance(overrides, dict):
        raise ValueError("Performance profile must be a JSON object.")
    profile = dict(DEFAULTS)
    for name, value in overrides.items():
        if name not in DEFAULTS:
            raise ValueError(f"Unrecognized performance parameter: {name}")
        original = DEFAULTS[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{name} must be numeric.")
        if isinstance(original, int) and (not isinstance(value, int)):
            raise ValueError(f"{name} must be an integer.")
        low, high = RANGES[name]
        if not low <= value <= high:
            raise ValueError(f"{name} must be in {low}..{high}.")
        profile[name] = value
    if profile["cache_prune_to"] >= profile["cache_entries"]:
        raise ValueError("cache_prune_to must be smaller than cache_entries.")
    if profile["full_refresh_s"] * 1000 < profile["refresh_ms"]:
        raise ValueError("full_refresh_s cannot be shorter than the refresh interval.")
    return profile


def apply_profile(profile: dict) -> None:
    """Change only the gallery/cache tunables in this benchmark child process."""

    from dtflow.persistence import thumbnail_cache
    from dtflow.ui import gallery_page
    from dtflow.ui.gallery_preparation import GalleryPreparation

    gallery_page.THUMBNAIL_CONCURRENCY = profile["thumbnail_workers"]
    gallery_page.LOOKAHEAD_SCREENS = profile["lookahead_screens"]
    gallery_page.THUMBNAIL_SIDE = profile["thumbnail_side_px"]
    gallery_page.AUTO_REFRESH_MS = profile["refresh_ms"]
    gallery_page.AUTO_REFRESH_FULL_INTERVAL_S = profile["full_refresh_s"]
    gallery_page.GalleryPreparation = partial(GalleryPreparation, budget_ms=profile["preparation_budget_ms"])

    # No writer exists yet: the child process has not entered Qt or scheduled
    # any thumbnail work. Nothing is replaced in an active DTFlow session.
    thumbnail_cache.PENDING_WRITES = profile["pending_cache_writes"]
    thumbnail_cache._writes = queue.Queue(maxsize=profile["pending_cache_writes"])
    thumbnail_cache.PRUNE_EVERY = profile["cache_prune_every"]
    original_prune = thumbnail_cache.prune

    def benchmark_prune(state: Path, *, limit: int | None = None, keep: int | None = None) -> int:
        return original_prune(
            state,
            limit=profile["cache_entries"] if limit is None else limit,
            keep=profile["cache_prune_to"] if keep is None else keep,
        )

    thumbnail_cache.prune = benchmark_prune

    original_init = gallery_page.GalleryPage.__init__

    def benchmark_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self._grid_mount_budget_ms = profile["mount_budget_ms"]
        self.filter_timer.setInterval(profile["filter_debounce_ms"])
        self.save_timer.setInterval(profile["save_debounce_ms"])
        self._pending_jump_timer.setInterval(profile["page_jump_debounce_ms"])
        self.auto_timer.setInterval(profile["refresh_ms"])

    gallery_page.GalleryPage.__init__ = benchmark_init


def enable_grid_progress_logging() -> None:
    """Expose visible image indices in development logs for the Grid matrix only.

    GalleryPager.present already receives the calculated visible range.
    Observing its arguments avoids an extra Qt layout traversal on each wheel
    event and does not affect non-matrix or production sessions.
    """

    import logging
    from time import monotonic

    from dtflow.ui.gallery_pager import GalleryPager

    original = GalleryPager.present
    logger = logging.getLogger("dtflow.ui.gallery_page")
    sampled = {"first": None, "time": 0.0}

    def observed(self, *, total, visible_start, visible_end, loaded, **kwargs):
        result = original(
            self,
            total=total,
            visible_start=visible_start,
            visible_end=visible_end,
            loaded=loaded,
            **kwargs,
        )
        now = monotonic()
        if visible_start != sampled["first"] and (now - sampled["time"] >= 0.07 or visible_start <= 1):
            sampled["first"], sampled["time"] = visible_start, now
            logger.debug(
                "gallery_perf phase=benchmark_view first=%d last=%d total=%d loaded=%d",
                visible_start,
                visible_end,
                total,
                loaded,
            )
        return result

    GalleryPager.present = observed


def main(argv: list[str] | None = None) -> int:
    """Launch native DTFlow with one benchmark profile and existing -x logs."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, required=True, help="Profile JSON created by the benchmark driver.")
    parser.add_argument("--logs", type=Path, required=True, help="Fresh trial diagnostic folder.")
    parser.add_argument("--pid-file", type=Path, help="Internal trial file for the actual GUI interpreter PID.")
    parser.add_argument("--no-cache", action="store_true", help="Do not read or write disposable thumbnail cache.")
    parser.add_argument("--ml", type=int, default=20, help="Gallery batch size passed as DTFlow -ml (default: 20).")
    parser.add_argument(
        "--mc", type=int, default=100, help="Gallery mounted-card cap passed as DTFlow -mc (default: 100)."
    )
    parser.add_argument(
        "--grid-progress",
        action="store_true",
        help="Emit Grid matrix visible-index telemetry from pager updates.",
    )
    args = parser.parse_args(argv)
    from dtflow.gallery_limits import resolve as resolve_gallery_limits

    try:
        ml, mc = resolve_gallery_limits(args.ml, args.mc)
    except ValueError as exc:
        parser.error(str(exc))
    profile = validate_profile(json.loads(args.profile.read_text(encoding="utf-8")))
    if args.pid_file is not None:
        # Windows virtual-environment python.exe can be a redirector. Popen.pid
        # then identifies that launcher, not this interpreter hosting the GUI.
        temporary = args.pid_file.with_suffix(".tmp")
        temporary.write_text(json.dumps({"pid": os.getpid()}) + "\n", encoding="utf-8", newline="\n")
        temporary.replace(args.pid_file)
    apply_profile(profile)
    if args.grid_progress:
        enable_grid_progress_logging()
    from dtflow.cli import main as dtflow_main

    cli = ["-x", "-q", str(args.logs), "gui", "-ml", str(ml), "-mc", str(mc), "-format", "jpg"]
    if args.no_cache:
        cli.append("-nc")
    return dtflow_main(cli)


if __name__ == "__main__":
    raise SystemExit(main())
