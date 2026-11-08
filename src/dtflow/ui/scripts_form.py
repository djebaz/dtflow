"""Native, data-driven Draw Things script form controls (UI only).

Controls are constructed from typed descriptors. No JavaScript is evaluated here;
preview values never become provider requests or mutate a saved ScriptRun.
"""

from __future__ import annotations

import html
import re

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from dtflow.ui.file_dialogs import QFileDialog
from dtflow.ui.theme import load_tokens
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


class _FieldRow(QWidget):
    """Reserve the wrapped caption and themed control heights before scrolling."""

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        layout = self.layout()
        return max(layout.minimumSize().height(), layout.totalHeightForWidth(width))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        height = self.heightForWidth(self.width())
        if height > 0 and self.minimumHeight() != height:
            self.setMinimumHeight(height)
            self.updateGeometry()


def _row(label: str, control: QWidget) -> QWidget:
    row = _FieldRow()
    row.setObjectName("scriptsFieldRow")
    policy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
    policy.setHeightForWidth(True)
    row.setSizePolicy(policy)
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 3, 0, 3)
    layout.setSpacing(10)
    heading = QLabel(label, row)
    heading.setTextFormat(Qt.TextFormat.PlainText)
    heading.setObjectName("scriptFieldLabel")
    heading.setToolTip(label)
    heading.setWordWrap(True)
    heading.setMinimumWidth(190)
    heading.setMaximumWidth(190)
    layout.addWidget(heading, 0)
    layout.addWidget(control, 1)
    if control.maximumWidth() < 16777215:
        # Compact segmented/size controls stay left-aligned alongside the
        # full-width fields instead of centering across the flexible row.
        layout.addStretch(1)
    control.setAccessibleName(label)
    control.ensurePolished()
    control.setMinimumHeight(
        max(control.minimumHeight(), control.minimumSizeHint().height(), control.sizeHint().height())
    )
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
        value.setText(
            f"{round(actual * 100)}%" if sub == "percent" else f"×{actual:.1f}" if sub == "scale" else f"{actual:.1f}"
        )

    slider.valueChanged.connect(update)
    update(slider.value())
    box = _group(slider, value)
    box.layout().setStretch(0, 1)
    value.setFixedWidth(45)
    return box


class _RangeControl(QWidget):
    """One native-painted track with independently adjustable minimum/maximum thumbs."""

    def __init__(self, low: int, high: int, values: tuple[int, int]):
        super().__init__()
        self.low = low
        self.high = high
        self.lower, self.upper = values
        self.active_thumb = 0
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
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
        return max(
            self.low, min(self.high, round(self.low + (x - 10) * (self.high - self.low) / max(1, self.width() - 20)))
        )

    def paintEvent(self, event):
        colors = load_tokens()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        y = self.height() // 2
        painter.setPen(QPen(QColor(colors["color.groove"]), 4))
        painter.drawLine(10, y, self.width() - 10, y)
        painter.setPen(QPen(QColor(colors["color.selectionBorder"]), 4))
        painter.drawLine(self._x(self.lower), y, self._x(self.upper), y)
        painter.setPen(QPen(QColor(colors["color.handleBorder"]), 1))
        painter.setBrush(QColor(colors["color.handle"]))
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
        elif event.key() == Qt.Key.Key_Space:
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
    box = _group(track, value)
    box.layout().setStretch(0, 1)
    value.setFixedWidth(45)
    return box


def _with_image_multiple(control: QWidget, multiple: int = 1) -> QWidget:
    """Shared preview/live disclosure for a script-authored crop rule."""
    if multiple == 1:
        return control
    box = QWidget()
    layout = QVBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(4)
    layout.addWidget(control)
    note = QLabel(f"Centre-crop the imported copy to multiples of {multiple} px; no resizing. Original file unchanged.")
    note.setObjectName("scriptsFormSupporting")
    note.setTextFormat(Qt.TextFormat.PlainText)
    note.setWordWrap(True)
    layout.addWidget(note)
    return box


