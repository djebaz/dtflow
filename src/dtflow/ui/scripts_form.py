"""Native, data-driven Draw Things script form controls (UI only).

Controls are constructed from typed descriptors. No JavaScript is evaluated here;
preview values never become provider requests or mutate a saved ScriptRun.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QPainter, QPen
from dtflow.ui.theme import load_tokens
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFrame, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QScrollArea, QSlider, QSpinBox,
    QVBoxLayout, QWidget,
)


def _row(label: str, control: QWidget) -> QWidget:
    row = QWidget()
    layout = QHBoxLayout(row)
    row.setObjectName("scriptsFieldRow")
    layout.setContentsMargins(0, 3, 0, 3)
    layout.setSpacing(10)
    heading = QLabel(label, row)
    heading.setObjectName("scriptFieldLabel")
    heading.setToolTip(label)
    heading.setWordWrap(True)
    heading.setMinimumWidth(115)
    heading.setMaximumWidth(190)
    layout.addWidget(heading, 0)
    layout.addWidget(control, 1)
    control.setAccessibleName(label)
    return row


def _group(*widgets: QWidget) -> QWidget:
    box = QWidget()
    layout = QHBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)
    for widget in widgets:
        layout.addWidget(widget)
    return box


def _slider(spec: dict) -> QWidget:
    low, high, step = spec["min"], spec["max"], spec["step"]
    scale = round(1 / step)
    slider = QSlider(Qt.Orientation.Horizontal)
    slider.setRange(round(low * scale), round(high * scale))
    slider.setValue(round(spec["value"] * scale))
    value = QLabel()
    value.setObjectName("scriptsFieldValue")
    value.setMinimumWidth(45)
    sub = spec.get("sub")
    def update(number: int) -> None:
        actual = number / scale
        value.setText(f"{round(actual * 100)}%" if sub == "percent"
                      else f"×{actual:g}" if sub == "scale" else f"{actual:.1f}")
    slider.valueChanged.connect(update)
    update(slider.value())
    return _group(slider, value)


class _RangeControl(QWidget):
    """One native-painted track with independently adjustable minimum/maximum thumbs."""

    def __init__(self, low: int, high: int, values: tuple[int, int]):
        super().__init__()
        self.low = low
        self.high = high
        self.lower, self.upper = values
        self.active_thumb = 0
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Step range minimum and maximum")
        self._sync_accessible()

    def _sync_accessible(self):
        self.setAccessibleDescription(f"Minimum {self.lower}, maximum {self.upper}")

    def sizeHint(self):
        return QSize(220, 26)

    def _x(self, value: int) -> int:
        return round(10 + (self.width() - 20) * (value - self.low) / (self.high - self.low))

    def _value(self, x: int) -> int:
        return max(self.low, min(self.high, round(
            self.low + (x - 10) * (self.high - self.low) / max(1, self.width() - 20)
        )))

    def paintEvent(self, event):
        colors = load_tokens()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        y = self.height() // 2
        painter.setPen(QPen(colors["color.groove"], 4))
        painter.drawLine(10, y, self.width() - 10, y)
        painter.setPen(QPen(colors["color.accent"], 4))
        painter.drawLine(self._x(self.lower), y, self._x(self.upper), y)
        painter.setPen(QPen(colors["color.handleBorder"], 1))
        painter.setBrush(colors["color.handle"])
        painter.drawEllipse(self._x(self.lower) - 7, y - 7, 14, 14)
        painter.drawEllipse(self._x(self.upper) - 7, y - 7, 14, 14)

    def mousePressEvent(self, event):
        x = event.position().x()
        self.active_thumb = 0 if abs(x - self._x(self.lower)) <= abs(x - self._x(self.upper)) else 1
        self.mouseMoveEvent(event)
        self.setFocus()

    def mouseMoveEvent(self, event):
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        value = self._value(round(event.position().x()))
        if self.active_thumb == 0:
            self.lower = min(value, self.upper)
        else:
            self.upper = max(self.lower, value)
        self._sync_accessible()
        self.update()
        if self.on_change:
            self.on_change(self.lower, self.upper)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            delta = 1 if event.key() == Qt.Key.Key_Right else -1
            if self.active_thumb == 0:
                self.lower = max(self.low, min(self.upper, self.lower + delta))
            else:
                self.upper = min(self.high, max(self.lower, self.upper + delta))
            self._sync_accessible()
            self.update()
            if self.on_change:
                self.on_change(self.lower, self.upper)
        elif event.key() == Qt.Key.Key_Tab:
            self.active_thumb = 1 - self.active_thumb
            self.update()
        else:
            super().keyPressEvent(event)

    on_change = None


def _range_slider(spec: dict) -> QWidget:
    track = _RangeControl(spec["min"], spec["max"], tuple(spec["value"]))
    value = QLabel()
    value.setObjectName("scriptsFieldValue")
    value.setMinimumWidth(45)
    def update(lower, upper):
        value.setText(f"{lower}–{upper}")
    track.on_change = update
    update(track.lower, track.upper)
    return _group(track, value)


def _path_control(spec: dict) -> QWidget:
    path = QLineEdit(str(spec.get("value") or ""))
    path.setObjectName("scriptsPathValue")
    path.setReadOnly(True)
    path.setPlaceholderText("No image chosen" if spec["type"] == "imageField" else "No folder selected")
    path.setToolTip(path.text())
    path.textChanged.connect(path.setToolTip)
    choose = QPushButton("Choose…")
    choose.setAccessibleName("Choose " + spec["label"])
    def choose_file():
        if spec["type"] == "directory":
            selected = QFileDialog.getExistingDirectory(path, "Choose " + spec["label"])
        else:
            selected, _ = QFileDialog.getOpenFileName(
                path, "Choose " + spec["label"], "", "Images (*.png *.jpg *.jpeg *.webp);;All files (*)"
            )
        if selected:
            path.setText(selected)
    choose.clicked.connect(choose_file)
    controls = [path, choose]
    if spec["type"] == "imageField":
        clear = QPushButton("Clear")
        clear.setAccessibleName("Clear " + spec["label"])
        clear.clicked.connect(path.clear)
        controls.append(clear)
    return _group(*controls)


def _control(spec: dict) -> QWidget:
    kind = spec["type"]
    value = spec.get("value")
    if kind == "textField":
        widget = QLineEdit(str(value or ""))
    elif kind in ("comboBox", "menu"):
        widget = QComboBox()
        widget.addItems(spec["options"])
        widget.setCurrentText(str(value))
    elif kind == "segmented":
        bar = _group(*[QPushButton(option) for option in spec["options"]])
        buttons = bar.findChildren(QPushButton)
        for button in buttons:
            button.setCheckable(True)
            button.setChecked(button.text() == value)
            button.setAccessibleName(spec["label"] + " " + button.text())
            button.clicked.connect(
                lambda checked=False, current=button: [
                    other.setChecked(other is current) for other in buttons
                ]
            )
        widget = bar
    elif kind == "switch":
        widget = QCheckBox("On" if value else "Off")
        widget.setChecked(bool(value))
        widget.toggled.connect(lambda on: widget.setText("On" if on else "Off"))
    elif kind == "slider":
        widget = _slider(spec)
    elif kind == "doubleSlider":
        widget = _range_slider(spec)
    elif kind == "size":
        width = QSpinBox()
        height = QSpinBox()
        for field, amount, suffix in ((width, value[0], " width"), (height, value[1], " height")):
            field.setRange(64, 8192)
            field.setSingleStep(64)
            field.setValue(amount)
            field.setAccessibleName(spec["label"] + suffix)
        widget = _group(width, QLabel("×"), height)
    elif kind in ("imageField", "directory"):
        widget = _path_control(spec)
    elif kind == "image":
        image = QLabel("image")
        image.setObjectName("scriptsImageTile")
        image.setFixedSize(56, 56)
        image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        image.setAccessibleName(spec["label"])
        widget = _group(image, QLabel(spec.get("caption", "")))
    else:
        raise ValueError(f"Unsupported script form component: {kind}")
    return widget


class ScriptForm(QWidget):
    """Render a non-executable form definition using native, accessible Qt widgets."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("scriptsForm")
        self.layout_ = QVBoxLayout(self)
        self.layout_.setContentsMargins(10, 10, 10, 10)
        self.layout_.setSpacing(8)
        self.layout_.addStretch(1)

    def set_narrow(self, narrow: bool) -> None:
        """Stack field captions above inputs at 800 px and similar widths."""
        from PySide6.QtWidgets import QBoxLayout

        for row in self.findChildren(QWidget, "scriptsFieldRow"):
            layout = row.layout()
            if layout is not None:
                layout.setDirection(
                    QBoxLayout.Direction.TopToBottom if narrow
                    else QBoxLayout.Direction.LeftToRight
                )
            heading = row.findChild(QLabel, "scriptFieldLabel")
            if heading is not None:
                heading.setMaximumWidth(16777215 if narrow else 190)

    def set_fields(self, fields: list[dict], *, long_values: bool = False) -> None:
        while self.layout_.count():
            item = self.layout_.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        for spec in fields:
            kind = spec["type"]
            if kind == "section":
                heading = QLabel(spec["label"])
                heading.setObjectName("scriptsFormSection")
                self.layout_.addWidget(heading)
            elif kind in ("plainText", "markdown"):
                # UI-only subset intentionally renders formatting as text, not HTML.
                body = QLabel(spec["text"].replace("**", "").replace("`", ""))
                body.setObjectName("scriptsFormSupporting")
                body.setWordWrap(True)
                self.layout_.addWidget(body)
            else:
                self.layout_.addWidget(_row(spec["label"], _control(spec)))
        self.layout_.addStretch(1)


