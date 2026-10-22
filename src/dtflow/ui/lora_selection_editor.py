"""Edit the whole Generate LoRA selection in one inline editor: check, uncheck and weigh each LoRA."""

from collections.abc import Callable, Sequence
from pathlib import Path

from PySide6.QtCore import QEvent, QFile, QIODevice, QObject, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtUiTools import QUiLoader
from PySide6.QtWidgets import QPushButton, QSlider, QWidget

from dtflow.core.models import LoRASelection
from dtflow.ui.inline_feedback import feedback

WEIGHT_STEPS = 20  # slider ticks per 1.0 of weight: 0.05 per tick, per arrow key and per mouse-wheel notch
DEFAULT_WEIGHT = 1.0
CHIP_HEIGHT = 24
MIN_LIST_HEIGHT = 220
LIST_HEIGHT_SHARE = 0.55  # the list may take up to this share of the window height before it scrolls


def _load(name: str, parent: QWidget | None) -> QWidget:
    file = QFile(str(Path(__file__).with_name("forms") / name))
    if not file.open(QIODevice.OpenModeFlag.ReadOnly):
        raise OSError(f"{name} unavailable")
    try:
        form = QUiLoader().load(file, parent)
    finally:
        file.close()
    if form is None:
        raise OSError(f"{name} could not be loaded")
    return form


class _WheelStep(QObject):
    """One mouse-wheel notch moves a weight slider by exactly one tick (0.05), not Qt's three-line scroll step."""

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Wheel and isinstance(watched, QSlider):
            delta = event.angleDelta().y()
            if delta:
                watched.setValue(watched.value() + (1 if delta > 0 else -1))
            event.accept()
            return True
        return False


class _ClickToAdd(QObject):
    """Weights editor: a click (or a drag that is released) anywhere on a slider sets that weight and adds it."""

    HANDLE_MARGIN = 8  # px each end of the groove that the handle never reaches

    def __init__(self, parent: QObject, add: Callable[[], None]):
        super().__init__(parent)
        self._add = add
        self._down = False

    @classmethod
    def _value_at(cls, slider: QSlider, x: float) -> int:
        span = max(1, slider.width() - 2 * cls.HANDLE_MARGIN)
        share = min(1.0, max(0.0, (x - cls.HANDLE_MARGIN) / span))
        return slider.minimum() + round(share * (slider.maximum() - slider.minimum()))

    def eventFilter(self, watched, event):
        if not isinstance(watched, QSlider):
            return False
        kind = event.type()
        if kind == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            self._down = True
            watched.setValue(self._value_at(watched, event.position().x()))
            return True
        if kind == QEvent.Type.MouseMove and self._down:
            watched.setValue(self._value_at(watched, event.position().x()))
            return True
        if kind == QEvent.Type.MouseButtonRelease and self._down:
            self._down = False
            watched.setValue(self._value_at(watched, event.position().x()))
            self._add()
            return True
        return False


def format_weight(weight: float) -> str:
    """Shortest exact digits, at least one decimal (``1.0``, ``0.65``)."""
    text = f"{weight:g}"
    return text if "." in text or "e" in text else text + ".0"


