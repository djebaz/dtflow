"""Interactive native requestFromUser controls, mounted in the existing Designer host.

The standalone Scripts mockup's wrapped captions, compact tracks, value labels,
section headings and responsive stacking are reused through ScriptForm's row seam.
No script code, HTML, filesystem path or network resource is executed here.
"""

from __future__ import annotations

import asyncio
import copy
import html
from decimal import Decimal

from PySide6.QtCore import QSize, Qt, Signal, Slot
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSlider,
    QSpinBox,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from dtflow.scripting.forms import (
    MAX_TEXT,
    contributes,
    decimals,
    image_sources,
    initial_answers,
    limits,
    validate_answers,
    validate_components,
)
from dtflow.ui.scripts_form import ScriptForm, _RangeControl, _group, _row


def _text(value: str, *, heading=False) -> QLabel:
    label = QLabel(value)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    label.setMinimumWidth(0)
    label.setObjectName("scriptsFormSection" if heading else "scriptsFormSupporting")
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


class _SafeMarkdown(QTextBrowser):
    """Native Markdown without HTML, link navigation or resource access."""

    def __init__(self, value):
        super().__init__()
        self.setObjectName("scriptsFormSupporting")
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.setReadOnly(True)
        self.setMinimumWidth(0)
        self.setMinimumHeight(64)
        self.setMaximumHeight(360)
        self.setMarkdown(html.escape(value, quote=False))

    def loadResource(self, resource_type, url):
        return None  # Never read a script-supplied URL or local resource.

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.document().setTextWidth(self.viewport().width())
        self.setMinimumHeight(min(360, max(64, round(self.document().size().height()) + 18)))