# All 14 upstream types are represented: section, plainText, markdown,
# textField, comboBox, menu, segmented, switch, slider, doubleSlider,
# size, imageField, directory and image.
REFERENCE_FORM: list[dict] = [
    {"type": "section", "label": "Prompt"},
    {"type": "markdown", "text": "Sweeps **wildcards** through the prompt, one generation per value. Use `{a|b}` to group."},
    {"type": "textField", "label": "Wildcards", "value": "red, green, blue, amber"},
    {"type": "comboBox", "label": "Style", "options": ["Photographic", "Illustration", "Line art"], "value": "Photographic"},
    {"type": "segmented", "label": "Aspect", "options": ["1:1", "3:2", "16:9"], "value": "3:2"},
    {"type": "menu", "label": "Sweep order", "options": ["Sequential", "Shuffled", "Random"], "value": "Sequential"},
    {"type": "section", "label": "Sampling"},
    {"type": "slider", "label": "Strength", "sub": "percent", "min": 0, "max": 1, "step": 0.01, "value": .75},
    {"type": "slider", "label": "Guidance", "sub": "fractional", "min": 1, "max": 20, "step": .1, "value": 5.5},
    {"type": "slider", "label": "Upscale", "sub": "scale", "min": 1, "max": 4, "step": .25, "value": 2},
    {"type": "doubleSlider", "label": "Step range", "min": 1, "max": 40, "value": (8, 28)},
    {"type": "size", "label": "Size", "value": (512, 512)},
    {"type": "switch", "label": "Tile results into a sheet", "value": False},
    {"type": "section", "label": "Files"},
    {"type": "imageField", "label": "Reference image", "value": None},
    {"type": "directory", "label": "Export folder", "value": "C:\\Users\\me\\Pictures\\sweeps"},
    {"type": "image", "label": "Style swatch", "caption": "Shipped with the script"},
    {"type": "plainText", "text": "Results are saved to the gallery. The export folder is optional."},
]
