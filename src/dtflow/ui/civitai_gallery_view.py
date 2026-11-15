"""The Browse Civitai tab of the Downloads view (#466, epic #327).

Presentation and wiring only. Listing and thumbnails live in ``dtflow.services.civitai_gallery`` and run off the GUI
thread; Download hands cards to the Downloads page, which uses the same download-and-convert path as Links / IDs.
Covers are decoded to small pixmaps held in memory for the current search only; nothing is written to disk.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from PySide6.QtCore import QEvent, QObject, QRect, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QFontMetrics,
    QImage,
    QLinearGradient,
    QPainter,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QStyle,
    QStyledItemDelegate,
    QWidget,
)

from dtflow.diagnostics import event as diagnostic_event
from dtflow.services import civitai
from dtflow.services import civitai_gallery as gallery
from dtflow.ui.gallery_loading import _SpinningRing
from dtflow.ui.runner import AsyncRunner

log = logging.getLogger(__name__)

CARD = 260  # nominal card side; the grid fits whole columns to the width (four across at the design's width)
MIN_COLUMNS = 2
GAP = 10
SOURCE = 360  # side of the decoded square cover; painted scaled to the card
CARD_ROLE = Qt.ItemDataRole.UserRole

# The design's colours (devdocs/mockups/civitai_downloads_view.html, values of the design tokens).
TEXT, SECONDARY = QColor("#e6e6e6"), QColor("#b2b2b2")
SELECTION, SELECTION_BORDER, SELECTION_TEXT = QColor("#173852"), QColor("#285d84"), QColor("#d5ecff")
WARNING, WARNING_BORDER = QColor("#ffbf59"), QColor("#9a6700")
ITEM_SURFACE, SECTION_BORDER = QColor("#222222"), QColor("#343434")
ACCENT, ACCENT_HOVER = QColor("#0068b8"), QColor("#0078d4")


def compact(value: int | None) -> str:
    if value is None:
        return "–"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1000:
        return f"{value / 1000:.1f}k" if value < 100_000 else f"{value // 1000}k"
    return str(value)


def size_text(size: int | None) -> str:
    if not size:
        return ""
    return f"{size / 1024**3:.1f} GB" if size >= 1024**3 else f"{max(1, round(size / 1024**2))} MB"


class CardDelegate(QStyledItemDelegate):
    """Paints the design's card: a square cover, a checkbox and badges top left, family and base-model chips top
    right, name and numbers on a gradient at the bottom, and the details panel with Download while hovered."""

    download_requested = Signal(object)

    def __init__(self, parent: QObject, pixmaps: dict[str, QPixmap], state_of: Callable[[gallery.GalleryCard], str]):
        super().__init__(parent)
        self._pixmaps, self._state_of = pixmaps, state_of
        self.side = CARD

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        return QSize(self.side, self.side)

    @staticmethod
    def check_rect(rect: QRect) -> QRect:
        return QRect(rect.left() + 6, rect.top() + 6, 16, 16)

    @staticmethod
    def button_rect(rect: QRect) -> QRect:
        return QRect(rect.right() - 108, rect.bottom() - 34, 100, 24)

    def editorEvent(self, event, model, option, index) -> bool:  # noqa: N802
        card = index.data(CARD_ROLE)
        if (
            event.type() == QEvent.Type.MouseButtonPress
            and event.button() == Qt.MouseButton.LeftButton
            and isinstance(card, gallery.GalleryCard)
            and card.downloadable
            and not self._state_of(card)
            and self.button_rect(option.rect.adjusted(1, 1, -1, -1)).contains(event.position().toPoint())
        ):
            self.download_requested.emit(card)
            return True  # consumed: the click starts a download and does not toggle the selection
        return False

    def paint(self, painter: QPainter, option, index) -> None:
        card = index.data(CARD_ROLE)
        if not isinstance(card, gallery.GalleryCard):
            return
        rect = option.rect.adjusted(1, 1, -1, -1)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setClipRect(rect)
        pixmap = self._pixmaps.get(card.cover_url or "")
        if pixmap is not None and not pixmap.isNull():
            painter.drawPixmap(rect, pixmap)
        else:
            painter.fillRect(rect, ITEM_SURFACE)
            painter.setPen(SECONDARY)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "No cover" if not card.cover_url else "Loading…")
        small = QFont(option.font)
        small.setPointSizeF(max(7.0, option.font.pointSizeF() - 1))
        tiny = QFont(small)
        tiny.setBold(True)
        bold = QFont(option.font)
        bold.setBold(True)
        if hovered:
            self._hover_panel(painter, rect, card, small)
        # Bottom: name and numbers on a gradient, as in the design.
        fade = QLinearGradient(0, rect.bottom() - 62, 0, rect.bottom())
        fade.setColorAt(0, QColor(0, 0, 0, 0))
        fade.setColorAt(1, QColor(0, 0, 0, 225))
        painter.fillRect(QRect(rect.left(), rect.bottom() - 62, rect.width(), 63), fade)
        inner = QRect(rect.left() + 8, rect.bottom() - 40, rect.width() - 16, 36)
        painter.setPen(TEXT)
        painter.setFont(bold)
        painter.drawText(
            QRect(inner.left(), inner.top(), inner.width(), 18),
            Qt.AlignmentFlag.AlignVCenter,
            QFontMetrics(bold).elidedText(card.name, Qt.TextElideMode.ElideRight, inner.width()),
        )
        painter.setFont(small)
        painter.setPen(SECONDARY)
        parts = [
            f"@{card.creator}" if card.creator else "",
            f"↓ {compact(card.downloads)}",
            f"👍 {compact(card.thumbs_up)}",
            size_text(card.size_bytes),
        ]
        painter.drawText(
            QRect(inner.left(), inner.top() + 18, inner.width(), 16),
            Qt.AlignmentFlag.AlignVCenter,
            QFontMetrics(small).elidedText(
                "   ".join(part for part in parts if part), Qt.TextElideMode.ElideRight, inner.width()
            ),
        )
        # Top right: the family chip, then the base model when its label differs.
        painter.setFont(tiny)
        y = rect.top() + 6
        y = self._chip(painter, card.family, rect, y, SELECTION, SELECTION_BORDER, SELECTION_TEXT, right=True)
        if card.base_model and card.base_model.casefold() != card.family.split(",")[0].casefold():
            self._chip(
                painter, card.base_model, rect, y, QColor(21, 21, 21, 199), QColor(255, 255, 255, 46), TEXT, right=True
            )
        # Top left: the checkbox, then the badges.
        box = self.check_rect(rect)
        painter.setPen(QPen(QColor("#909090"), 1))
        painter.setBrush(ACCENT if selected else QColor(30, 30, 30, 217))
        painter.drawRoundedRect(box, 2, 2)
        if selected:
            painter.setPen(QColor("#ffffff"))
            painter.drawText(box, Qt.AlignmentFlag.AlignCenter, "✓")
        y = rect.top() + 28
        dark = QColor(21, 21, 21, 199)
        if card.nsfw_label:
            y = self._chip(painter, "NSFW", rect, y, dark, WARNING_BORDER, WARNING, right=False)
        if card.availability and card.availability.casefold() != "public":
            label = "Early access" if "early" in card.availability.casefold() else card.availability
            self._chip(painter, label, rect, y, dark, WARNING_BORDER, WARNING, right=False)
        # A card already in the downloads list says so; the hovered card offers Download.
        state = self._state_of(card)
        action = self.button_rect(rect)
        painter.setFont(small)
        if state or (hovered and card.problem):
            text = state or "Unavailable"
            width = QFontMetrics(small).horizontalAdvance(text) + 14
            pill = QRect(rect.right() - width - 8, rect.bottom() - 70, width, 22)
            painter.setPen(QPen(QColor(255, 255, 255, 46), 1))
            painter.setBrush(dark)
            painter.drawRoundedRect(pill, 3, 3)
            painter.setPen(TEXT)
            painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, text)
        elif hovered and card.downloadable:
            action.moveBottom(rect.bottom() - 46)
            painter.setPen(QPen(ACCENT_HOVER, 1))
            painter.setBrush(ACCENT)
            painter.drawRoundedRect(action, 3, 3)
            painter.setPen(QColor("#ffffff"))
            several = len(card.options) > 1
            painter.drawText(action, Qt.AlignmentFlag.AlignCenter, "Download ▾" if several else "Download")
        painter.setClipping(False)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if selected:
            painter.setPen(QPen(SELECTION, 3))
            painter.drawRect(rect.adjusted(-1, -1, 0, 0))
            painter.setPen(QPen(SELECTION_BORDER, 1))
            painter.drawRect(rect)
        else:
            painter.setPen(QPen(SECTION_BORDER, 1))
            painter.drawRect(rect)
        painter.restore()

    @staticmethod
    def _hover_panel(painter: QPainter, rect: QRect, card: gallery.GalleryCard, font: QFont) -> None:
        """The design's hover overlay: base model, version, trigger words, tags, rating, published."""
        painter.fillRect(rect, QColor(15, 15, 15, 209))
        rows = [
            ("Base model", card.base_model or "–"),
            ("Version", card.version_name or "–"),
        ]
        if card.trained_words:
            rows.append(("Trigger words", ", ".join(card.trained_words[:6])))
        if card.tags:
            rows.append(("Tags", ", ".join(card.tags[:6])))
        if card.thumbs_up is not None or card.thumbs_down is not None:
            rows.append(("Rating", f"thumbs up {compact(card.thumbs_up or 0)} / down {compact(card.thumbs_down or 0)}"))
        if card.published:
            rows.append(("Published", card.published))
        if len(card.options) > 1:
            rows.append(("Versions", f"{len(card.options)} supported"))
        if card.problem:
            rows.append(("Note", card.problem))
        painter.setFont(font)
        metrics = QFontMetrics(font)
        y = rect.top() + 34
        width = rect.width() - 16
        for label, value in rows:
            if y + 16 > rect.bottom() - 76:
                break
            painter.setPen(SECONDARY)
            painter.drawText(QRect(rect.left() + 8, y, width, 16), Qt.AlignmentFlag.AlignVCenter, label)
            offset = metrics.horizontalAdvance(label) + 6
            painter.setPen(TEXT)
            painter.drawText(
                QRect(rect.left() + 8 + offset, y, width - offset, 16),
                Qt.AlignmentFlag.AlignVCenter,
                metrics.elidedText(value, Qt.TextElideMode.ElideRight, width - offset),
            )
            y += 17

    @staticmethod
    def _chip(
        painter: QPainter, text: str, rect: QRect, y: int, fill: QColor, border: QColor, colour: QColor, *, right: bool
    ) -> int:
        metrics = painter.fontMetrics()
        width = min(metrics.horizontalAdvance(text) + 14, rect.width() - 40)
        box = QRect(rect.right() - 6 - width if right else rect.left() + 6, y, width, 18)
        painter.setPen(QPen(border, 1))
        painter.setBrush(fill)
        painter.drawRoundedRect(box, 3, 3)
        painter.setPen(colour)
        painter.drawText(
            box, Qt.AlignmentFlag.AlignCenter, metrics.elidedText(text, Qt.TextElideMode.ElideRight, width - 8)
        )
        return y + 21


