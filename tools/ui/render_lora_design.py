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
    "prepare": ("prepare_lora_batch.ui", (1180, 570)),
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


def capture(app, name: str, target: Path, *, expanded: bool = False, compact: bool = False) -> None:
    """Load one design-only Designer form and capture its native Qt widget appearance."""
    from PySide6.QtCore import QFile, QIODevice, Qt
    from PySide6.QtUiTools import QUiLoader
    from PySide6.QtWidgets import QLabel, QToolButton, QWidget

    filename, size = FORM_SIZES[name]
    file = QFile(str(FORMS / filename))
    if not file.open(QIODevice.OpenModeFlag.ReadOnly):
        raise OSError(f"Cannot read LoRA design form: {filename}")
    try:
        window = QUiLoader().load(file)
    finally:
        file.close()
    if not isinstance(window, QWidget):
        raise RuntimeError(f"Could not load Qt Designer LoRA form: {filename}")

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
        # Keep the source column narrow, middle-elide in the actual available width,
        # and retain the full filename as the native Qt hover tooltip.
        for index in range(5):
            label = window.findChild(QLabel, f"sourceFile{index}")
            if label is None:
                raise RuntimeError(f"Prepare design lacks sourceFile{index}")
            source_name = label.toolTip()
            if not source_name:
                raise RuntimeError(f"Prepare design lacks full filename tooltip for sourceFile{index}")
            label.setText(
                label.fontMetrics().elidedText(
                    source_name,
                    Qt.TextElideMode.ElideMiddle,
                    max(48, label.contentsRect().width() - 8),
                )
            )
        app.processEvents()

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
