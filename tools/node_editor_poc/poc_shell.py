"""Shared window shell for the node-editor POC (#223): theme, fixture, thumbnail, inspector, smoke report.

Both packaged candidates (``nodeeditor_app.py`` and the Essentials-only ``baseline_app.py``) use this file
unchanged, so their EXE sizes and start times differ only by the canvas library. It deliberately does not
import ``dtflow``: the POC stays isolated and reads DTFlow's tracked theme files directly
(``src/dtflow/resources/styles``), the same tokens and QSS the app uses.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
import time
import traceback
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QImage, QKeySequence, QLinearGradient, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

import poc_contract as contract

PROCESS_T0 = time.perf_counter()  # after PySide6 import; external wall time covers the rest

if getattr(sys, "frozen", False):
    BUNDLE = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    RESOURCES = BUNDLE / "dtflow_resources"
    FIXTURE = BUNDLE / "reference_workflow_v1.json"
else:
    HERE = Path(__file__).resolve().parent
    RESOURCES = HERE.parents[1] / "src" / "dtflow" / "resources"
    FIXTURE = HERE / "reference_workflow_v1.json"
STYLES = RESOURCES / "styles"


def load_tokens() -> dict[str, str]:
    return json.loads((STYLES / "tokens.json").read_text(encoding="utf-8"))


TOKENS = load_tokens()


def token_color(name: str) -> QColor:
    value = TOKENS[name]
    match = re.fullmatch(r"rgba\((\d+),\s*(\d+),\s*(\d+),\s*(\d+)\)", value)
    return QColor(*map(int, match.groups())) if match else QColor(value)


def token_font(size_token: str = "type.body", weight_token: str = "type.weightBody") -> QFont:
    font = QFont(TOKENS["type.family"])
    font.setPointSizeF(float(TOKENS[size_token].removesuffix("pt")))
    font.setWeight(QFont.Weight(int(TOKENS[weight_token])))
    return font


def load_stylesheet() -> str:
    """DTFlow's tracked QSS with ${token} substitution and image URLs resolved, like dtflow.ui.theme."""
    source = (STYLES / "dtflow_dark.qss").read_text(encoding="utf-8")
    resolved = re.sub(r"\$\{([\w.]+)\}", lambda m: TOKENS[m.group(1)], source)
    resolved = re.sub(
        r"url\((\.\./images/(?:gallery|controls)/[^)]+)\)",
        lambda m: f'url("{(STYLES / m.group(1)).resolve().as_posix()}")',
        resolved,
    )
    # POC-only rules: node content proxies must not inherit the global QWidget background.
    return resolved + (
        "\nQWidget#dtfNodeContent, QWidget#dtfNodeContent QLabel { background: transparent; }"
        f"\nQWidget#dtfNodeContent QLabel {{ color: {TOKENS['color.secondary']}; font-size: {TOKENS['type.small']}; }}"
    )