class _Resized(QObject):
    """Refits the card size when the grid's viewport changes width."""

    def __init__(self, callback, parent: QObject):
        super().__init__(parent)
        self._callback = callback

    def eventFilter(self, _watched, event) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.Resize:
            self._callback()
        return False


class GalleryTab(QObject):
    """Controller of the Browse Civitai tab inside ``downloads_page.ui``."""

    def __init__(
        self,
        form: QWidget,
        *,
        runner: AsyncRunner,
        download: Callable[[list[civitai.CivitaiVersion]], int],
        state_of: Callable[[gallery.GalleryCard], str],
    ):
        super().__init__(form)
        self.form, self._runner, self._download, self._state_of = form, runner, download, state_of
        self.cards: list[gallery.GalleryCard] = []
        self._cursors: list[str | None] = [None]  # the cursor of each page reached so far; Prev reuses them
        self._page = 0
        self._next: str | None = None
        self._generation = 0
        self._loading = False
        self._loaded = False
        self._pending: set[str] = set()
        self._pixmaps: dict[str, QPixmap] = {}
        self._cache = gallery.ThumbnailCache()
        self._thumb_client = None
        view: QListWidget = form.galleryList
        self._view = view
        view.setViewMode(QListView.ViewMode.IconMode)
        view.setResizeMode(QListView.ResizeMode.Adjust)
        view.setMovement(QListView.Movement.Static)
        view.setUniformItemSizes(True)
        view.setWrapping(True)
        view.setSpacing(0)
        view.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)  # a steady width for the column fit
        view.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        view.setMouseTracking(True)
        view.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self._delegate = CardDelegate(view, self._pixmaps, state_of)
        self._delegate.download_requested.connect(self._download_card)
        view.setItemDelegate(self._delegate)
        self._spinner = _SpinningRing(view.viewport())  # the Images gallery's ring, shown while Civitai answers
        self._spinner.hide()
        self._resized = _Resized(self._fit, view)
        view.viewport().installEventFilter(self._resized)
        for sort in gallery.SORTS:
            form.gallerySortCombo.addItem(sort)
        form.galleryFamilyCombo.addItem("All families", None)
        for key, label in gallery.family_choices():
            form.galleryFamilyCombo.addItem(label, key)
        for period, label in zip(
            gallery.PERIODS, ("All time", "This year", "This month", "This week", "Today"), strict=True
        ):
            form.galleryPeriodCombo.addItem(label, period)
        form.galleryFamilyCombo.currentIndexChanged.connect(lambda _index: self.search())
        form.galleryPeriodCombo.currentIndexChanged.connect(lambda _index: self.search())
        form.gallerySearchEdit.returnPressed.connect(self.search)
        form.gallerySortCombo.currentIndexChanged.connect(lambda _index: self.search())
        form.galleryPrevButton.clicked.connect(lambda: self._go(self._page - 1))
        form.galleryNextButton.clicked.connect(lambda: self._go(self._page + 1))
        form.galleryDownloadButton.clicked.connect(self.download_selected)
        view.itemSelectionChanged.connect(self._selection_changed)
        form.galleryNote.hide()
        self._selection_changed()
        runner.shutdown_started.connect(self.shutdown)

    def _fit(self) -> None:
        """Whole columns across the width: four at the design's width, more on a wide window, never under two."""
        viewport = self._view.viewport()
        self._spinner.move(
            (viewport.width() - self._spinner.width()) // 2, (viewport.height() - self._spinner.height()) // 2
        )
        width = viewport.width()
        columns = max(MIN_COLUMNS, round(width / (CARD + GAP)))
        cell = max(120, width // columns)
        if cell == self._delegate.side + GAP:
            return
        self._delegate.side = cell - GAP
        self._view.setGridSize(QSize(cell, cell))
        for row in range(self._view.count()):
            self._view.item(row).setSizeHint(QSize(cell - GAP, cell - GAP))
        self._view.doItemsLayout()

    # ------------------------------------------------------------------------------------------------ lifecycle
    def ensure_loaded(self) -> None:
        if not self._loaded and not self._loading:
            self.search()

    def shutdown(self) -> None:
        self._generation += 1
        self._spinner.timer.stop()
        if self._thumb_client is not None:
            try:
                self._thumb_client.close()
            except Exception:  # closing is best effort at shutdown
                log.debug("thumbnail client close failed", exc_info=True)
            self._thumb_client = None

    def refresh_states(self) -> None:
        """A download changed state: repaint the cards' buttons and pills."""
        self._view.viewport().update()
        self._selection_changed()

    # ------------------------------------------------------------------------------------------------- listing
    def search(self) -> None:
        """A new search (Enter in the box, or a changed sort or filter): back to page 1."""
        if self._runner.closed:
            return
        self._cursors = [None]
        self._pixmaps.clear()
        self._cache.clear()
        self.form.galleryNote.hide()
        self._go(0)

    def _go(self, page: int) -> None:
        if self._runner.closed or page < 0 or page >= len(self._cursors):
            return
        self._generation += 1
        self._loaded = True
        self._page = page
        self._next = None
        self.cards.clear()
        self._pending.clear()
        self._view.clear()
        self._fetch()

    def _busy(self, busy: bool) -> None:
        self._loading = busy
        if busy:
            self._fit()  # centres the ring
            self._spinner.show()
            self._spinner.raise_()
            self._spinner.timer.start()
        else:
            self._spinner.timer.stop()
            self._spinner.hide()

    def _fetch(self) -> None:
        self._busy(True)
        generation = self._generation
        query = self.form.gallerySearchEdit.text()
        sort = self.form.gallerySortCombo.currentText()
        family = self.form.galleryFamilyCombo.currentData()
        period = self.form.galleryPeriodCombo.currentData()
        cursor = self._cursors[self._page]
        self.form.galleryStatus.setText("Loading…")
        self.form.galleryPrevButton.setEnabled(False)
        self.form.galleryNextButton.setEnabled(False)
        self.form.galleryPageLabel.setText(f"page {self._page + 1}")

        async def work():
            def blocking():
                client = civitai.new_client()
                try:
                    return gallery.list_page(client, query, sort, cursor, family=family, period=period)
                finally:
                    client.close()

            return await asyncio.to_thread(blocking)

        self._runner.submit(work(), lambda result, failure: self._listed(generation, result, failure))

    def _listed(self, generation: int, result, failure) -> None:
        if generation != self._generation:
            return  # a newer search or page replaced this one
        self._busy(False)
        self.form.galleryPrevButton.setEnabled(self._page > 0)
        if failure is not None or result is None:
            message = str(failure) if isinstance(failure, civitai.CivitaiError) else "Civitai could not be reached."
            self.form.galleryStatus.setText(f"Could not load the gallery. {message}")
            diagnostic_event(log, "gallery.list_failed", level=logging.WARNING)
            return
        self._next = result.next_cursor
        if self._next and len(self._cursors) == self._page + 1:
            self._cursors.append(self._next)
        self.form.galleryNextButton.setEnabled(self._page + 1 < len(self._cursors))
        side = self._delegate.side
        for card in result.cards:
            self.cards.append(card)
            item = QListWidgetItem()
            item.setData(CARD_ROLE, card)
            item.setSizeHint(QSize(side, side))
            self._view.addItem(item)
            self._load_cover(card)
        self._fit()
        if result.withheld_note:
            self.form.galleryNote.setText(
                "Civitai may withhold mature content in your region; results can be incomplete."
            )
            self.form.galleryNote.show()
        else:
            self.form.galleryNote.hide()
        count = len(self.cards)
        self.form.galleryStatus.setText(f"{count} LoRAs on this page." if count else "No supported LoRAs matched.")
        diagnostic_event(log, "gallery.listed", data={"count": len(result.cards)})
        self._selection_changed()

    # ------------------------------------------------------------------------------------------------ covers
    def _load_cover(self, card: gallery.GalleryCard) -> None:
        url = card.cover_url
        if not url or url in self._pixmaps or url in self._pending or self._runner.closed:
            return
        self._pending.add(url)
        generation = self._generation
        if self._thumb_client is None:
            self._thumb_client = civitai.new_client()
        client, cache = self._thumb_client, self._cache

        async def work():
            return await asyncio.to_thread(gallery.fetch_thumbnail, client, url, cache)

        def done(data, failure) -> None:
            self._pending.discard(url)
            if generation != self._generation or failure is not None or not data:
                return
            image = QImage.fromData(data)
            if image.isNull():
                return
            scaled = image.scaled(
                SOURCE,
                SOURCE,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            crop = scaled.copy((scaled.width() - SOURCE) // 2, (scaled.height() - SOURCE) // 2, SOURCE, SOURCE)
            pixmap = QPixmap.fromImage(crop)
            self._pixmaps[url] = pixmap
            self._view.viewport().update()

        self._runner.submit(work(), done)

    # --------------------------------------------------------------------------------------------- downloading
    def _selected_cards(self) -> list[gallery.GalleryCard]:
        cards = []
        for item in self._view.selectedItems():
            card = item.data(CARD_ROLE)
            if isinstance(card, gallery.GalleryCard):
                cards.append(card)
        return cards

    def _selection_changed(self) -> None:
        ready = [card for card in self._selected_cards() if card.downloadable and not self._state_of(card)]
        button = self.form.galleryDownloadButton
        button.setText(f"Download selected ({len(ready)})" if ready else "Download selected")
        button.setEnabled(bool(ready) and not self._runner.closed)
        button.setToolTip(
            "Download each selected card's shown version, then convert automatically."
            if ready
            else "Click cards to select them."
        )

    def download_selected(self) -> None:
        """Each selected card's shown version (its newest supported one)."""
        ready = [card for card in self._selected_cards() if card.downloadable and not self._state_of(card)]
        if ready:
            self._start([card.version for card in ready if card.version is not None])
            self._view.clearSelection()

    def _download_card(self, card: gallery.GalleryCard) -> None:
        """A card's Download: directly with one supported version, otherwise after choosing one from a short list."""
        if len(card.options) <= 1:
            if card.version is not None:
                self._start([card.version])
            return
        menu = QMenu(self._view)
        for option in card.options:
            action = menu.addAction(option.label)
            action.setData(option)
        chosen = menu.exec(QCursor.pos())
        option = chosen.data() if chosen is not None else None
        if isinstance(option, gallery.VersionOption):
            self._start([option.version])

    def _start(self, versions: list[civitai.CivitaiVersion]) -> None:
        started = self._download(versions)
        diagnostic_event(log, "gallery.download", data={"count": started})
        self.refresh_states()
