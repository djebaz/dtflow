"""Select-based value lists for the Queues composer: chips in, the planner's ``;``-separated text out.

``ValueChips`` is a Designer-promoted presentation widget. It owns no value rules: it hands the Plan form the same text a
user could have typed (``text()``), so ``planning_presenter`` stays the single parser and the planner validates bounds,
duplicates and ordering exactly as for CLI plan files.
"""

from collections.abc import Callable
from math import ceil

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QTextDocument, QTextOption
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QLayout,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QWidget,
    QWidgetItem,
)

from dtflow.ui.search_select import choose_searchable

# Offered size choices besides the Generate size; every value is a multiple of 64, and custom WIDTHxHEIGHT is allowed.
SIZE_PRESETS = (
    "512x512",
    "768x768",
    "1024x1024",
    "768x1024",
    "1024x768",
    "832x1216",
    "1216x832",
    "768x1344",
    "1344x768",
)


def _noun(title: str) -> str:
    """``Model`` -> ``model`` for running text, keeping acronyms such as ``LoRA set`` as written."""
    return title[:1].lower() + title[1:] if title[1:2].islower() else title


class FlowLayout(QLayout):
    """Left-to-right items that wrap to the next line, so a long value list never widens the composer."""

    def __init__(self, parent=None, spacing: int = 6):
        super().__init__(parent)
        self._items = []
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(spacing)

    def addItem(self, item):
        self._items.append(item)

    def insertWidget(self, index: int, widget: QWidget) -> None:
        self.addChildWidget(widget)
        self._items.insert(index, QWidgetItem(widget))
        self.invalidate()

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._arrange(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._arrange(rect, apply=True)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    def _arrange(self, rect: QRect, *, apply: bool) -> int:
        margins = self.contentsMargins()
        area = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        x, y, line = area.x(), area.y(), 0
        for item in self._items:
            if item.widget() is not None and item.widget().isHidden():
                continue
            hint = item.sizeHint()
            if x + hint.width() > area.right() + 1 and line > 0:
                x, y, line = area.x(), y + line + self.spacing(), 0
            if apply:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self.spacing()
            line = max(line, hint.height())
        return y + line - rect.y() + margins.bottom()


class ValueChips(QWidget):
    """One plan dimension as removable value chips plus an adder (searchable list, choices or a number box)."""

    textChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._values: list[str] = []
        self._kind = "search"
        self._title = ""
        self._options: Callable[[], list[str]] = list
        self._custom: str | None = None
        self._weights: Callable[[], dict[str, float | None]] | None = None
        self._convert: Callable[[object], str] | None = None
        self._layout = FlowLayout(self)
        self._chips: list[QPushButton] = []
        self._input: QWidget | None = None
        self._add = QPushButton("Add…", self)
        self._add.setObjectName("valueChipsAddButton")
        self._add.clicked.connect(self._add_clicked)
        self._layout.addWidget(self._add)
        policy = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._layout.heightForWidth(width)

    def sizeHint(self) -> QSize:
        width = max(self.width(), 240)
        return QSize(width, self._layout.heightForWidth(width))

    # -- configuration ---------------------------------------------------------------------------------------------
    def configure(
        self,
        kind: str,
        title: str,
        *,
        options: Callable[[], list[str]] = list,
        custom: str | None = None,
        minimum: float = 0,
        maximum: float = 100,
        step: float = 1,
        decimals: int = 1,
        weights: Callable[[], dict[str, float | None]] | None = None,
        convert: Callable[[object], str] | None = None,
    ) -> None:
        """``search`` opens the shared searchable editor; ``choice`` is an editable list; ``int``/``float`` a box.

        With ``weights`` the search editor also asks for a weight (the shared LoRA identifier+weight editor) and hands
        ``(identifier, weight)`` to ``convert``, which returns the chip value.
        """
        self._kind, self._title, self._options, self._custom = kind, title, options, custom
        self._weights, self._convert = weights, convert
        if self._input is not None:
            self._layout.removeWidget(self._input)
            self._input.deleteLater()
            self._input = None
        if kind == "choice":
            combo = QComboBox(self)
            combo.setEditable(True)
            combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            combo.lineEdit().returnPressed.connect(self._add_clicked)
            self._input = combo
        elif kind in ("int", "float"):
            spin = QSpinBox(self) if kind == "int" else QDoubleSpinBox(self)
            if kind == "float":
                spin.setDecimals(decimals)
            spin.setRange(minimum, maximum)
            spin.setSingleStep(step)
            self._input = spin
        if self._input is not None:
            self._input.setAccessibleName(f"{title} value to add")
            self._input.setObjectName("valueChipsInput")
            self._layout.removeWidget(self._add)
            self._layout.addWidget(self._input)
            self._layout.addWidget(self._add)
        self._add.setEnabled(True)  # preview forms start with every control disabled until a controller wires it
        self._add.setText("Add" if self._input is not None else "Add…")
        self._add.setAccessibleName(f"Add {_noun(title)}")
        self._add.setToolTip(f"Add a {_noun(title)} value; click a value to remove it.")
        self.refresh_options()

    def refresh_options(self, current: str | None = None) -> None:
        """Reload offered choices (inventory or presets); ``current`` becomes the box value for number inputs."""
        if isinstance(self._input, QComboBox):
            text = self._input.currentText()
            self._input.blockSignals(True)
            self._input.clear()
            self._input.addItems(list(dict.fromkeys(self._options())))
            self._input.setCurrentText(text or self._input.itemText(0))
            self._input.blockSignals(False)
        elif current is not None and isinstance(self._input, (QSpinBox, QDoubleSpinBox)):
            try:
                self._input.setValue(type(self._input.value())(current))
            except ValueError:
                pass

    # -- values ----------------------------------------------------------------------------------------------------
    def values(self) -> tuple[str, ...]:
        return tuple(self._values)

    def text(self) -> str:
        return "; ".join(self._values)

    def setText(self, text: str) -> None:
        self._set([token.strip() for token in text.replace("\n", ";").split(";") if token.strip()])

    def clear(self) -> None:
        self._set([])

    def add_value(self, value: str) -> None:
        value = value.strip()
        if value:
            self._set([*self._values, value])

    def remove_value(self, index: int) -> None:
        self._set(self._values[:index] + self._values[index + 1 :])

    def _set(self, values: list[str]) -> None:
        if values == self._values:
            return
        self._values = values
        for chip in self._chips:
            self._layout.removeWidget(chip)
            chip.hide()
            chip.deleteLater()
        self._chips = []
        for index, value in enumerate(values):
            chip = QPushButton(f"{value}  ×", self)
            chip.setProperty("role", "valueChip")
            chip.setToolTip(f"Remove {value}")
            chip.setAccessibleName(f"Remove {_noun(self._title)} {value}")
            chip.clicked.connect(lambda _=False, i=index: self.remove_value(i))
            self._layout.insertWidget(index, chip)
            self._chips.append(chip)
        self.updateGeometry()
        self.textChanged.emit()

    def _add_clicked(self) -> None:
        if isinstance(self._input, QComboBox):
            self.add_value(self._input.currentText())
        elif isinstance(self._input, QSpinBox):
            self.add_value(str(self._input.value()))
        elif isinstance(self._input, QDoubleSpinBox):
            self.add_value(f"{self._input.value():g}")
        else:
            convert = self._convert
            choose_searchable(
                self,
                title=f"Add {_noun(self._title)}",
                items=self._options(),
                callback=self.add_value if convert is None else lambda chosen: self.add_value(convert(chosen)),
                custom_label=self._custom,
                custom_prompt=f"Exact {_noun(self._title)} identifier",
                weights=self._weights() if self._weights is not None else None,
            )


class ActionsFit(QObject):
    """Keep all composer actions, including Close, in one responsive action group."""

    def __init__(self, scroll, title_layout, column_layout, actions, fixed):
        super().__init__(scroll)
        self._title, self._actions, self._fixed = title_layout, tuple(actions), tuple(fixed)
        self._host = QWidget(scroll.widget())
        self._host.setObjectName("composerWrappedActions")
        self._flow = FlowLayout(self._host)
        self._host.hide()
        column_layout.insertWidget(1, self._host)
        self._wrapped = False
        self._viewport = scroll.viewport()
        self._viewport.installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Resize:
            self.refit()
        return False

    def refit(self) -> None:
        widgets = (*self._fixed, *self._actions)
        margins = self._title.contentsMargins()
        needed = sum(widget.sizeHint().width() for widget in widgets) + self._title.spacing() * len(widgets)
        # Reserve the card's inner gutter and prefer the separate row before labels get clipped.
        wrap = self._viewport.width() < max(680, needed + margins.left() + margins.right() + 24)
        if wrap == self._wrapped:
            return
        self._wrapped = wrap
        # Move Close with the other actions, so a narrow composer never gets an
        # almost-empty header row containing only Close.
        for widget in (*self._actions, *self._fixed):
            (self._title if wrap else self._flow).removeWidget(widget)
            if wrap:
                self._flow.addWidget(widget)
            else:
                self._title.addWidget(widget)
        self._host.setVisible(wrap)


class ComposerGridFit(QObject):
    """Reflow dense composer controls to the *visible* center width, never the window width.

    Pair labels with their spin boxes and move existing Qt widgets between grid cells;
    their values, signal connections and focus state are unchanged by a resize.
    """

    def __init__(self, scroll, draw_grid, draw_pairs, mutation_grid, mutation_groups):
        super().__init__(scroll)
        self._viewport = scroll.viewport()
        self._draw_grid = draw_grid
        self._draw_pairs = tuple(tuple(group) for group in draw_pairs)
        self._mutation_grid = mutation_grid
        self._mutation_groups = tuple(tuple(group) for group in mutation_groups)
        self._columns: tuple[int, int] | None = None
        self._viewport.installEventFilter(self)
        self.refit()

    def eventFilter(self, watched, event):
        if watched is self._viewport and event.type() == QEvent.Type.Resize:
            self.refit()
        return False

    @staticmethod
    def _arrange(grid, groups, columns):
        """Assign whole label/field groups to rows; never strand a field at the right edge."""
        for group in groups:
            for widget in group:
                grid.removeWidget(widget)
        for index, group in enumerate(groups):
            row, pair = divmod(index, columns)
            col = pair * 2
            if len(group) == 1:
                grid.addWidget(group[0], row, col, 1, 2)
            else:
                grid.addWidget(group[0], row, col)
                grid.addWidget(group[1], row, col + 1)
        grid.invalidate()
        grid.parentWidget().updateGeometry()

    def refit(self):
        """Keep three groups on wide canvases, two on medium, and one on narrow canvases."""
        width = self._viewport.width()
        # At about 500 px of visible center width the form has room for two
        # compact label/number pairs; one-per-row wastes too much vertical space.
        columns = (3, 3) if width >= 740 else (2, 2) if width >= 500 else (1, 1)
        if columns == self._columns:
            return
        self._columns = columns
        self._arrange(self._draw_grid, self._draw_pairs, columns[0])
        self._arrange(self._mutation_grid, self._mutation_groups, columns[1])


class SummaryLabelHeightFit(QObject):
    """Keep long wrapped inspector values fully visible instead of clipping their final lines.

    QFormLayout may underestimate a QLabel's height when its horizontal size
    policy ignores a long unbroken identifier. Measure the complete plain text
    at the *actual* label width; the right inspector itself remains scrollable.
    """

    def __init__(self, contents: QWidget, labels):
        super().__init__(contents)
        self._labels = tuple(labels)
        self._pending = False
        for widget in (contents, *self._labels):
            widget.installEventFilter(self)
        self.refit()

    @staticmethod
    def text_height(label) -> int:
        """Return the full rendered plain-text height at a label's current width."""
        width = label.contentsRect().width()
        if width <= 0:
            return 0
        document = QTextDocument()
        document.setDefaultFont(label.font())
        document.setDocumentMargin(0)
        option = document.defaultTextOption()
        option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        document.setDefaultTextOption(option)
        document.setPlainText(label.text())
        document.setTextWidth(width)
        return ceil(document.size().height())

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self.schedule()
        return False

    def schedule(self) -> None:
        """Wait for Qt to settle label widths before measuring wrapped height."""
        if self._pending:
            return
        self._pending = True
        QTimer.singleShot(0, self.refit)

    def refit(self) -> None:
        self._pending = False
        for label in self._labels:
            text_height = self.text_height(label)
            if not text_height:
                continue
            metrics = label.fontMetrics()
            multi_line = "\n" in label.text() or text_height > metrics.lineSpacing() + 1
            minimum = max(label.heightForWidth(label.width()), text_height + metrics.descent() + 2) if multi_line else 0
            if label.minimumHeight() != minimum:
                label.setMinimumHeight(minimum)
