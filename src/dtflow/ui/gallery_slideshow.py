"""Window-owned slideshow presentation over the existing bounded Detail viewer."""

from pathlib import Path

from PySide6.QtCore import QEvent, QFile, QIODevice, QObject, QSignalBlocker, Qt, QTimer, Slot
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtUiTools import QUiLoader
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QGraphicsOpacityEffect,
    QPushButton,
    QScrollArea,
    QWidget,
)

from dtflow.ui.gallery_detail_preview import toggle_image_focus


class GallerySlideshow(QObject):
    """No image inventory or decoder of its own: advance through ordinary gallery paging."""

    def __init__(self, gallery):
        super().__init__(gallery.page)
        self.gallery = gallery
        self.active = False
        self.button = gallery._widget("gallerySlideshowButton", QPushButton)
        self.interval = gallery._widget("gallerySlideshowInterval", QDoubleSpinBox)
        self.info_scroll = gallery._widget("gallerySlideshowInfoScroll", QScrollArea)
        self.info_scroll.hide()
        source = QFile(str(Path(__file__).with_name("forms") / "gallery_slideshow.ui"))
        if not source.open(QIODevice.OpenModeFlag.ReadOnly):
            raise RuntimeError("Cannot open slideshow controls form")
        try:
            self.controls = QUiLoader().load(source, gallery.surface)
        finally:
            source.close()
        if self.controls is None:
            raise RuntimeError("Cannot load slideshow controls form")
        self.controls.hide()
        self.seconds = self.controls.findChild(QDoubleSpinBox, "slideshowSeconds")
        self.direction = self.controls.findChild(QComboBox, "slideshowDirection")
        if self.seconds is None or self.direction is None:
            raise ValueError("Missing slideshow rate/direction control")
        self.opacity = QGraphicsOpacityEffect(self.controls)
        self.controls.setGraphicsEffect(self.opacity)
        self.opacity.setOpacity(0.35)
        self.controls.installEventFilter(self)
        for widget in self.controls.findChildren(QWidget):
            widget.installEventFilter(self)
        gallery.surface.installEventFilter(self)
        self.controls.findChild(QPushButton, "slideshowPrevious").clicked.connect(lambda: self.navigate(-1))
        self.controls.findChild(QPushButton, "slideshowNext").clicked.connect(lambda: self.navigate(1))
        self.controls.findChild(QPushButton, "slideshowLeave").clicked.connect(self.stop)
        self.seconds.valueChanged.connect(self.interval.setValue)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._advance)
        self.direction.currentIndexChanged.connect(self._direction_changed)
        self.button.clicked.connect(self.toggle)
        self.interval.valueChanged.connect(self._interval_changed)
        self.shortcut = QShortcut(QKeySequence("F2"), gallery.page)
        self.shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.shortcut.setAutoRepeat(False)
        self.shortcut.activated.connect(self.toggle)
        gallery.runner.shutdown_started.connect(self.stop)
        gallery.page._slideshow = self

    def toggle(self) -> None:
        if self.active:
            self.stop()
        elif self.gallery._active and not self.gallery.runner.closed and self.gallery.items:
            self.start()
        else:
            with QSignalBlocker(self.button):
                self.button.setChecked(False)

    def start(self) -> None:
        gallery = self.gallery
        self._previous_view = gallery.mode
        self._previous_live = gallery._live_mode
        self._previous_focus = bool(gallery.page.property("imageFocus"))
        self._window = gallery.page.window()
        self._window_state = self._window.windowState()
        self._filmstrip_visible = gallery.film_scroll.isVisibleTo(gallery.page)
        self.active = True
        with QSignalBlocker(self.button):
            self.button.setChecked(True)
        gallery._apply_live(0)  # polling/Follow must not pull an automatically advanced selection away
        gallery.show_view("Detail")
        toggle_image_focus(gallery.page, True)
        gallery.film_scroll.hide()
        self.info_scroll.show()
        gallery.hover_card.set_dock(self.info_scroll)
        self._window.showFullScreen()
        self.controls.show()
        self._position_controls()
        self.timer.start(self._milliseconds())

    @Slot()
    def stop(self) -> None:
        self.timer.stop()
        if not self.active:
            return
        self.active = False
        self.controls.hide()
        gallery = self.gallery
        with QSignalBlocker(self.button):
            self.button.setChecked(False)
        gallery.hover_card.set_dock(None)
        self.info_scroll.hide()
        gallery.film_scroll.setVisible(self._filmstrip_visible)
        toggle_image_focus(gallery.page, self._previous_focus)
        if not gallery.runner.closed:
            self._window.setWindowState(self._window_state)
            gallery.show_view(self._previous_view, persist=False)
            restoring = gallery._restoring
            gallery._restoring = True
            try:
                gallery._apply_live(self._previous_live)
            finally:
                gallery._restoring = restoring
            gallery._schedule_save()

    def _milliseconds(self) -> int:
        return round(self.interval.value() * 1000)

    def _interval_changed(self, _value: float) -> None:
        with QSignalBlocker(self.seconds):
            self.seconds.setValue(self.interval.value())
        if self.active:
            self.timer.start(self._milliseconds())

    def _position_controls(self) -> None:
        self.controls.adjustSize()
        self.controls.move(
            max(0, self.gallery.surface.width() - self.controls.width() - 16),
            16,
        )
        self.controls.raise_()

    def _direction_changed(self, _index: int) -> None:
        if self.active:
            self.timer.start(self._milliseconds())

    def eventFilter(self, watched, event):
        if not self.active:
            return False
        if watched is self.gallery.surface:
            if event.type() == QEvent.Type.Resize:
                self._position_controls()
            return False  # image wheel keeps its existing zoom behavior
        if (
            event.type() in (QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress)
            and event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right)
            and event.modifiers() == Qt.KeyboardModifier.NoModifier
        ):
            if event.type() == QEvent.Type.KeyPress and not event.isAutoRepeat():
                self.navigate(-1 if event.key() == Qt.Key.Key_Left else 1)
            event.accept()
            return True
        if event.type() == QEvent.Type.Wheel:
            delta = event.angleDelta().y() or event.pixelDelta().y()
            if delta:
                self.interval.stepBy(1 if delta > 0 else -1)
            event.accept()
            return True
        if event.type() in (QEvent.Type.Enter, QEvent.Type.FocusIn):
            self.opacity.setOpacity(1.0)
        elif event.type() in (QEvent.Type.Leave, QEvent.Type.FocusOut):
            QTimer.singleShot(0, self, self._sync_opacity)
        return False

    def _sync_opacity(self) -> None:
        focus = QApplication.focusWidget()
        engaged = (
            self.controls.underMouse()
            or focus is self.controls
            or (focus is not None and self.controls.isAncestorOf(focus))
        )
        self.opacity.setOpacity(1.0 if engaged else 0.35)

    def image_ready(self) -> None:
        if self.active:
            self.timer.start(self._milliseconds())

    def _advance(self) -> None:
        gallery = self.gallery
        if not self.active:
            return
        if gallery.runner.closed or not gallery._active or gallery.mode != "Detail" or gallery._refresh_failing:
            self.stop()
            return
        if gallery._loading or gallery._adjacent_loading or gallery._image_pending or gallery._pending_page_jump:
            self.timer.start(100)
            return
        if not gallery.items:
            self.stop()
            return
        self.navigate(1 if self.direction.currentIndex() == 0 else -1)

    def navigate(self, delta: int) -> None:
        gallery = self.gallery
        if (
            not self.active
            or gallery.runner.closed
            or gallery._loading
            or gallery._adjacent_loading
            or gallery._image_pending
            or gallery._pending_page_jump
        ):
            return
        gallery.slideshow_step(delta)
        # image_ready restarts the full dwell after decoding; a slow read/paging is only polled.
        self.timer.start(100 if gallery._loading or gallery._image_pending else self._milliseconds())