def edit_lora_selection(
    parent: QWidget,
    *,
    candidates: Sequence[str],
    selected: Sequence[LoRASelection],
    callback: Callable[[list[tuple[str, float]]], None],
    suggestions: dict[str, float | None] | None = None,
    title: str = "Edit LoRAs",
    hint: str | None = None,
    multi_weight: bool = False,
) -> None:
    """Show every offered LoRA with a checkbox and a 0.0–3.0 weight slider; Apply replaces the selection once.

    Selected LoRAs come first, checked, at their weights. Unchecked LoRAs start at 1.0. A loaded weight outside the
    slider range is kept exactly until its slider is moved. ``callback`` receives the checked (identifier, weight)
    pairs in list order.

    With ``multi_weight`` each row also has a **+** that collects the slider's weight as a chip (click a chip to drop
    it; clicking or releasing a drag anywhere on the slider does the same); a checked LoRA then yields one pair per collected weight, or one at the slider's weight when none were added.
    """
    editor = _load("lora_selection_editor.ui", None)
    wheel_step = _WheelStep(editor)
    if hint:
        editor.loraSelectionHint.setText(hint)
    rows_layout = editor.loraSelectionRowsLayout
    empty = editor.loraSelectionEmpty
    suggestions = suggestions or {}
    weights: dict[str, float] = {}
    extra: dict[str, list[float]] = {}  # multi_weight: the weights collected per LoRA
    rows: dict[str, QWidget] = {}

    def update_count() -> None:
        count = sum(row.loraSelectionCheck.isChecked() for row in rows.values())
        editor.loraSelectionCount.setText(f"{count} selected")

    def add_row(name: str, *, checked: bool, weight: float, top: bool = False) -> QWidget:
        row = _load("lora_selection_row.ui", editor.loraSelectionRows)
        row.setProperty("identifier", name)
        check, slider, label = row.loraSelectionCheck, row.loraSelectionSlider, row.loraSelectionWeight
        check.setText(name)
        check.setChecked(checked)
        check.setAccessibleName(f"Use {name}")
        suggestion = suggestions.get(name)
        check.setToolTip(name if suggestion is None else f"{name}\nCatalogue suggests weight {suggestion:g}.")
        weights[name] = weight
        slider.setValue(max(slider.minimum(), min(slider.maximum(), round(weight * WEIGHT_STEPS))))
        slider.setAccessibleName(f"{name} weight")
        label.setText(format_weight(weight))

        def moved(value: int) -> None:
            weights[name] = round(value / WEIGHT_STEPS, 2)
            label.setText(format_weight(weights[name]))
            check.setChecked(True)  # weighing a LoRA means using it

        slider.valueChanged.connect(moved)
        slider.installEventFilter(wheel_step)
        if multi_weight:
            extra[name] = list(collected[name]) if len(collected.get(name, ())) > 1 else []
            row.loraSelectionAddWeightButton.setVisible(True)
            row.loraSelectionAddWeightButton.setAccessibleName(f"Add weight for {name}")

            def render_chips() -> None:
                host = row.loraSelectionWeightChipsHost
                chips = row.loraSelectionWeightChips
                for index in reversed(range(chips.count() - 1)):  # keep the trailing spacer
                    stale = chips.takeAt(index).widget()
                    if stale is not None:
                        stale.setParent(None)  # leave the row now, so it is not counted before deletion
                        stale.deleteLater()
                for index, value in enumerate(extra[name]):
                    chip = QPushButton(f"{format_weight(value)}  ×", host)
                    chip.setProperty("role", "valueChip")
                    chip.setMinimumHeight(CHIP_HEIGHT)  # the row would otherwise squeeze it to an empty bar
                    chip.setToolTip(f"Remove weight {format_weight(value)}")
                    chip.setAccessibleName(f"Remove weight {format_weight(value)} for {name}")
                    chip.clicked.connect(lambda _=False, i=index: drop(i))
                    chips.insertWidget(index, chip)
                host.setMinimumHeight(CHIP_HEIGHT + 2 if extra[name] else 0)
                host.setVisible(bool(extra[name]))

            def drop(index: int) -> None:
                del extra[name][index]
                if not extra[name]:
                    check.setChecked(False)  # dropping the last collected weight drops the LoRA
                render_chips()

            def add_weight() -> None:
                if weights[name] not in extra[name]:
                    extra[name].append(weights[name])
                check.setChecked(True)
                render_chips()

            row.loraSelectionAddWeightButton.clicked.connect(add_weight)
            slider.installEventFilter(_ClickToAdd(row, add_weight))
            render_chips()  # several loaded weights start as chips
        check.toggled.connect(lambda _checked: update_count())
        # Rows sit before the "no matches" label and the trailing spacer.
        rows_layout.insertWidget(0 if top else len(rows), row)
        rows[name] = row
        return row

    # A loaded request may repeat an identifier; the last weight wins, as when the selection is applied.
    current = {item.identifier: item.weight for item in selected}
    collected: dict[str, list[float]] = {}  # every loaded weight per LoRA, repeats kept (a Pair repeats weights)
    for item in selected:
        collected.setdefault(item.identifier, []).append(item.weight)
    for name, weight in current.items():
        add_row(name, checked=True, weight=weight)
    for name in dict.fromkeys(candidates):
        if name and name not in current:
            add_row(name, checked=False, weight=DEFAULT_WEIGHT)

    def filter_rows(query: str = "") -> None:
        needle = query.casefold().strip()
        shown = 0
        for name, row in rows.items():
            visible = not needle or needle in name.casefold()
            row.setVisible(visible)
            shown += visible
        empty.setText("No LoRA matches this search." if rows else "No LoRAs offered for this checkpoint.")
        empty.setVisible(not shown)

    def add_custom() -> None:
        field = editor.loraSelectionCustomIdentifier
        name = field.text().strip()
        if not name:
            field.setFocus()
            return
        row = rows.get(name) or add_row(name, checked=True, weight=DEFAULT_WEIGHT, top=True)
        row.loraSelectionCheck.setChecked(True)
        field.clear()
        editor.loraSelectionSearch.clear()
        filter_rows()
        update_count()
        editor.loraSelectionScroll.ensureWidgetVisible(row)

    panel = feedback(parent)

    def accept() -> None:
        if editor.loraSelectionCustomIdentifier.text().strip():
            add_custom()  # an identifier typed but not yet added is part of the selection
        # Display order: the existing selection keeps its order, newly checked LoRAs follow.
        chosen = [
            (name, value)
            for name, row in rows.items()
            if row.loraSelectionCheck.isChecked()
            for value in (extra.get(name) or [weights[name]])
        ]
        panel.dismiss()
        callback(chosen)

    def enter() -> None:
        if editor.loraSelectionCustomIdentifier.hasFocus() and editor.loraSelectionCustomIdentifier.text().strip():
            add_custom()
        else:
            accept()

    editor.loraSelectionSearch.textChanged.connect(filter_rows)
    editor.loraSelectionCustomAddButton.clicked.connect(add_custom)
    for key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
        shortcut = QShortcut(QKeySequence(key), editor)
        shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        shortcut.activated.connect(enter)
    filter_rows()
    update_count()
    window = parent.window()
    content = editor.loraSelectionRows.sizeHint().height() + 2 * editor.loraSelectionScroll.frameWidth()
    limit = max(MIN_LIST_HEIGHT, int(window.height() * LIST_HEIGHT_SHARE))
    editor.loraSelectionScroll.setMinimumHeight(min(content, limit))
    panel.editor(title, editor, "Apply", accept, primary=True)
    editor.loraSelectionSearch.setFocus()
