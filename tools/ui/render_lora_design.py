"""Capture offline Qt Designer LoRA layout references without starting DTFlow.

These design-only forms are not wired to application services or provider operations.
They reuse the tracked DTFlow stylesheet and live outside the runtime forms directory.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FORMS = ROOT / "devdocs" / "design-system" / "ui_kits" / "lora" / "qt"
FORM_SIZES = {
    "connections": ("connections_lora.ui", (1190, 720)),
    "generate": ("generate_lora.ui", (1170, 720)),
    "prepare": ("prepare_lora_batch.ui", (1180, 650)),
    "picker": ("lora_picker.ui", (950, 610)),
    "metadata": ("lora_metadata_editor.ui", (810, 490)),
}


def parse_args() -> argparse.Namespace:
    """Read a destination and an optional single view; no provider configuration is needed."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "tmp" / "lora-design")
    parser.add_argument("--view", choices=FORM_SIZES, default=None)
    parser.add_argument(
        "--connections-expanded",
        action="store_true",
        help="Also capture the library-expanded Connections state.",
    )
    parser.add_argument(
        "--compact-prepare",
        action="store_true",
        help="Also capture the same six-column Prepare form at 760 px width.",
    )
    return parser.parse_args()


def _populate_canonical_prepare(window) -> None:
    """Fill the *runtime* Prepare Designer forms with synthetic, offline review rows."""
    from PySide6.QtWidgets import QSizePolicy
    from dtflow.ui.prepare_lora_editor import _load

    samples = (
        (
            "portrait_8e62d922f3a4c82b1aef990108a2fae9_lora_f16.ckpt",
            "Krea 2",
            "PortraitV2",
            "F16",
            "portrait style",
            "File-derived evidence",
            "Ready",
            "ok",
        ),
        (
            "cinematic_20261109_f3e817d0a432bf_lora_q8p.ckpt",
            "Z-Image Turbo",
            "Cinematic",
            "Q8P",
            "cinematic lighting",
            "File-derived evidence",
            "Ready",
            "ok",
        ),
        (
            "detail_boost_ep12_3a32f30bc9e7821d_911f.ckpt",
            "",
            "DetailBoost",
            "F16",
            "",
            "Required · not file-detected",
            "Needs architecture",
            "warning",
        ),
        ("nak_lora_f16.ckpt", "Krea 2", "Nak", "F16", "", "User-declared, unverified", "Ready", "ok"),
        ("portrait_77ad2a_lora_f16.ckpt", "Krea 2", "PortraitV2_2", "F16", "", "User-declared", "Ready", "ok"),
    )
    window.prepareEmpty.hide()
    for index, (filename, family, short, fmt, trigger, provenance, status, state) in enumerate(samples):
        row = _load("prepare_lora_row.ui", window.prepareBatchRows)
        row.prepareSourceFile.setToolTip(filename)
        row.prepareSourceFile.setText(filename)
        row.prepareSourceFile.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        row.prepareArchitecture.addItem("Krea 2", "krea_2")
        row.prepareArchitecture.addItem("Z-Image Turbo", "z_image")
        row.prepareArchitecture.addItem("FLUX.1", "flux1")
        if family:
            row.prepareArchitecture.setCurrentText(family)
        row.prepareArchitectureSource.setText(provenance)
        row.prepareFileEvidence.setText(
            (
                "Detected from file",
                "Detected from file",
                "Unknown inspection",
                "Declared by user",
                "Distinct-content fixture",
            )[index]
        )
        row.prepareShortName.setText(short)
        row.prepareFormat.setCurrentText(fmt)
        row.prepareTriggerWords.setText(trigger)
        row.prepareRowStatus.setText(status)
        row.prepareRowStatus.setProperty("role", "statusChip")
        row.prepareRowStatus.setProperty("state", state)
        row.prepareStatusDot.setProperty("state", state)
        row.prepareArchitectureSource.setProperty("state", "error" if not family else "unknown")
        row.prepareArchitecture.setProperty("invalid", not bool(family))
        row.setProperty("checkedSelection", index == 0)
        window.prepareBatchRowsLayout.insertWidget(index, row)
    window.prepareFileCount.setText("5 selected files")
    window.prepareSelectedCount.setText("Selected: 5")
    window.prepareAssignedCount.setText("Architectures assigned: 4/5")
    window.prepareReadyCount.setText("Ready: 4/5")
    window.prepareManagedFilename.setText("PortraitV2_Kr2_lora_f16.ckpt")
    window.prepareDisplayLabel.setText("Display label · PortraitV2[Kr2]")