def _path_control(spec: dict) -> QWidget:
    path = QLineEdit(str(spec.get("value") or ""))
    path.setObjectName("scriptsPathValue")
    path.setReadOnly(True)
    path.setPlaceholderText("No image chosen" if spec["type"] == "imageField" else "No folder selected")
    path.setToolTip(path.text())
    path.textChanged.connect(path.setToolTip)
    path.setCursorPosition(len(path.text()))  # Keep the folder name visible for long paths.
    path.textChanged.connect(lambda text: path.setCursorPosition(len(text)))
    choose = QPushButton("Choose…")
    choose.setAccessibleName("Choose " + spec["label"])

    def choose_file():
        if spec["type"] == "directory":
            selected = QFileDialog.getExistingDirectory(
                path, "Choose " + spec["label"], remember_as="script_input_folder"
            )
        else:
            selected, _ = QFileDialog.getOpenFileName(
                path,
                "Choose " + spec["label"],
                "",
                "Images (*.png *.jpg *.jpeg *.webp);;All files (*)",
                remember_as="script_input_file",
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
    return _with_image_multiple(_group(*controls), spec.get("multiple", 1))


def _control(spec: dict) -> QWidget:
    kind = spec["type"]
    value = spec.get("value")
    if kind == "textField":
        widget = QLineEdit(str(value or ""))
    elif kind == "spinBox":
        widget = QSpinBox()
        widget.setRange(spec.get("min", 0), spec.get("max", 100))
        widget.setValue(value)
    elif kind in ("comboBox", "menu"):
        widget = QComboBox()
        widget.addItems(spec["options"])
        widget.setCurrentText(str(value))
    elif kind == "segmented":
        bar = QFrame()
        bar.setProperty("role", "segmented")
        joined = QHBoxLayout(bar)
        joined.setContentsMargins(0, 0, 0, 0)
        joined.setSpacing(0)
        for option in spec["options"]:
            joined.addWidget(QPushButton(option))
        buttons = bar.findChildren(QPushButton)
        for index, button in enumerate(buttons):
            button.setProperty(
                "segmentEdge", "first" if index == 0 else "last" if index == len(buttons) - 1 else "middle"
            )
            button.setCheckable(True)
            button.setChecked(button.text() == value)
            button.setAccessibleName(spec["label"] + " " + button.text())
            button.clicked.connect(
                lambda checked=False, current=button: [other.setChecked(other is current) for other in buttons]
            )
        bar.ensurePolished()
        bar.setMaximumWidth(bar.sizeHint().width())
        bar.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
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
        separator = QLabel("×")
        separator.setFixedWidth(16)
        widget = _group(width, separator, height)
        widget.layout().setStretch(0, 1)
        widget.layout().setStretch(2, 1)
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
        self._narrow: bool | None = None
        self.layout_ = QVBoxLayout(self)
        self.layout_.setContentsMargins(0, 0, 0, 0)
        self.layout_.setSpacing(8)
        self.layout_.setSizeConstraint(QVBoxLayout.SizeConstraint.SetMinimumSize)
        self.layout_.addStretch(1)

    def set_narrow(self, narrow: bool) -> None:
        """Stack field captions above inputs only when the width mode changes.

        Avoid invalidating layouts on every shell resize/event-filter callback.
        A rebuilt set of fields clears the cached width mode.
        """
        from PySide6.QtWidgets import QBoxLayout

        if self._narrow is narrow:
            return
        self._narrow = narrow
        direction = QBoxLayout.Direction.TopToBottom if narrow else QBoxLayout.Direction.LeftToRight
        label_max = 16777215 if narrow else 190
        for row in self.findChildren(QWidget, "scriptsFieldRow"):
            layout = row.layout()
            if isinstance(layout, QBoxLayout) and layout.direction() != direction:
                layout.setDirection(direction)
            heading = row.findChild(QLabel, "scriptFieldLabel")
            if heading is not None:
                heading.setMinimumWidth(0 if narrow else 190)
                if heading.maximumWidth() != label_max:
                    heading.setMaximumWidth(label_max)
        self.layout_.activate()
        self.updateGeometry()

    def set_fields(self, fields: list[dict], *, long_values: bool = False) -> None:
        self._narrow = None
        while self.layout_.count():
            item = self.layout_.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # deleteLater() alone leaves visible orphans until Qt handles
                # DeferredDelete. Hide and detach before rebuilding a live page.
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        for spec in fields:
            kind = spec["type"]
            if kind == "section":
                heading = QLabel(spec["label"])
                heading.setObjectName("scriptsFormSection")
                self.layout_.addWidget(heading)
            elif kind in ("plainText", "markdown"):
                body = QLabel()
                body.setObjectName("scriptsFormSupporting")
                body.setWordWrap(True)
                if kind == "markdown":
                    # Only bold and code are allowed. Escape script text before
                    # adding these two formatting elements; never render raw HTML.
                    value = html.escape(spec["text"])
                    value = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", value)
                    value = re.sub(r"`([^`]+)`", r"<code>\1</code>", value)
                    body.setTextFormat(Qt.TextFormat.RichText)
                    body.setText(value)
                else:
                    body.setText(spec["text"])
                self.layout_.addWidget(body)
            else:
                self.layout_.addWidget(_row(spec["label"], _control(spec)))
        self.layout_.addStretch(1)
        # The scroll area must honor the form content height rather than
        # compressing rows until controls overlap each other.
        self.layout_.activate()
        self.updateGeometry()


# All 14 upstream types are represented: section, plainText, markdown,
# textField, comboBox, menu, segmented, switch, slider, doubleSlider,
# size, imageField, directory and image.
REFERENCE_FORM: list[dict] = [
    {"type": "section", "label": "Prompt"},
    {
        "type": "markdown",
        "text": "Sweeps **wildcards** through the prompt, one generation per value. Use `{a|b}` to group.",
    },
    {"type": "textField", "label": "Wildcards", "value": "red, green, blue, amber"},
    {
        "type": "comboBox",
        "label": "Style",
        "options": ["Photographic", "Illustration", "Line art"],
        "value": "Photographic",
    },
    {"type": "segmented", "label": "Aspect", "options": ["1:1", "3:2", "16:9"], "value": "3:2"},
    {"type": "menu", "label": "Sweep order", "options": ["Sequential", "Shuffled", "Random"], "value": "Sequential"},
    {"type": "section", "label": "Sampling"},
    {"type": "slider", "label": "Strength", "sub": "percent", "min": 0, "max": 1, "step": 0.01, "value": 0.75},
    {"type": "slider", "label": "Guidance", "sub": "fractional", "min": 1, "max": 20, "step": 0.1, "value": 5.5},
    {"type": "slider", "label": "Upscale", "sub": "scale", "min": 1, "max": 4, "step": 0.25, "value": 2},
    {"type": "doubleSlider", "label": "Step range", "min": 1, "max": 40, "value": (8, 28)},
    {"type": "size", "label": "Size", "value": (512, 512)},
    {"type": "switch", "label": "Tile results into a sheet", "value": False},
    {"type": "section", "label": "Files"},
    {"type": "imageField", "label": "Reference image", "value": None},
    {"type": "directory", "label": "Export folder", "value": "C:\\Users\\me\\Pictures\\sweeps"},
    {"type": "image", "label": "Style swatch", "caption": "Shipped with the script"},
    {"type": "plainText", "text": "Results are saved to the gallery. The export folder is optional."},
]
