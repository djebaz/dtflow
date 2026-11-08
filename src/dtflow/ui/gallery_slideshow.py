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
    QLabel,
    QPushButton,
    QScrollArea,
    QSlider,
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
        self.position = self.controls.findChild(QSlider, "slideshowPosition")
        self.position_label = self.controls.findChild(QLabel, "slideshowPositionLabel")
        if self.seconds is None or self.direction is None or self.position is None or self.position_label is None:
            raise ValueError("Missing slideshow rate/direction control")
        self.pager = gallery._widget("galleryPagerBar")
        self.header = gallery._widget("galleryBrandRow")
        self._pager_opacity = None
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
        self.idle_timer = QTimer(self)
        self.idle_timer.setSingleShot(True)
        self.idle_timer.setInterval(2500)
        self.idle_timer.timeout.connect(self._hide_controls)
        self.direction.currentIndexChanged.connect(self._direction_changed)
        self.position.valueChanged.connect(self._seek)
        self.position.sliderPressed.connect(self.timer.stop)
        self.position.sliderReleased.connect(lambda: self._seek(self.position.sliderPosition()))
        self.position.sliderMoved.connect(self._preview_position)
        self.button.clicked.connect(self.toggle)
        self.interval.valueChanged.connect(self._interval_changed)
        # Images' header/brand and other shell controls are siblings of the gallery page.
        # Page-only shortcut scope missed F2 when focus stayed in one of those controls.
        self.shortcut = QShortcut(QKeySequence("F2"), gallery.page.window())
        self.shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
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
        self._mouse_tracking = [
            (widget, widget.hasMouseTracking())
            for widget in (
                gallery.surface,
                self.info_scroll.viewport(),
                gallery.hover_card,
                gallery.hover_card.prompt.viewport(),
            )
        ]
        for widget, _previous in self._mouse_tracking:
            widget.setMouseTracking(True)
        self._filmstrip_visible = gallery.film_scroll.isVisibleTo(gallery.page)
        self._header_visible = self.header.isVisibleTo(gallery.page)
        self._pager_index = gallery.page.layout().indexOf(self.pager)
        self.active = True
        with QSignalBlocker(self.button):
            self.button.setChecked(True)
        gallery._apply_live(0)  # polling/Follow must not pull an automatically advanced selection away
        gallery.show_view("Detail")
        toggle_image_focus(gallery.page, True)
        self.header.hide()
        gallery.page.layout().removeWidget(self.pager)
        self.pager.setParent(gallery.surface)
        self.pager.setProperty("slideshow", True)
        self.pager.style().unpolish(self.pager)
        self.pager.style().polish(self.pager)
        self._pager_opacity = QGraphicsOpacityEffect(self.pager)
        self._pager_opacity.setOpacity(0.35)
        self.pager.setGraphicsEffect(self._pager_opacity)
        for widget in [self.pager, *self.pager.findChildren(QWidget)]:
            widget.installEventFilter(self)
        self.pager.show()
        gallery.film_scroll.hide()
        self.info_scroll.show()
        gallery.hover_card.set_dock(self.info_scroll)
        self._window.showFullScreen()
        QApplication.instance().installEventFilter(self)
        self._reveal_controls()
        self._position_controls()
        self.sync_position()
        self.timer.start(self._milliseconds())

    @Slot()
    def stop(self) -> None:
        self.timer.stop()
        self.idle_timer.stop()
        if not self.active:
            return
        self.active = False
        QApplication.instance().removeEventFilter(self)
        self.controls.hide()
        gallery = self.gallery
        for widget, previous in self._mouse_tracking:
            widget.setMouseTracking(previous)
        with QSignalBlocker(self.button):
            self.button.setChecked(False)
        gallery.hover_card.set_dock(None)
        self.info_scroll.hide()
        gallery.film_scroll.setVisible(self._filmstrip_visible)
        self.pager.setGraphicsEffect(None)
        self._pager_opacity = None
        self.pager.setParent(gallery.page)
        gallery.page.layout().insertWidget(self._pager_index, self.pager)
        self.pager.setProperty("slideshow", False)
        self.pager.style().unpolish(self.pager)
        self.pager.style().polish(self.pager)
        self.pager.show()
        self.header.setVisible(self._header_visible)
        gallery._slideshow_position = None
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
            self._reveal_controls()
            self.timer.start(self._milliseconds())

    def _position_controls(self) -> None:
        self.pager.setGeometry(0, 0, self.gallery.surface.width(), self.pager.height())
        self.pager.raise_()
        self.controls.adjustSize()
        self.controls.move(
            max(0, self.gallery.surface.width() - self.controls.width() - 16),
            self.pager.height() + 12,
        )
        self.controls.raise_()

    def _direction_changed(self, _index: int) -> None:
        if self.active:
            self._reveal_controls()
            self.timer.start(self._milliseconds())

    def sync_position(self) -> None:
        if not self.active or self.position.isSliderDown():
            return
        gallery = self.gallery
        index = next((i for i, item in enumerate(gallery.items) if item.identity == gallery.selected_id), -1)
        number = gallery.offset + index + 1 if index >= 0 else 0
        with QSignalBlocker(self.position):
            self.position.setRange(1, max(1, gallery.total))
            self.position.setValue(max(1, number))
        self.position.setEnabled(gallery.total > 0)
        self.position_label.setText(f"{number:,} / {gallery.total:,}")

    def _preview_position(self, number: int) -> None:
        self.position_label.setText(f"{number:,} / {self.gallery.total:,}")

    def _seek(self, number: int) -> None:
        if self.active and not self.gallery.runner.closed:
            self._reveal_controls()
            self.gallery.slideshow_seek(number)
            self.timer.start(self._milliseconds())

    def _reveal_controls(self) -> None:
        if self.active:
            self.pager.show()
            self.controls.show()
            self._position_controls()
            self.idle_timer.start()

    def _hide_controls(self) -> None:
        if QApplication.activePopupWidget() is not None or self.position.isSliderDown():
            self.idle_timer.start()
            return
        self.pager.hide()
        self.controls.hide()

    def eventFilter(self, watched, event):
        if not self.active:
            return False
        if not isinstance(watched, QWidget) or watched.window() is not self._window:
            return False
        if event.type() in (
            QEvent.Type.MouseMove,
            QEvent.Type.MouseButtonPress,
            QEvent.Type.KeyPress,
            QEvent.Type.Wheel,
        ):
            self._reveal_controls()
        if watched is self.gallery.surface:
            if event.type() == QEvent.Type.Resize:
                self._position_controls()
            return False  # image wheel keeps its existing zoom behavior
        if not (
            watched is self.controls
            or self.controls.isAncestorOf(watched)
            or watched is self.pager
            or self.pager.isAncestorOf(watched)
        ):
            return False
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
                step = 1 if delta > 0 else -1
                if watched is self.position:
                    self.position.setValue(self.position.value() + step)
                else:
                    self.interval.stepBy(step)
            event.accept()
            return True
        if event.type() in (QEvent.Type.Enter, QEvent.Type.FocusIn):
            effect = self._pager_opacity if watched is self.pager or self.pager.isAncestorOf(watched) else self.opacity
            if effect is not None:
                effect.setOpacity(1.0)
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
        if self._pager_opacity is not None:
            engaged = (
                self.pager.underMouse() or focus is self.pager or (focus is not None and self.pager.isAncestorOf(focus))
            )
            self._pager_opacity.setOpacity(1.0 if engaged else 0.35)

    def image_ready(self) -> None:
        if self.active:
            self.sync_position()
            self.timer.start(self._milliseconds())

    def _advance(self) -> None:
        gallery = self.gallery
        if not self.active:
            return
        if gallery.runner.closed or not gallery._active or gallery.mode != "Detail" or gallery._refresh_failing:
            self.stop()
            return
        if self.position.isSliderDown():
            self.timer.start(100)
            return
        if (
            gallery._loading
            or gallery._adjacent_loading
            or gallery._image_pending
            or gallery._pending_page_jump
            or gallery._slideshow_position is not None
        ):
            self.timer.start(100)
            return
        if not gallery.items:
            self.stop()
            return
        self.navigate(1 if self.direction.currentIndex() == 0 else -1, reveal=False)

    def navigate(self, delta: int, *, reveal: bool = True) -> None:
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
        if reveal:
            self._reveal_controls()
        gallery.slideshow_step(delta)
        # image_ready restarts the full dwell after decoding; a slow read/paging is only polled.
        self.timer.start(100 if gallery._loading or gallery._image_pending else self._milliseconds())
