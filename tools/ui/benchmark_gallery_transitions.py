"""Measure ordinary 100/200-card presentation, without DB, image IO or providers.

Run: uv run --frozen python tools/ui/benchmark_gallery_transitions.py --out tmp/gallery-benchmark
Set QT_QPA_PLATFORM=xcb and DISPLAY for native Linux; offscreen is the default.
Both paths use this checkout: 'production' is the synchronous fixture fallback,
'prepared' stages real card/cell/filmstrip widgets. This is not a main-branch
comparison, Windows acceptance or an FPS guarantee. Timers include Qt layout,
paint and deferred deletion after commit; values depend on host load and QTest.
"""

import argparse
import json
import os
import statistics
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests"))
from support.network_guard import install  # noqa: E402

install()
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, QTimer, Signal  # noqa: E402
from PySide6.QtGui import QColor, QIcon, QImage, QPixmap  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from dtflow.core.gallery import GalleryItem  # noqa: E402
from dtflow.ui.gallery_page import GalleryPage  # noqa: E402
from dtflow.ui.main_window import FORMS_DIR, load_ui  # noqa: E402
from dtflow.ui.theme import apply_theme  # noqa: E402


class ReadlessRunner(QObject):
    """Close intercepted fixture workers; do not simulate durable outcomes."""

    shutdown_started = Signal()
    closed = False

    def submit(self, coroutine, callback):
        coroutine.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=10)
    parser.add_argument("--view", choices=["Grid", "List"], default="Grid")
    args = parser.parse_args()
    assert args.repeats > 0
    args.out.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    apply_theme(app)
    picture = QImage(256, 192, QImage.Format.Format_RGB32)
    picture.fill(QColor("#567789"))
    pixmap = QPixmap.fromImage(picture)
    base = GalleryItem(
        "0",
        "fixture",
        "fixture",
        "/fixture/output.png",
        "/fixture/meta.json",
        "2026-10-18T06:00:00+00:00",
        "fake",
        "fixture",
        "Offline presentation fixture",
        "",
        256,
        192,
        (),
        "{}",
        8000,
    )
    items = [replace(base, identity=str(i), attempt_id=f"fixture-{i}") for i in range(300)]
    result = {
        "qpa": app.platformName(),
        "view": args.view,
        "scope": "ordinary 100/200-record presentation; no IO, providers or generation",
        "production": [],
        "prepared": [],
    }
    temporary = tempfile.TemporaryDirectory(prefix="dtflow-normal-transitions-")
    root = load_ui(FORMS_DIR / "gallery_detail_page.ui")
    runner = ReadlessRunner(root)
    gallery = GalleryPage(root, runner=runner, state_dir=Path(temporary.name))
    root.resize(1600, 1000)
    root.show()
    gallery.auto_button.setChecked(False)
    gallery.show_view(args.view)
    QTest.qWait(100)

    commit_times = []

    def production(records):
        commit_start = time.perf_counter()
        gallery.items = list(records)
        gallery.offset = int(records[0].identity) if records else 0
        gallery.total = 300
        gallery._render_page()
        gallery._rendered_page = (gallery.offset, tuple(records))
        for row, item in enumerate(records):
            gallery._grid_painters[item.identity].pixmap = pixmap
            gallery._film_entries[item.identity][1].pixmap = pixmap
            gallery.table.item(row, 1).setIcon(QIcon(pixmap))
        commit_times.append((time.perf_counter() - commit_start) * 1000)

    def measure(kind, label, records, action):
        queued = []
        ticks = []
        last = time.perf_counter()
        timer = QTimer()
        timer.setInterval(4)

        def tick():
            nonlocal last
            now = time.perf_counter()
            ticks.append((now - last) * 1000)
            last = now

        timer.timeout.connect(tick)
        timer.start()
        QTest.qWait(12)
        start = time.perf_counter()
        QTimer.singleShot(0, lambda: queued.append((time.perf_counter() - start) * 1000))
        action(records)
        mutation = (time.perf_counter() - start) * 1000
        if kind == "prepared":
            deadline = time.monotonic() + 5
            while gallery.preparation.active:
                assert time.monotonic() < deadline
                QTest.qWait(2)
        ready_ms = (time.perf_counter() - start) * 1000
        QTest.qWait(80)
        timer.stop()
        result[kind].append(
            dict(
                transition=label,
                records=len(records),
                mutation_ms=mutation,
                ready_ms=ready_ms,
                commit_ms=commit_times[-1],
                queued_callback_ms=queued[0],
                max_heartbeat_gap_ms=max(ticks, default=0),
            )
        )

    # Initial allocation is separate from the repeated ordinary navigation cases.
    measure("production", "initial-100", items[:100], production)
    for _ in range(args.repeats):
        measure("production", "append-100-to-200", items[:200], production)
        measure("production", "switch-to-next-100", items[200:300], production)
        measure("production", "switch-back-100", items[:100], production)
    root.hide()
    prepared_root = load_ui(FORMS_DIR / "gallery_detail_page.ui")
    prepared_runner = ReadlessRunner(prepared_root)
    gallery = GalleryPage(prepared_root, runner=prepared_runner, state_dir=Path(temporary.name) / "prepared")
    prepared_root.resize(1600, 1000)
    prepared_root.show()
    gallery.auto_button.setChecked(False)
    gallery.show_view(args.view)

    def prepared(records):
        gallery._prepare_page(records, gallery._query_generation, lambda: production(records))

    measure("prepared", "initial-100", items[:100], prepared)
    for _ in range(args.repeats):
        measure("prepared", "append-100-to-200", items[:200], prepared)
        measure("prepared", "switch-to-next-100", items[200:300], prepared)
        measure("prepared", "switch-back-100", items[:100], prepared)
    result["summary"] = {}
    for kind in ("production", "prepared"):
        result["summary"][kind] = {}
        for label in ("initial-100", "append-100-to-200", "switch-to-next-100", "switch-back-100"):
            rows = [r for r in result[kind] if r["transition"] == label]
            result["summary"][kind][label] = {
                k: {"median": statistics.median(r[k] for r in rows), "max": max(r[k] for r in rows)}
                for k in ("mutation_ms", "ready_ms", "commit_ms", "queued_callback_ms", "max_heartbeat_gap_ms")
            }
    (args.out / "results.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result["summary"]), flush=True)
    prepared_root.grab().save(str(args.out / "prepared.png"))
    gallery.auto_timer.stop()
    gallery.save_timer.stop()
    runner.closed = True
    root.close()
    prepared_root.close()
    app.processEvents()
    temporary.cleanup()


if __name__ == "__main__":
    main()
