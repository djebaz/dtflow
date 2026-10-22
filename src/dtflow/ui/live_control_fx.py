"""Halo, resume ring and dot icon for the Live control (design spec: LiveControl.spec.md)."""

import sys

from PySide6.QtCore import QEasingCurve, QObject, QPointF, QRect, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QFrame, QGraphicsDropShadowEffect, QGraphicsOpacityEffect, QPushButton

HALO_MS = 320  # halo fade
RING_MS = 600  # one resume ring
HALO_BLUR = 10  # px, 0 -> 10
HALO_ALPHA = 115  # 45 % of 255


def animations_enabled() -> bool:
    """Windows 'Show animations in Windows' switch; the reduced-motion equivalent."""
    if sys.platform != "win32":
        return True
    import ctypes

    flag = ctypes.c_int(1)
    ok = ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(flag), 0)  # SPI_GETCLIENTAREAANIMATION
    return bool(flag.value) if ok else True


def live_dot_icon(on: bool, focus: QColor, secondary: QColor, size: int = 10) -> QIcon:
    """10 px status dot: filled with the focus colour when Live is on, a hollow 2 px ring when off."""
    ratio = 2
    pixmap = QPixmap(size * ratio, size * ratio)
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    if on:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(focus)
        painter.drawEllipse(QPointF(size / 2, size / 2), size / 2, size / 2)
    else:
        painter.setPen(QPen(secondary, 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QPointF(size / 2, size / 2), size / 2 - 1, size / 2 - 1)
    painter.end()
    return QIcon(pixmap)


class LiveFollowFx(QObject):
    """Animates the Follow button: halo while following, one ring when following resumes."""

    def __init__(self, button: QPushButton, focus_color: QColor):
        super().__init__(button)
        self.button = button
        self.color = QColor(focus_color)
        self.shadow = QGraphicsDropShadowEffect(button)
        self.shadow.setOffset(0, 0)
        self._set_halo(0.0)
        button.setGraphicsEffect(self.shadow)
        self.halo = QVariantAnimation(self)
        self.halo.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self.halo.valueChanged.connect(lambda v: self._set_halo(float(v)))
        self._level = 0.0
        # The ring is a sibling overlay: an effect on the button itself would clip it.
        self.ring = QFrame(button.parentWidget())
        self.ring.setObjectName("liveResumeRing")  # QSS: 1px solid focus, transparent fill
        self.ring.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.ring.hide()
        self.ring_opacity = QGraphicsOpacityEffect(self.ring)
        self.ring.setGraphicsEffect(self.ring_opacity)
        self.ring_anim = QVariantAnimation(self)
        self.ring_anim.setDuration(RING_MS)
        self.ring_anim.setStartValue(0.0)
        self.ring_anim.setEndValue(1.0)
        self.ring_anim.setEasingCurve(QEasingCurve.Type.OutQuad)
        self.ring_anim.valueChanged.connect(self._step_ring)
        self.ring_anim.finished.connect(self.ring.hide)
        self._lit = False

    def _set_halo(self, level: float) -> None:
        self._level = level
        self.shadow.setBlurRadius(HALO_BLUR * level)
        color = QColor(self.color)
        color.setAlpha(int(HALO_ALPHA * level))
        self.shadow.setColor(color)

    def _step_ring(self, t) -> None:
        t = float(t)
        base = self.button.geometry()
        width, height = base.width() * (1 + 0.5 * t), base.height() * (1 + 0.9 * t)
        self.ring.setGeometry(
            QRect(int(base.center().x() - width / 2), int(base.center().y() - height / 2), int(width), int(height))
        )
        self.ring_opacity.setOpacity(0.9 * (1 - t))

    def apply(self, previous: str | None, state: str) -> None:
        """Call from GalleryPage._sync_live_text whenever the drawn state changes."""
        lit = state == "following"
        if lit != self._lit:
            self._lit = lit
            self.halo.stop()
            if animations_enabled():
                self.halo.setStartValue(self._level)
                self.halo.setEndValue(1.0 if lit else 0.0)
                self.halo.setDuration(HALO_MS)
                self.halo.start()
            else:
                self._set_halo(1.0 if lit else 0.0)
        if previous == "paused" and lit and animations_enabled():  # resume only; never on manual toggles
            self.ring.raise_()
            self._step_ring(0.0)
            self.ring.show()
            self.ring_anim.stop()
            self.ring_anim.start()
