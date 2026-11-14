"""Edit the whole Generate LoRA selection in one inline editor: check, uncheck and weigh each LoRA."""

from collections.abc import Callable, Sequence
from pathlib import Path

from PySide6.QtCore import QEvent, QFile, QIODevice, QObject, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtUiTools import QUiLoader
from PySide6.QtWidgets import QLabel, QPushButton, QSizePolicy, QSlider, QWidget

from dtflow.core.models import LoRASelection
from dtflow.services.lora_naming import display_label
from dtflow.ui.inline_feedback import feedback
from dtflow.ui.lora_hints import LoRAHover

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


_display_label = display_label  # one shared format for every LoRA picker and summary


class _ToggleRow(QObject):
    """Make unused row space select a LoRA without stealing clicks from its controls."""

    def __init__(self, row: QWidget):
        super().__init__(row)
        self._row = row
        self._checkbox = row.loraSelectionCheck
        self._slider = row.loraSelectionSlider

    def _on_slider(self, event) -> bool:
        """A release that lands over the weight slider (groove or handle) belongs to the slider, never the row."""
        origin = self._slider.mapTo(self._row, self._slider.rect().topLeft())
        area = self._slider.rect().translated(origin).adjusted(-4, -4, 4, 4)
        return area.contains(event.position().toPoint())

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            if self._on_slider(event):
                return False
            self._checkbox.click()
            return True
        return False


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


#: The picker hint value for a LoRA the selected DT+ key is not known to hold (see ``LoraAssetPanel.storage_marks``).
NOT_ON_KEY = "needs re-attach"


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
    families: dict[str, str] | None = None,
    marks: dict[str, str] | None = None,
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
    families = families or {}
    marks = marks or {}  # DT+ storage hint per LoRA; display only, never filters or blocks
    names = list(dict.fromkeys([*candidates, *(item.identifier for item in selected)]))
    taken = [_display_label(name) for name in names]

    def shown(name: str) -> str:
        """The row's name: ShortName[Tag], with the filename added where two files would read the same."""
        label = _display_label(name)
        return f"{label} ({name})" if taken.count(label) > 1 else label

    weights: dict[str, float] = {}
    extra: dict[str, list[float]] = {}  # multi_weight: the weights collected per LoRA
    rows: dict[str, QWidget] = {}

    def reorder_rows() -> None:
        """Selected first and A–Z within both groups; no execution-order mutation."""
        ordered = sorted(
            rows.items(),
            key=lambda item: (
                not item[1].loraSelectionCheck.isChecked(),
                _display_label(item[0]).casefold(),
                item[0].casefold(),
            ),
        )
        for index, (_identifier, row) in enumerate(ordered):
            rows_layout.insertWidget(index, row)

    current_order = list(dict.fromkeys(item.identifier for item in selected))

    def effective_names() -> list[str]:
        """Existing request order first; new selections follow candidate order."""
        return [
            name
            for name in (*current_order, *(name for name in rows if name not in current))
            if name in rows and rows[name].loraSelectionCheck.isChecked()
        ]

    def update_order_summary() -> None:
        """Show effective execution order without using visual picker positions."""
        lines = []
        for index, name in enumerate(effective_names(), 1):
            values = extra.get(name)
            weights_for_name = values if values else [weights[name]]
            values_text = ", ".join(format_weight(v) for v in weights_for_name)
            lines.append(f"{index}. {shown(name)} @ {values_text}")
        editor.loraSelectionOrderSummary.setText("\n".join(lines) if lines else "No LoRAs selected")

    def update_count() -> None:
        count = sum(row.loraSelectionCheck.isChecked() for row in rows.values())
        editor.loraSelectionCount.setText(f"{count} selected")
        update_order_summary()
        reorder_rows()

    def add_row(name: str, *, checked: bool, weight: float, top: bool = False) -> QWidget:
        row = _load("lora_selection_row.ui", editor.loraSelectionRows)
        row.setProperty("identifier", name)
        check, slider, label = row.loraSelectionCheck, row.loraSelectionSlider, row.loraSelectionWeight
        family = families.get(name, "Unknown")
        mark = marks.get(name)
        check.setText(f"{shown(name)} · {family}")
        check.setChecked(checked)
        check.setAccessibleName(
            f"Use {_display_label(name)}, family {family}" + (", not on the DT+ key" if mark == NOT_ON_KEY else "")
        )
        if mark == NOT_ON_KEY:
            # A small amber pill marks only what the key does not hold; held LoRAs carry no extra text.
            pill = QLabel("not on key", row)
            pill.setObjectName("loraKeyPill")
            pill.setProperty("state", "warning")
            pill.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            row.loraSelectionRowLayout.insertWidget(1, pill)
        suggestion = suggestions.get(name)
        lead = f"{name}\nFamily: {family}" + (
            f"\nDT+ key: {mark}. A LoRA that needs a re-attach is restored automatically at generation and may use key storage."
            if mark
            else ""
        )
        lead += f"\nCatalogue suggests weight {suggestion:g}." if suggestion is not None else ""
        weights[name] = weight
        row.metadata_hint = LoRAHover(
            row,
            lambda: [("LoRA metadata", (LoRASelection(name, weights[name]),))],
            detailed=True,
            descendants=True,
            lead=lambda: lead,  # the checkbox carries no tooltip of its own, so the card shows over it too
        )
        slider.setValue(max(slider.minimum(), min(slider.maximum(), round(weight * WEIGHT_STEPS))))
        slider.setAccessibleName(f"{_display_label(name)} weight")
        label.setText(format_weight(weight))

        def moved(value: int) -> None:
            weights[name] = round(value / WEIGHT_STEPS, 2)
            label.setText(format_weight(weights[name]))
            check.setChecked(True)  # weighing a LoRA means using it
            update_order_summary()

        slider.valueChanged.connect(moved)
        slider.installEventFilter(wheel_step)
        row.installEventFilter(_ToggleRow(row))
        if multi_weight:
            extra[name] = list(collected[name]) if len(collected.get(name, ())) > 1 else []
            row.loraSelectionAddWeightButton.setVisible(True)
            row.loraSelectionAddWeightButton.setAccessibleName(f"Add weight for {_display_label(name)}")

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
                    chip.setAccessibleName(f"Remove weight {format_weight(value)} for {_display_label(name)}")
                    chip.clicked.connect(lambda _=False, i=index: drop(i))
                    chips.insertWidget(index, chip)
                host.setMinimumHeight(CHIP_HEIGHT + 2 if extra[name] else 0)
                host.setVisible(bool(extra[name]))

            def drop(index: int) -> None:
                del extra[name][index]
                if not extra[name]:
                    check.setChecked(False)  # dropping the last collected weight drops the LoRA
                render_chips()
                update_order_summary()

            def add_weight() -> None:
                if weights[name] not in extra[name]:
                    extra[name].append(weights[name])
                check.setChecked(True)
                render_chips()
                update_order_summary()

            row.loraSelectionAddWeightButton.clicked.connect(add_weight)
            slider.installEventFilter(_ClickToAdd(row, add_weight))
            render_chips()  # several loaded weights start as chips

        def toggle_row(checked: bool) -> None:
            row.setProperty("checkedSelection", checked)
            row.style().unpolish(row)
            row.style().polish(row)
            update_count()

        row.setProperty("checkedSelection", checked)
        check.toggled.connect(toggle_row)
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
            visible = not needle or needle in name.casefold() or needle in families.get(name, "Unknown").casefold()
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
        # Visual A–Z sorting must never reorder existing effective requests or weights.
        chosen = [(name, value) for name in effective_names() for value in (extra.get(name) or [weights[name]])]
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
