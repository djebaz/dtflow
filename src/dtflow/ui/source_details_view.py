"""One-line labels for what a safetensors file declares about this LoRA's training (#414).

File content is untrusted: it is drawn as plain text (never rich text or image markup), and each line is simply cut
with an ellipsis to the width it has, so it never grows the layout."""

from __future__ import annotations

import html
from functools import cache

from PySide6.QtCore import QEvent, QObject, QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import QApplication, QLabel, QSizePolicy, QToolTip, QWidget

from dtflow.ui.theme import load_tokens


def safe_tooltip(text: str) -> str:
    """Tooltips are rich text by default; file content is escaped so it shows literally."""
    return f"<p style='white-space:pre-wrap'>{html.escape(text)}</p>" if text else ""


@cache
def _tokens() -> dict[str, str]:
    return load_tokens()


def _font(weight_token: str) -> QFont:
    tokens = _tokens()
    font = QFont(tokens["type.family"])
    font.setPointSizeF(float(tokens["type.small"].removesuffix("pt")))
    font.setWeight(QFont.Weight(int(tokens[weight_token])))
    return font


def replace_with_runs(label: QLabel) -> "RunsLine":
    """Swap a Designer placeholder label for a RunsLine in the same layout slot (the form stays canonical)."""
    line = RunsLine(label.parentWidget())
    line.setObjectName(label.objectName())
    layout = label.parentWidget().layout()
    layout.replaceWidget(label, line)
    label.hide()
    label.deleteLater()
    return line


class RunsLine(QWidget):
    """One line of (text, strong) runs drawn as plain text, cut with an ellipsis; never grows the layout.

    Drawn with QPainter, so file content can never be interpreted as markup. Design tokens only: labels and
    separators use ``color.secondary`` at ``type.weightBody``, emphasized values ``color.text`` at
    ``type.weightEmphasis``, both at ``type.small`` (the same as ``QLabel[role="secondary"]``)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._runs: tuple[tuple[str, bool], ...] = ()
        self._muted_font, self._strong_font = _font("type.weightBody"), _font("type.weightEmphasis")
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(QFontMetrics(self._strong_font).lineSpacing() + 2)

    def set_runs(self, runs: tuple[tuple[str, bool], ...]) -> None:
        self._runs = runs
        self.update()

    def plain_text(self) -> str:
        return "".join(text for text, _strong in self._runs)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(0, self.height())

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
        tokens = _tokens()
        muted, strong = QColor(tokens["color.secondary"]), QColor(tokens["color.text"])
        painter = QPainter(self)
        x, width = 0, self.width()
        for text, is_strong in self._runs:
            painter.setFont(self._strong_font if is_strong else self._muted_font)
            painter.setPen(strong if is_strong else muted)
            metrics = painter.fontMetrics()
            baseline = (self.height() + metrics.ascent() - metrics.descent()) // 2
            if metrics.horizontalAdvance(text) <= width - x:
                painter.drawText(x, baseline, text)
                x += metrics.horizontalAdvance(text)
                continue
            painter.drawText(x, baseline, metrics.elidedText(text, Qt.TextElideMode.ElideRight, max(0, width - x)))
            break
        painter.end()


class RegionTooltip(QObject):
    """One tooltip for a whole region: the union of ``members`` (and ``margin`` px around it, full height of ``root``).

    Qt shows a tooltip only for the widget under the cursor (or its ancestors with their own), so the lines, the
    gaps between them and the band's margins around them would otherwise answer differently. This filter answers every
    ToolTip event inside the region with the same text, whichever widget it reaches."""

    def __init__(self, root: QWidget, members: list[QWidget], *, margin: int = 8, full_height: bool = False) -> None:
        super().__init__(root)
        self._root, self._members, self._margin, self._full_height = root, members, margin, full_height
        self._text = ""
        watched = {root, *members}
        for member in members:
            watched.update(member.findChildren(QWidget))
        for widget in watched:
            widget.installEventFilter(self)

    def set_text(self, text: str) -> None:
        self._text = text

    def _area(self) -> QRect:
        area = QRect()
        for member in self._members:
            if member.isVisible():
                top_left = member.mapTo(self._root, member.rect().topLeft())
                area = area.united(QRect(top_left, member.size()))
        area = area.adjusted(-self._margin, -self._margin, self._margin, self._margin)
        if self._full_height:
            area.setTop(0)
            area.setBottom(self._root.height())
        return area

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt override
        if event.type() == QEvent.Type.ToolTip and self._text and isinstance(watched, QWidget):
            if self._area().contains(self._root.mapFromGlobal(event.globalPos())):
                # Match the Logs hover: Qt's short native fade takes precedence over its roll animation.
                # Scope the preference to this show and respect desktop-wide disabled effects.
                effects = (Qt.UIEffect.UI_FadeTooltip, Qt.UIEffect.UI_AnimateTooltip)
                saved_effects = (
                    tuple(QApplication.isEffectEnabled(effect) for effect in effects)
                    if QApplication.isEffectEnabled(Qt.UIEffect.UI_General)
                    else None
                )
                try:
                    if saved_effects is not None:
                        QApplication.setEffectEnabled(Qt.UIEffect.UI_FadeTooltip, True)
                    QToolTip.showText(event.globalPos(), self._text, self._root)
                finally:
                    # Enabling fade also enables animation; restore both flags, even if showing fails.
                    if saved_effects is not None:
                        for effect, enabled in zip(effects, saved_effects, strict=True):
                            QApplication.setEffectEnabled(effect, enabled)
                event.accept()
                return True
        return False