def fixture_thumbnail(seed: int = 7, size: int = 256) -> QPixmap:
    """Deterministic synthetic 'generated image' standing in for a real output (no model, no file)."""
    image = QImage(size, size, QImage.Format.Format_RGB32)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    sky = QLinearGradient(0, 0, 0, size)
    sky.setColorAt(0, QColor.fromHsv((seed * 37) % 360, 120, 230))
    sky.setColorAt(1, QColor.fromHsv((seed * 37 + 40) % 360, 160, 90))
    painter.fillRect(0, 0, size, size, sky)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(255, 214, 160))
    painter.drawEllipse(QRectF(size * 0.34, size * 0.22, size * 0.32, size * 0.40))  # face
    painter.setBrush(QColor(70, 45, 30))
    painter.drawChord(QRectF(size * 0.32, size * 0.16, size * 0.36, size * 0.30), 0, 180 * 16)  # hair
    painter.setBrush(QColor(40, 60, 90))
    painter.drawRoundedRect(QRectF(size * 0.22, size * 0.62, size * 0.56, size * 0.42), 30, 30)  # shoulders
    painter.setPen(QColor(255, 255, 255, 200))
    painter.setFont(QFont(TOKENS["type.family"], max(8, size // 20)))
    painter.drawText(QPointF(8, size - 10), "fixture")
    painter.end()
    return QPixmap.fromImage(image)


class LoraEditor(QWidget):
    """Configuration control only: rows of [file, weight]. Not DTFlow's LoRA services or selector."""

    changed = Signal(list)

    def __init__(self, param: contract.Param):
        super().__init__()
        self.param = param
        self.rows: list[tuple[QComboBox, QDoubleSpinBox, QWidget]] = []
        self.box = QVBoxLayout(self)
        self.box.setContentsMargins(0, 0, 0, 0)
        add = QPushButton("Add LoRA")
        add.clicked.connect(lambda: (self._add_row(param.choices[0], 1.0), self._emit()))
        self.box.addWidget(add)

    def set_value(self, value: list) -> None:
        while self.rows:
            self._remove_row(self.rows[-1][2], emit=False)
        for name, weight in value:
            self._add_row(name, weight)

    def _add_row(self, name: str, weight: float) -> None:
        row = QWidget()
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 0, 0, 0)
        combo = QComboBox()
        combo.setEditable(True)
        combo.addItems(self.param.choices)
        combo.setCurrentText(name)
        combo.lineEdit().setCursorPosition(0)
        combo.setMinimumContentsLength(8)
        combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        spin = QDoubleSpinBox()
        spin.setRange(self.param.minimum, self.param.maximum)
        spin.setSingleStep(self.param.step)
        spin.setValue(weight)
        remove = QPushButton("×")
        remove.setFixedWidth(28)
        remove.clicked.connect(lambda: self._remove_row(row))
        combo.currentTextChanged.connect(self._emit)
        spin.valueChanged.connect(self._emit)
        line.addWidget(combo, 1)
        line.addWidget(spin)
        line.addWidget(remove)
        self.box.insertWidget(len(self.rows), row)
        self.rows.append((combo, spin, row))

    def _remove_row(self, row: QWidget, emit: bool = True) -> None:
        self.rows = [entry for entry in self.rows if entry[2] is not row]
        row.deleteLater()
        if emit:
            self._emit()

    def _emit(self, *_args) -> None:
        self.changed.emit([[combo.currentText(), spin.value()] for combo, spin, _row in self.rows])


class Inspector(QFrame):
    """DTFlow-style inspector bound to the canvas selection. Emits (node_id, param, value) on edits."""

    paramEdited = Signal(int, str, object)

    def __init__(self):
        super().__init__()
        self.setObjectName("inspectorPanel")
        self.setMinimumWidth(320)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        eyebrow = QLabel("INSPECTOR")
        eyebrow.setObjectName("inspectorEyebrowLabel")
        self.title = QLabel("Nothing selected")
        self.title.setObjectName("workspaceTitleLabel")
        self.subtitle = QLabel("Select one node on the canvas.")
        self.subtitle.setProperty("role", "secondary")
        self.subtitle.setWordWrap(True)
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.hide()
        outer.addWidget(eyebrow)
        outer.addWidget(self.title)
        outer.addWidget(self.subtitle)
        outer.addWidget(self.preview)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)  # no sideways scroll
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(self.scroll, 1)
        self.node_id: int | None = None
        self.editors: dict[str, tuple[contract.Param, QWidget]] = {}
        self._updating = False

    def clear(self, message: str = "Select one node on the canvas.") -> None:
        self.node_id = None
        self.editors = {}
        self.title.setText("Nothing selected")
        self.subtitle.setText(message)
        self.preview.hide()
        self.scroll.setWidget(QWidget())

    def show_node(self, node_id: int, node_type: str, params: dict[str, object], thumbnail: QPixmap | None) -> None:
        spec = contract.CATALOG[node_type]
        if self.node_id == node_id and set(self.editors) == {p.name for p in spec.params}:
            self.refresh(params)
            return
        self.node_id = node_id
        self.title.setText(spec.title)
        inputs = ", ".join(f"{p.name}:{p.type}" for p in spec.inputs) or "none"
        outputs = ", ".join(f"{p.name}:{p.type}" for p in spec.outputs) or "none"
        self.subtitle.setText(f"{spec.type} #{node_id}\nInputs: {inputs}\nOutputs: {outputs}")
        if thumbnail is not None:
            self.preview.setPixmap(thumbnail.scaled(160, 160, Qt.AspectRatioMode.KeepAspectRatio))
            self.preview.show()
        else:
            self.preview.hide()
        body = QWidget()
        form = QVBoxLayout(body)  # labels above fields: fits the narrow rail without sideways scroll
        form.setContentsMargins(0, 8, 4, 8)
        self.editors = {}
        for param in spec.params:
            editor = self._editor(param)
            self.editors[param.name] = (param, editor)
            label = QLabel(param.label)
            label.setProperty("role", "secondary")
            form.addWidget(label)
            form.addWidget(editor)
            form.addSpacing(4)
        form.addStretch(1)
        self.scroll.setWidget(body)
        self.refresh(params)

    def refresh(self, params: dict[str, object]) -> None:
        self._updating = True
        try:
            for name, (_param, editor) in self.editors.items():
                value = params[name]
                if isinstance(editor, LoraEditor):
                    editor.set_value(value)
                elif isinstance(editor, QComboBox):
                    editor.setCurrentText(value)
                    if editor.lineEdit() is not None:
                        editor.lineEdit().setCursorPosition(0)
                elif isinstance(editor, QPlainTextEdit):
                    if editor.toPlainText() != value:
                        editor.setPlainText(value)
                elif isinstance(editor, QLineEdit):
                    if editor.text() != value:
                        editor.setText(value)
                else:
                    editor.setValue(value)
        finally:
            self._updating = False

    def _editor(self, param: contract.Param) -> QWidget:
        emit = self._emitter(param)
        if param.kind == "loras":
            editor = LoraEditor(param)
            editor.changed.connect(emit)
        elif param.kind in ("checkpoint", "choice"):
            editor = QComboBox()
            editor.addItems(param.choices)
            editor.setEditable(param.kind == "checkpoint")  # placeholder ids; no model service in the POC
            editor.currentTextChanged.connect(emit)
        elif param.kind == "text":
            editor = QPlainTextEdit()
            editor.setFixedHeight(64)
            editor.textChanged.connect(lambda: emit(editor.toPlainText()))
        elif param.kind == "string":
            editor = QLineEdit()
            editor.textEdited.connect(emit)
        elif param.kind == "int":
            editor = QSpinBox()
            editor.setRange(int(param.minimum), min(int(param.maximum), 2**31 - 1))
            editor.setSingleStep(int(param.step))
            editor.valueChanged.connect(emit)
        else:
            editor = QDoubleSpinBox()
            editor.setRange(param.minimum, param.maximum)
            editor.setSingleStep(param.step)
            editor.setDecimals(2)
            editor.valueChanged.connect(emit)
        return editor

    def _emitter(self, param: contract.Param):
        def emit(value):
            if self._updating or self.node_id is None:
                return
            try:
                value = contract.check_param(param, value)
            except contract.WorkflowError:
                return  # e.g. an empty checkpoint while typing; the stored value stays valid
            self.paramEdited.emit(self.node_id, param.name, value)

        return emit


class PocWindow(QMainWindow):
    """Window shell. Subclasses provide the canvas and the graph <-> canvas mapping."""

    CANDIDATE = "abstract"
    SUPPORTS_EDITING = True

    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"DTFlow node editor POC · {self.CANDIDATE}")
        self.resize(1440, 860)
        self.path: Path | None = None
        self.thumbnail = fixture_thumbnail()
        self.inspector = Inspector()
        self.inspector.paramEdited.connect(self.set_node_param)
        splitter = QSplitter()
        splitter.addWidget(self.build_canvas())
        splitter.addWidget(self.inspector)
        splitter.setStretchFactor(0, 1)
        splitter.setSizes([1080, 360])
        self.setCentralWidget(splitter)
        self._build_actions()
        self.statusBar().showMessage("Ready")

    # --- subclass API -------------------------------------------------------------------------------
    def build_canvas(self) -> QWidget:
        raise NotImplementedError

    def canvas_focus_widget(self) -> QWidget:
        raise NotImplementedError

    def load_graph(self, graph: contract.Graph) -> None:
        raise NotImplementedError

    def current_graph(self) -> contract.Graph:
        raise NotImplementedError

    def set_node_param(self, node_id: int, name: str, value: object) -> None:
        raise NotImplementedError

    def add_node(self, node_type: str) -> None:
        raise NotImplementedError

    def undo(self) -> None:
        pass

    def redo(self) -> None:
        pass

    def delete_selected(self) -> None:
        pass

    def fit_view(self) -> None:
        pass

    # --- shared behaviour ---------------------------------------------------------------------------
    def _build_actions(self) -> None:
        bar = QToolBar("Workflow")
        bar.setMovable(False)
        self.addToolBar(bar)
        canvas = self.canvas_focus_widget()

        def action(text, slot, shortcut=None, canvas_only=False, enabled=True):
            item = QAction(text, self)
            item.triggered.connect(slot)
            item.setEnabled(enabled)
            if shortcut is not None:
                item.setShortcut(QKeySequence(shortcut))
                if canvas_only:
                    # Keep Ctrl+Z / Delete for text fields while the inspector has focus.
                    item.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
                    canvas.addAction(item)
            bar.addAction(item)
            return item

        action("Open…", self.open_dialog, QKeySequence.StandardKey.Open)
        action("Save", self.save, QKeySequence.StandardKey.Save)
        action("Save as…", self.save_dialog, QKeySequence.StandardKey.SaveAs)
        action("Reload fixture", lambda: self.open_path(FIXTURE))
        bar.addSeparator()
        editing = self.SUPPORTS_EDITING
        action("Undo", self.undo, QKeySequence.StandardKey.Undo, True, editing)
        action("Redo", self.redo, QKeySequence.StandardKey.Redo, True, editing)
        action("Delete", self.delete_selected, QKeySequence.StandardKey.Delete, True, editing)
        action("Fit", self.fit_view, "F", True)
        bar.addSeparator()
        for spec in contract.CATALOG.values():
            action(f"+ {spec.title}", lambda _=False, t=spec.type: self.add_node(t), enabled=editing)

    def thumbnail_for(self, node_type: str) -> QPixmap | None:
        return self.thumbnail if contract.CATALOG[node_type].thumbnail else None

    def show_status(self, message: str, error: bool = False) -> None:
        self.statusBar().setStyleSheet(f"color: {TOKENS['color.error' if error else 'color.secondary']};")
        self.statusBar().showMessage(message, 8000)

    def open_dialog(self) -> None:
        name, _ = QFileDialog.getOpenFileName(self, "Open workflow", str(self.path or FIXTURE), "Workflow (*.json)")
        if name:
            self.open_path(Path(name))

    def open_path(self, path: Path) -> bool:
        try:
            graph = contract.load_workflow(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:  # WorkflowError is a ValueError, like JSON errors
            QMessageBox.warning(self, "Workflow rejected", f"{path.name} was not opened.\n\n{exc}")
            return False
        self.path = path if path != FIXTURE else None
        self.inspector.clear()
        self.load_graph(graph)
        notes = graph.diagnostics()
        self.show_status(f"Opened {path.name}: {len(graph.nodes)} nodes, {len(graph.links)} links. " + " ".join(notes))
        return True

    def save(self) -> None:
        if self.path is None:
            self.save_dialog()
        else:
            self.save_path(self.path)

    def save_dialog(self) -> None:
        name, _ = QFileDialog.getSaveFileName(self, "Save workflow", "workflow.json", "Workflow (*.json)")
        if name:
            self.save_path(Path(name))

    def save_path(self, path: Path) -> bool:
        try:
            data = contract.dump_workflow(self.current_graph())
            contract.load_workflow(data)  # never write a file this editor would refuse to reopen
            path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Not saved", str(exc))
            return False
        self.path = path
        self.show_status(f"Saved {path.name}")
        return True


def _binding_report() -> dict:
    """What actually got bundled: Essentials-only means the Addons modules are absent."""
    import PySide6
    from PySide6 import QtCore

    addons = ("PySide6.QtWebEngineCore", "PySide6.Qt3DCore", "PySide6.QtCharts", "PySide6.QtDataVisualization")
    report = {
        "pyside6": PySide6.__version__,
        "qt_runtime": QtCore.qVersion(),
        "addons_modules_present": [name for name in addons if importlib.util.find_spec(name) is not None],
        "other_bindings_present": [
            name for name in ("PyQt5", "PyQt6", "PySide2") if importlib.util.find_spec(name) is not None
        ],
    }
    if "qtpy" in sys.modules:
        report["qtpy_api"] = sys.modules["qtpy"].API_NAME
    return report


def run(window_class: type[PocWindow], argv: list[str] | None = None) -> int:
    """Entry point. ``--smoke-report PATH`` writes start-up evidence after the first frame, then quits."""
    argv = list(sys.argv if argv is None else argv)
    report_path = None
    if "--smoke-report" in argv:
        report_path = Path(argv[argv.index("--smoke-report") + 1])

        # Windowed EXEs have no usable console. Retain startup/timer exceptions
        # at the explicitly requested report path instead of losing the cause.
        def smoke_exception(exc_type, exc, tb):
            report_path.write_text(
                json.dumps({"error": "".join(traceback.format_exception(exc_type, exc, tb))}, indent=2),
                encoding="utf-8",
            )
            instance = QApplication.instance()
            if instance is not None:
                instance.exit(1)

        sys.excepthook = smoke_exception
    open_path = Path(argv[argv.index("--open") + 1]) if "--open" in argv else FIXTURE
    app = QApplication(argv)
    t_app = time.perf_counter()
    app.setStyleSheet(load_stylesheet())
    window = window_class()
    if not window.open_path(open_path):
        if report_path is not None:
            raise RuntimeError(f"Smoke fixture could not be opened: {open_path}")
        window.show_status(f"Could not open {open_path.name}", error=True)
    window.show()
    t_show = time.perf_counter()

    def first_frame() -> None:
        if report_path is None:
            return
        graph = window.current_graph()
        first = contract.dump_workflow(graph)
        second = contract.dump_workflow(contract.load_workflow(first))
        report = {
            "candidate": window.CANDIDATE,
            "frozen": bool(getattr(sys, "frozen", False)),
            "python": sys.version.split()[0],
            "executable": sys.executable,
            "wall_time_first_frame": time.time(),
            "seconds_shell_import_to_qapplication": round(t_app - PROCESS_T0, 3),
            "seconds_shell_import_to_window_shown": round(t_show - PROCESS_T0, 3),
            "seconds_shell_import_to_first_frame": round(time.perf_counter() - PROCESS_T0, 3),
            "nodes": len(graph.nodes),
            "links": len(graph.links),
            "export_reimport_stable": first == second,
            "binding": _binding_report(),
        }
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        app.quit()

    QTimer.singleShot(0, first_frame)
    return app.exec()