class LiveScriptForm(ScriptForm):
    """Stable per-prompt controls: polling does not rebuild or reset user edits."""

    busyChanged = Signal(bool)
    problem = Signal(str)

    def __init__(self, runner, parent=None):
        super().__init__(parent)
        self.runner = runner
        self.identity = None
        self._values = []
        self._components = []
        self._pickers = []
        self._pending = 0
        self._generation = 0
        self._submitting = False

    @property
    def busy(self):
        return bool(self._pending or self._pickers or self._submitting)

    @property
    def submitting(self):
        return self._submitting

    def _notify_busy(self):
        self.busyChanged.emit(self.busy)

    def set_submitting(self, active: bool):
        self._submitting = active
        self.setEnabled(not active)
        self._notify_busy()

    @Slot()
    def clear_request(self):
        self._generation += 1
        self.identity = None
        for picker in tuple(self._pickers):
            picker.reject()
        self._pickers.clear()
        self._pending = 0
        self._submitting = False
        self.setEnabled(True)
        self.set_fields([])
        self._values, self._components = [], []
        self._notify_busy()

    def set_request(self, state, result: dict):
        self.clear_request()
        prompt = result["prompt"]
        validate_components(prompt.components)
        self.identity = (prompt.run_id, prompt.ordinal)
        self.state = state
        self._components = prompt.components
        values = result["answers"]
        self._values = copy.deepcopy(initial_answers(prompt.components) if values is None else values)
        validate_answers(self._components, self._values)
        self._thumbnails = result["thumbnails"]
        # Remove the preview form's trailing stretch; real groups follow source order.
        self.layout_.takeAt(self.layout_.count() - 1)
        self._build(prompt.components, self.layout_, ())
        self.layout_.addStretch(1)
        self.set_narrow(self.window().width() < 1100)

    def answers(self) -> list:
        if self.identity is None or self.busy:
            raise ValueError("Wait for this form's selected inputs to finish preparing.")
        validate_answers(self._components, self._values)
        return copy.deepcopy(self._values)

    def _get(self, path):
        value = self._values
        for index in path:
            value = value[index]
        return value

    def _put(self, path, value):
        parent = self._values
        for index in path[:-1]:
            parent = parent[index]
        parent[path[-1]] = value

    def _build(self, fields, layout, path):
        answer_index = 0
        for spec in fields:
            kind = spec["type"]
            here = (*path, answer_index)
            if contributes(spec):
                answer_index += 1
            if kind == "section":
                group = QWidget()
                content = QVBoxLayout(group)
                content.setContentsMargins(0, 4, 0, 0)
                content.setSpacing(8)
                content.addWidget(_text(spec.get("title") or "Section", heading=True))
                if spec.get("detail"):
                    content.addWidget(_text(spec["detail"]))
                self._build(spec.get("views") or [], content, here)
                layout.addWidget(group)
            elif kind in ("plainText", "markdown"):
                layout.addWidget(
                    _SafeMarkdown(spec.get("value", "")) if kind == "markdown" else _text(spec.get("value", ""))
                )
            else:
                label = (
                    spec.get("title")
                    or spec.get("placeholder")
                    or {
                        "directory": "Input folder",
                        "imageField": "Image",
                        "size": "Size",
                        "image": "Image",
                    }.get(kind, f"Field {answer_index}")
                )
                control = self._control(spec, here, label)
                control.setMinimumWidth(0)
                layout.addWidget(_row(label, control))

    def _numeric(self, spec, value, label):
        low, high = limits(spec)
        spin = QDoubleSpinBox()
        # Preserve valid starting precision rather than silently rounding defaults.
        precision = max(decimals(spec), *(-Decimal(str(x)).as_tuple().exponent for x in (value, low, high)))
        if precision > 16:
            raise ValueError("This slider exceeds the native control's 16-digit decimal precision.")
        spin.setDecimals(precision)
        spin.setRange(low, high)
        spin.setSingleStep(10 ** -decimals(spec))
        spin.setValue(value)
        spin.setAccessibleName(label)
        if (spec.get("valueType") or {}).get("scale"):
            spin.setPrefix("×")
        return spin

    def _slider(self, spec, path, label):
        low, high = limits(spec)
        value = self._get(path)
        spin = self._numeric(spec, value, label)
        track = QSlider(Qt.Orientation.Horizontal)
        track.setRange(0, 10000)
        track.setAccessibleName(label + " slider")
        percent = QLabel()
        percent.setObjectName("scriptsFieldValue")

        def update(value):
            track.blockSignals(True)
            track.setValue(round((value - low) / (high - low) * 10000))
            track.blockSignals(False)
            percent.setText(f"{value * 100:g}%" if (spec.get("valueType") or {}).get("percent") else "")
            self._put(path, value)

        spin.valueChanged.connect(update)
        track.valueChanged.connect(lambda tick: spin.setValue(low + tick * (high - low) / 10000))
        update(value)
        return _group(track, spin, percent)

    def _double_slider(self, spec, path, label):
        low, high = limits(spec)
        values = self._get(path)
        lower, upper = [
            self._numeric(spec, x, label + suffix) for x, suffix in zip(values, (" minimum", " maximum"), strict=True)
        ]
        track = _RangeControl(0, 10000, (0, 10000))
        track.setAccessibleName(label + "; Space selects minimum or maximum")

        def update():
            pair = [lower.value(), upper.value()]
            track.lower, track.upper = [round((x - low) / (high - low) * 10000) for x in pair]
            track._sync_accessible()
            track.update()
            self._put(path, pair)

        def set_track(a, b):
            lower.blockSignals(True)
            upper.blockSignals(True)
            lower.setValue(low + a * (high - low) / 10000)
            upper.setValue(low + b * (high - low) / 10000)
            lower.blockSignals(False)
            upper.blockSignals(False)
            update()

        lower.valueChanged.connect(update)
        upper.valueChanged.connect(update)
        track.on_change = set_track
        update()
        # Spins allow direct accessible entry; invalid crossing is rejected on Continue.
        return _group(track, lower, QLabel("–"), upper)

    def _control(self, spec, path, label):
        kind = spec["type"]
        value = self._get(path) if contributes(spec) else None
        if kind == "switch":
            widget = QCheckBox("On" if value else "Off")
            widget.setChecked(value)
            widget.toggled.connect(lambda on: (self._put(path, on), widget.setText("On" if on else "Off")))
        elif kind == "textField":
            if spec.get("multiline"):
                widget = QPlainTextEdit()
                widget.setPlainText(value)
                widget.setMinimumHeight(min(480, max(64, round(spec.get("height") or 94))))
                widget.textChanged.connect(lambda: self._put(path, widget.toPlainText()))
            else:
                widget = QLineEdit(value)
                widget.setMaxLength(MAX_TEXT)
                widget.textChanged.connect(lambda text: self._put(path, text))
            widget.setPlaceholderText(spec.get("placeholder") or "")
        elif kind in ("comboBox", "menu", "segmented"):
            if kind == "segmented":
                widget = QFrame()
                widget.setProperty("role", "segmented")
                grid = QGridLayout(widget)
                grid.setContentsMargins(0, 0, 0, 0)
                grid.setSpacing(0)
                buttons = QButtonGroup(widget)
                buttons.setExclusive(True)
                for index, option in enumerate(spec["options"]):
                    button = QPushButton()
                    button.setText(button.fontMetrics().elidedText(option, Qt.TextElideMode.ElideRight, 100))
                    button.setCheckable(True)
                    button.setChecked(index == value)
                    button.setToolTip(option)
                    button.setAccessibleName(label + " " + option)
                    button.setProperty(
                        "segmentEdge",
                        "first" if index == 0 else "last" if index == len(spec["options"]) - 1 else "middle",
                    )
                    button.setMinimumWidth(0)
                    buttons.addButton(button, index)
                    grid.addWidget(button, index // 3, index % 3)
                buttons.idClicked.connect(lambda index: self._put(path, index))
            else:
                widget = QComboBox()
                widget.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
                widget.setMinimumContentsLength(8)
                widget.addItems(spec["options"])
                widget.setEditable(kind == "comboBox")
                if kind == "comboBox":
                    widget.lineEdit().setMaxLength(MAX_TEXT)
                    widget.setCurrentText(value)
                    widget.currentTextChanged.connect(lambda text: self._put(path, text))
                else:
                    widget.setCurrentIndex(value)
                    widget.currentIndexChanged.connect(lambda index: self._put(path, index))
                widget.setToolTip(widget.currentText())
                widget.currentTextChanged.connect(widget.setToolTip)
        elif kind == "slider":
            widget = self._slider(spec, path, label)
        elif kind == "doubleSlider":
            widget = self._double_slider(spec, path, label)
        elif kind == "size":
            spins = []
            for index, dimension in enumerate(value):
                spin = QSpinBox()
                spin.setRange(spec.get("minValue", 64), spec.get("maxValue", 8192))
                spin.setSingleStep(64)
                spin.setValue(dimension)
                spin.setAccessibleName(label + (" width" if index == 0 else " height"))
                spins.append(spin)
            for spin in spins:
                spin.valueChanged.connect(lambda: self._put(path, [s.value() for s in spins]))
            widget = _group(spins[0], QLabel("×"), spins[1])
        elif kind in ("imageField", "directory"):
            widget = self._picker_control(spec, path, label)
        elif kind == "image":
            widget = QWidget()
            grid = QGridLayout(widget)
            grid.setContentsMargins(0, 0, 0, 0)
            group = QButtonGroup(widget)
            group.setExclusive(True)
            for index, source in enumerate(image_sources(spec)):
                pixmap = QPixmap()
                pixmap.loadFromData(self._thumbnails[source])
                if spec.get("selectable"):
                    tile = QPushButton(str(index + 1))
                    tile.setIcon(QIcon(pixmap))
                    tile.setIconSize(QSize(72, 72))
                    tile.setCheckable(True)
                    tile.setChecked(index == value)
                    group.addButton(tile, index)
                else:
                    tile = QLabel()
                    tile.setPixmap(
                        pixmap.scaledToHeight(
                            min(240, round(spec.get("height") or 96)), Qt.TransformationMode.SmoothTransformation
                        )
                    )
                tile.setAccessibleName(label + f" image {index + 1}")
                grid.addWidget(tile, index // 3, index % 3)
            group.idClicked.connect(lambda index: self._put(path, index))
        else:
            raise ValueError("Unsupported form control.")
        widget.setAccessibleName(label)
        return widget

    def _picker_control(self, spec, path, label):
        text = QLineEdit()
        text.setObjectName("scriptsPathValue")
        text.setReadOnly(True)
        text.setPlaceholderText("No folder selected" if spec["type"] == "directory" else "No image chosen")
        text.setAccessibleName(label + " selection")
        choose, clear = QPushButton("Choose…"), QPushButton("Clear")
        choose.setAccessibleName("Choose " + label)
        clear.setAccessibleName("Clear " + label)
        clear.clicked.connect(lambda: (self._put(path, "" if spec["type"] == "directory" else []), text.clear()))
        choose.clicked.connect(lambda: self._choose(spec, path, label, text, choose, clear))
        box = _group(text, choose, clear)
        if spec["type"] == "directory":
            box.setToolTip(
                "Imports a bounded, nonrecursive copy of this folder's images; JS cannot access the original folder."
            )
        return box

    def _choose(self, spec, path, label, text, choose, clear):
        if self.busy or self.identity is None or self.runner.closed:
            return
        identity, generation = self.identity, self._generation
        picker = QFileDialog(self.window(), "Choose " + label)
        directory = spec["type"] == "directory"
        picker.setFileMode(
            QFileDialog.FileMode.Directory
            if directory
            else QFileDialog.FileMode.ExistingFiles
            if spec.get("multiSelect")
            else QFileDialog.FileMode.ExistingFile
        )
        if directory:
            picker.setOption(QFileDialog.Option.ShowDirsOnly, True)
        else:
            picker.setNameFilter("Images (*.png *.jpg *.jpeg *.webp)")
        self._pickers.append(picker)
        self._notify_busy()

        def finished(_):
            if picker in self._pickers:
                self._pickers.remove(picker)
            picker.deleteLater()
            self._notify_busy()

        def selected(paths):
            if self.identity != identity or self._generation != generation or self.runner.closed:
                return
            from dtflow.services.script_form_inputs import snapshot_picked_inputs

            self._pending += 1
            choose.setEnabled(False)
            clear.setEnabled(False)
            text.setText("Preparing owned copy…")
            self._notify_busy()

            def done(result, failure):
                if self.identity != identity or self._generation != generation:
                    return
                self._pending -= 1
                choose.setEnabled(True)
                clear.setEnabled(True)
                if failure is not None:
                    self._put(path, "" if directory else [])
                    text.clear()
                    self.problem.emit(str(failure))
                else:
                    self._put(path, result[0] if directory else result)
                    text.setText("Owned folder copy" if directory else f"{len(result)} image(s) selected")
                self._notify_busy()

            self.runner.submit(
                asyncio.to_thread(snapshot_picked_inputs, self.state, *identity, path, list(paths)), done
            )

        picker.filesSelected.connect(selected)
        picker.finished.connect(finished)
        picker.open()
