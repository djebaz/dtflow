"""Advanced section of the Generate page and the Queues composer: optional Draw Things values, collapsed by default (#539).

The panel owns only the controls and the first-pass preview. Validation, HiRes sizing and the provider mapping stay
in ``dtflow.core.advanced`` and the Draw Things backend, so every mode that later offers these values shares one rule.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QFile, QIODevice, QObject, Qt, Signal
from PySide6.QtUiTools import QUiLoader
from PySide6.QtWidgets import QCheckBox, QComboBox, QDoubleSpinBox, QLabel, QPushButton, QSpinBox, QWidget

from dtflow.core import advanced as rules
from dtflow.core.advanced import AdvancedSettings
from dtflow.core.models import ValidationError

FORM = Path(__file__).parent / "forms" / "advanced_panel.ui"
DEFAULT_TEXT = "Default"
# Widgets the Generate page marks invalid by name when a request error points at one of these fields.
FIELD_WIDGETS = (
    "hiresFixCheck",
    "hiresFixFactorSpin",
    "hiresFixWidthSpin",
    "hiresFixHeightSpin",
    "seedModeCombo",
    "tiledDecodingCheck",
)


class AdvancedPanel(QObject):
    """The controls and their first-pass preview.

    By default the panel sits under Generate's parameters and reads the final Size from Generate's spin boxes. A host
    such as the Queues composer passes its own ``layout`` and ``index`` (where the frame goes), ``size`` (the final size
    the preview uses, or ``None`` when there is none) and ``size_note`` (what to add when other sizes are derived the
    same way).
    """

    changed = Signal()

    def __init__(
        self,
        page: QWidget,
        *,
        layout=None,
        index: int | None = None,
        size: Callable[[], tuple[int, int] | None] | None = None,
        size_note: str = "",
    ):
        super().__init__(page)
        self.page = page
        generate = layout is None
        self._size = size or (lambda: (page.widthSpinBox.value(), page.heightSpinBox.value()))
        self._size_note = size_note
        file = QFile(str(FORM))
        if not file.open(QIODevice.OpenModeFlag.ReadOnly):
            raise OSError("Advanced settings form could not be opened.")
        try:
            self.frame = QUiLoader().load(file, page)
        finally:
            file.close()
        if self.frame is None:
            raise RuntimeError("Advanced settings form could not be loaded.")
        find = self.frame.findChild
        self.toggle = find(QPushButton, "advancedToggle")
        self.contents = find(QWidget, "advancedPanelContents")
        self.scope = find(QLabel, "advancedScopeLabel")
        self.hires = find(QCheckBox, "hiresFixCheck")
        self.factor = find(QDoubleSpinBox, "hiresFixFactorSpin")
        self.strength = find(QDoubleSpinBox, "hiresFixStrengthSpin")
        self.first_width = find(QSpinBox, "hiresFixWidthSpin")
        self.first_height = find(QSpinBox, "hiresFixHeightSpin")
        self.summary = find(QLabel, "hiresFixSummaryLabel")
        self.seed_mode = find(QComboBox, "seedModeCombo")
        self.tiled = find(QCheckBox, "tiledDecodingCheck")
        # The page marks fields invalid by Designer name, so these are reachable as page attributes too.
        for name in FIELD_WIDGETS:
            setattr(page, name, find(QWidget, name))
        self.seed_mode.addItem(DEFAULT_TEXT)
        self.seed_mode.addItems(rules.SEED_MODES)
        self.tiled.setCheckState(Qt.CheckState.PartiallyChecked)
        self._scope: str | None = "image"
        self._first_pass_seen = {self.first_width: 0, self.first_height: 0}
        for spin in self._first_pass_seen:
            spin.valueChanged.connect(lambda value, spin=spin: self._snap_first_pass(spin, value))
        self.toggle.toggled.connect(self._show)
        self.hires.toggled.connect(self._hires_toggled)
        for signal in (
            self.factor.valueChanged,
            self.strength.valueChanged,
            self.first_width.valueChanged,
            self.first_height.valueChanged,
            self.seed_mode.currentIndexChanged,
            self.tiled.stateChanged,
        ):
            signal.connect(self._edited)
        if generate:
            page.widthSpinBox.valueChanged.connect(self._edited)
            page.heightSpinBox.valueChanged.connect(self._edited)
            layout = page.generationContextLayout
            index = layout.indexOf(page.parametersBox) + 1
        layout.insertWidget(layout.count() if index is None else index, self.frame)
        self._edited()

    def refresh(self) -> None:
        """The host's size changed: show the first pass for it again."""
        self.summary.setText(self._preview())

    # -- state ------------------------------------------------------------------------------------------------------
    def _show(self, shown: bool) -> None:
        self.contents.setVisible(shown)
        self.toggle.setText("Hide" if shown else "Show")
        self.toggle.setAccessibleName("Hide advanced settings" if shown else "Show advanced settings")

    def _snap_first_pass(self, spin: QSpinBox, value: int) -> None:
        """Auto (0), then 128 and up in 64 steps: a click or typed value in between lands on a size the provider accepts."""
        previous = self._first_pass_seen[spin]
        snapped = value
        if value:
            snapped = max(
                rules.FIRST_PASS_MIN,
                min(2048, (value + rules.DIMENSION_STEP // 2) // rules.DIMENSION_STEP * rules.DIMENSION_STEP),
            )
            if value < rules.FIRST_PASS_MIN and previous >= rules.FIRST_PASS_MIN:
                snapped = 0  # stepping down from the smallest size returns to Auto
        self._first_pass_seen[spin] = snapped
        if snapped != value:
            spin.setValue(snapped)

    def _hires_toggled(self, on: bool) -> None:
        if on and self.factor.value() <= rules.HIRES_MIN_FACTOR:
            self.factor.setValue(rules.HIRES_DEFAULT_FACTOR)  # switching on without a choice means the default 2x
        self._edited()

    def _sync_enabled(self) -> None:
        """The factor needs the check; strength and the first-pass size need a factor above 1 (a factor of 1 is off)."""
        checked = self.hires.isChecked() and self.hires.isEnabled()
        self.factor.setEnabled(checked)
        for widget in (self.strength, self.first_width, self.first_height):
            widget.setEnabled(checked and self.factor.value() > rules.HIRES_MIN_FACTOR)

    def _edited(self, *_args) -> None:
        self._sync_enabled()
        self.summary.setText(self._preview())
        self.changed.emit()

    def _preview(self) -> str:
        """First pass -> final for the settings as shown, or the reason they cannot be used; never blocks editing."""
        if not self.hires.isEnabled():
            return ""
        size = self._size()
        if size is None:
            return ""
        width, height = size
        try:
            settings = self.settings()
            rules.check_first_pass(width, height, settings)
        except ValidationError as exc:
            return str(exc)
        first = rules.first_pass_size(width, height, settings)
        if first is None:
            return "HiRes is off." if self.hires.isChecked() else ""
        return f"First pass {first[0]}x{first[1]} → final {width}x{height}{self._size_note}"

    def set_scope(self, scope: str | None) -> None:
        """Which kind of request the form would send now: ``image``, ``pose``, ``video`` or ``None`` (not a single
        request, as in Scripts). Controls a kind cannot carry stay on screen, disabled and labelled, never hidden."""
        self._scope = scope
        allowed = rules.SCOPE_FIELDS.get(scope, ())
        notes = {
            "pose": "Advanced settings are sent as chosen; provider compatibility may vary.",
            "video": "Only seed mode is sent for video; HiRes and tiled decoding are image settings.",
            None: "Advanced settings are not sent in this mode.",
        }
        self.scope.setText(notes.get(scope, ""))
        self.scope.setVisible(scope != "image")
        self.hires.setEnabled("hires_fix_factor" in allowed)
        self.seed_mode.setEnabled("seed_mode" in allowed)
        self.tiled.setEnabled("tiled_decoding" in allowed)
        self._edited()

    @property
    def scope_name(self) -> str | None:
        return self._scope

    # -- values -----------------------------------------------------------------------------------------------------
    def settings(self) -> AdvancedSettings | None:
        """The chosen values, ``None`` when nothing is chosen. Raises ``ValidationError`` for an unusable combination."""
        factor = self.factor.value() if self.hires.isChecked() else None
        on = factor is not None and factor > rules.HIRES_MIN_FACTOR
        strength = self.strength.value() if on and self.strength.value() >= 0 else None
        mode = self.seed_mode.currentText()
        state = self.tiled.checkState()
        chosen = AdvancedSettings(
            hires_fix_factor=factor if on else None,
            hires_fix_width=(self.first_width.value() or None) if on else None,
            hires_fix_height=(self.first_height.value() or None) if on else None,
            hires_fix_strength=strength,
            seed_mode=None if mode == DEFAULT_TEXT else mode,
            tiled_decoding=None if state == Qt.CheckState.PartiallyChecked else state == Qt.CheckState.Checked,
        )
        return None if chosen.is_default else chosen

    def values(self) -> AdvancedSettings | None:
        """What a request carries: the chosen values in a mode that sends them, otherwise nothing."""
        return rules.restrict(self.settings(), self._scope)

    def restore(self, settings: AdvancedSettings | None) -> None:
        """Show recorded values exactly; ``None`` resets every control to "not chosen"."""
        settings = settings or AdvancedSettings()
        for widget in (
            self.hires,
            self.factor,
            self.strength,
            self.first_width,
            self.first_height,
            self.seed_mode,
            self.tiled,
        ):
            widget.blockSignals(True)
        try:
            on = settings.hires_fix_active
            self.hires.setChecked(on)
            self.factor.setValue(settings.hires_fix_factor if on else rules.HIRES_DEFAULT_FACTOR)
            self.strength.setValue(
                settings.hires_fix_strength if settings.hires_fix_strength is not None else self.strength.minimum()
            )
            self.first_width.setValue(settings.hires_fix_width or 0)
            self.first_height.setValue(settings.hires_fix_height or 0)
            self.seed_mode.setCurrentText(settings.seed_mode or DEFAULT_TEXT)
            self.tiled.setCheckState(
                Qt.CheckState.PartiallyChecked
                if settings.tiled_decoding is None
                else Qt.CheckState.Checked
                if settings.tiled_decoding
                else Qt.CheckState.Unchecked
            )
        finally:
            for widget in (
                self.hires,
                self.factor,
                self.strength,
                self.first_width,
                self.first_height,
                self.seed_mode,
                self.tiled,
            ):
                widget.blockSignals(False)
        if not settings.is_default:
            self.toggle.setChecked(True)  # recovered values are shown, never hidden behind a collapsed section
        self._edited()