def capture(app, name: str, target: Path, *, expanded: bool = False, compact: bool = False) -> None:
    """Load one design-only Designer form and capture its native Qt widget appearance."""
    from PySide6.QtCore import QFile, QIODevice, QPoint, Qt
    from PySide6.QtUiTools import QUiLoader
    from PySide6.QtWidgets import QToolButton, QWidget

    filename, size = FORM_SIZES[name]
    source = ROOT / "src" / "dtflow" / "ui" / "forms" if name == "prepare" else FORMS
    file = QFile(str(source / filename))
    if not file.open(QIODevice.OpenModeFlag.ReadOnly):
        raise OSError(f"Cannot read LoRA design form: {filename}")
    try:
        window = QUiLoader().load(file)
    finally:
        file.close()
    if not isinstance(window, QWidget):
        raise RuntimeError(f"Could not load Qt Designer LoRA form: {filename}")

    if name == "prepare":
        _populate_canonical_prepare(window)

    if name == "connections":
        library = window.findChild(QWidget, "customLorasDetails")
        if library is None:
            raise RuntimeError("Connections design lacks customLorasDetails")
        library.setVisible(expanded)
        disclosure = window.findChild(QToolButton, "customLorasDisclosure")
        if disclosure is not None:
            disclosure.setChecked(expanded)
            disclosure.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
            disclosure.setAccessibleName(f"Custom LoRAs, {'expanded' if expanded else 'collapsed'}")

    width, height = size
    if compact and name == "prepare":
        width = 760
    window.resize(width, height)
    window.show()
    app.processEvents()

    if name == "prepare":
        for index in range(5):
            row = window.prepareBatchRowsLayout.itemAt(index).widget()
            label = row.prepareSourceFile
            source_name = label.toolTip()
            label.setText(
                label.fontMetrics().elidedText(
                    source_name,
                    Qt.TextElideMode.ElideMiddle,
                    max(35, label.contentsRect().width() - 6),
                )
            )
            copy = row.prepareCopySource
            copy.clicked.connect(lambda _checked=False, filename=source_name: app.clipboard().setText(filename))
        app.processEvents()
        if compact:
            # The real six-column row must keep Status visible without horizontal scrolling.
            row = window.prepareBatchRowsLayout.itemAt(0).widget()
            scroll = window.prepareBatchScroll
            status = row.prepareRowStatus
            far_edge = status.mapTo(scroll.viewport(), QPoint(status.width(), 0)).x()
            if far_edge > scroll.viewport().width():
                raise RuntimeError(
                    f"Compact native Prepare status is clipped: {far_edge}px > {scroll.viewport().width()}px"
                )

    image = window.grab()
    if image.isNull() or not image.save(str(target), "PNG"):
        window.close()
        raise OSError(f"Could not save Qt capture: {target}")
    window.close()


def main() -> int:
    """Render local, synthetic reference forms without connecting to a backend."""
    args = parse_args()
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    sys.path.insert(0, str(ROOT / "src"))

    from PySide6.QtWidgets import QApplication
    from dtflow.ui.theme import apply_theme

    app = QApplication([])
    apply_theme(app)
    args.out.mkdir(parents=True, exist_ok=True)
    views = [args.view] if args.view else list(FORM_SIZES)

    for name in views:
        target = args.out / f"lora-{name}.png"
        capture(app, name, target)
        print(target)

    if "connections" in views and args.connections_expanded:
        target = args.out / "lora-connections-expanded.png"
        capture(app, "connections", target, expanded=True)
        print(target)

    if "prepare" in views and args.compact_prepare:
        target = args.out / "lora-prepare-compact.png"
        capture(app, "prepare", target, compact=True)
        print(target)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
