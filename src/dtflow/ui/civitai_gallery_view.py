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
from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QListView,
    QListWidget,
    QListWidgetItem,
    QStyle,
    QStyledItemDelegate,
    QWidget,
)

from dtflow.diagnostics import event as diagnostic_event
from dtflow.services import civitai
from dtflow.services import civitai_gallery as gallery
from dtflow.ui.runner import AsyncRunner

log = logging.getLogger(__name__)

CARD = 200
GAP = 8
BAND = 58
LOAD_MORE_MARGIN = 80
CARD_ROLE = Qt.ItemDataRole.UserRole


def compact(value: int | None) -> str:
    if value is None:
        return "–"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1000:
        return f"{value / 1000:.1f}k" if value < 10_000 else f"{value // 1000}k"
    return str(value)


def size_text(size: int | None) -> str:
    if not size:
        return ""
    return f"{size / 1024**3:.1f} GB" if size >= 1024**3 else f"{max(1, round(size / 1024**2))} MB"


class CardDelegate(QStyledItemDelegate):
    """Paints a square cover with overlays: chips top right, badges top left, details on a band at the bottom, and a
    Download button (or the card's download state) above the band."""

    download_requested = Signal(object)

    def __init__(self, parent: QObject, pixmaps: dict[str, QPixmap], state_of: Callable[[gallery.GalleryCard], str]):
        super().__init__(parent)
        self._pixmaps, self._state_of = pixmaps, state_of

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        return QSize(CARD, CARD)

    @staticmethod
    def button_rect(rect: QRect) -> QRect:
        return QRect(rect.right() - 84, rect.bottom() - BAND - 30, 78, 24)

    def editorEvent(self, event, model, option, index) -> bool:  # noqa: N802
        card = index.data(CARD_ROLE)
        if (
            event.type() == QEvent.Type.MouseButtonPress
            and event.button() == Qt.MouseButton.LeftButton
            and isinstance(card, gallery.GalleryCard)
            and card.downloadable
            and not self._state_of(card)
            and self.button_rect(option.rect).contains(event.position().toPoint())
        ):
            self.download_requested.emit(card)
            return True  # consumed: the click starts a download and does not toggle the selection
        return False

    def paint(self, painter: QPainter, option, index) -> None:
        card = index.data(CARD_ROLE)
        if not isinstance(card, gallery.GalleryCard):
            return
        rect = option.rect.adjusted(2, 2, -2, -2)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setClipRect(rect)
        pixmap = self._pixmaps.get(card.cover_url or "")
        if pixmap is not None and not pixmap.isNull():
            painter.drawPixmap(rect.topLeft(), pixmap)
        else:
            painter.fillRect(rect, QColor("#262b33"))
            painter.setPen(QColor("#6f7885"))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "No cover" if not card.cover_url else "Loading…")
        band = QRect(rect.left(), rect.bottom() - BAND + 1, rect.width(), BAND)
        painter.fillRect(band, QColor(10, 12, 16, 215))
        small = QFont(option.font)
        small.setPointSizeF(max(7.0, option.font.pointSizeF() - 1))
        bold = QFont(option.font)
        bold.setBold(True)
        painter.setPen(QColor("#f2f4f7"))
        painter.setFont(bold)
        inner = band.adjusted(7, 4, -7, 0)
        painter.drawText(
            QRect(inner.left(), inner.top(), inner.width(), 18),
            Qt.AlignmentFlag.AlignVCenter,
            QFontMetrics(bold).elidedText(card.name, Qt.TextElideMode.ElideRight, inner.width()),
        )
        painter.setFont(small)
        painter.setPen(QColor("#b4bcc8"))
        by = f"by {card.creator}" if card.creator else ""
        line = "  ·  ".join(part for part in (by, size_text(card.size_bytes)) if part)
        painter.drawText(
            QRect(inner.left(), inner.top() + 18, inner.width(), 16),
            Qt.AlignmentFlag.AlignVCenter,
            QFontMetrics(small).elidedText(line, Qt.TextElideMode.ElideRight, inner.width()),
        )
        painter.drawText(
            QRect(inner.left(), inner.top() + 34, inner.width(), 16),
            Qt.AlignmentFlag.AlignVCenter,
            f"↓ {compact(card.downloads)}    ▲ {compact(card.thumbs_up)}",
        )
        # Chips and badges.
        painter.setFont(small)
        y = rect.top() + 6
        chips = [card.family]
        if card.base_model and card.base_model.casefold() != card.family.casefold():
            chips.append(card.base_model)
        for text in chips:
            y = self._chip(painter, text, rect.right() - 6, y, QColor(36, 99, 160, 230), right=True)
        y = rect.top() + 6
        badge = card.nsfw_label
        if badge:
            y = self._chip(painter, badge, rect.left() + 6, y, QColor(150, 40, 40, 230), right=False)
        if card.availability and card.availability.casefold() != "public":
            self._chip(painter, card.availability, rect.left() + 6, y, QColor(150, 105, 20, 230), right=False)
        # Download button or the card's state.
        state = self._state_of(card)
        action = self.button_rect(option.rect)
        if card.downloadable and not state:
            painter.setPen(QPen(QColor("#9cc4f0"), 1))
            painter.setBrush(QColor(36, 99, 160, 235))
            painter.drawRoundedRect(action, 4, 4)
            painter.setPen(QColor("#ffffff"))
            painter.drawText(action, Qt.AlignmentFlag.AlignCenter, "↓ Download")
        elif state or card.problem:
            text = state or "Unavailable"
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(20, 24, 30, 225))
            width = QFontMetrics(small).horizontalAdvance(text) + 14
            pill = QRect(rect.right() - width - 6, action.top(), width, 24)
            painter.drawRoundedRect(pill, 4, 4)
            painter.setPen(QColor("#dfe5ee"))
            painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, text)
        painter.setClipping(False)
        if selected:
            painter.fillRect(rect, QColor(80, 150, 230, 55))
            painter.setPen(QPen(option.palette.highlight().color().lighter(135), 3))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(rect.adjusted(1, 1, -2, -2))
            painter.setPen(QColor("#ffffff"))
            painter.setBrush(QColor(36, 99, 160))
            tick = QRect(rect.left() + 8, rect.bottom() - BAND - 30, 22, 22)
            painter.drawEllipse(tick)
            painter.drawText(tick, Qt.AlignmentFlag.AlignCenter, "✓")
        painter.restore()

    @staticmethod
    def _chip(painter: QPainter, text: str, edge: int, y: int, fill: QColor, *, right: bool) -> int:
        metrics = painter.fontMetrics()
        width = min(metrics.horizontalAdvance(text) + 12, CARD - 20)
        box = QRect(edge - width if right else edge, y, width, 18)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawRoundedRect(box, 4, 4)
        painter.setPen(QColor("#ffffff"))
        painter.drawText(
            box, Qt.AlignmentFlag.AlignCenter, metrics.elidedText(text, Qt.TextElideMode.ElideRight, width - 8)
        )
        return y + 22


class GalleryTab(QObject):
    """Controller of the Browse Civitai tab inside ``downloads_page.ui``."""

    def __init__(
        self,
        form: QWidget,
        *,
        runner: AsyncRunner,
        download: Callable[[list[gallery.GalleryCard]], int],
        state_of: Callable[[gallery.GalleryCard], str],
    ):
        super().__init__(form)
        self.form, self._runner, self._download, self._state_of = form, runner, download, state_of
        self.cards: list[gallery.GalleryCard] = []
        self._cursor: str | None = None
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
        view.setSpacing(GAP)
        view.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        view.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        view.setMouseTracking(True)
        self._delegate = CardDelegate(view, self._pixmaps, state_of)
        self._delegate.download_requested.connect(lambda card: self._download_cards([card]))
        view.setItemDelegate(self._delegate)
        for sort in gallery.SORTS:
            form.gallerySortCombo.addItem(sort)
        form.gallerySearchButton.clicked.connect(self.search)
        form.gallerySearchEdit.returnPressed.connect(self.search)
        form.gallerySortCombo.currentIndexChanged.connect(lambda _index: self.search())
        form.galleryMoreButton.clicked.connect(self.load_more)
        form.galleryDownloadButton.clicked.connect(self.download_selected)
        view.itemSelectionChanged.connect(self._selection_changed)
        view.verticalScrollBar().valueChanged.connect(self._scrolled)
        form.galleryNote.hide()
        self._selection_changed()
        runner.shutdown_started.connect(self.shutdown)

    # ------------------------------------------------------------------------------------------------ lifecycle
    def ensure_loaded(self) -> None:
        if not self._loaded and not self._loading:
            self.search()

    def shutdown(self) -> None:
        self._generation += 1
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
        if self._runner.closed:
            return
        self._generation += 1
        self._loaded = True
        self._loading = False
        self._cursor = None
        self.cards.clear()
        self._pixmaps.clear()
        self._pending.clear()
        self._cache.clear()
        self._view.clear()
        self.form.galleryNote.hide()
        self._fetch()

    def load_more(self) -> None:
        if self._cursor and not self._loading and not self._runner.closed:
            self._fetch()

    def _scrolled(self, value: int) -> None:
        if self._cursor and not self._loading and value >= self._view.verticalScrollBar().maximum() - LOAD_MORE_MARGIN:
            self.load_more()

    def _fetch(self) -> None:
        self._loading = True
        generation = self._generation
        query = self.form.gallerySearchEdit.text()
        sort = self.form.gallerySortCombo.currentText()
        cursor = self._cursor
        self.form.galleryStatus.setText("Loading…")
        self.form.galleryMoreButton.setEnabled(False)

        async def work():
            def blocking():
                client = civitai.new_client()
                try:
                    return gallery.list_page(client, query, sort, cursor)
                finally:
                    client.close()

            return await asyncio.to_thread(blocking)

        self._runner.submit(work(), lambda result, failure: self._listed(generation, result, failure))

    def _listed(self, generation: int, result, failure) -> None:
        if generation != self._generation:
            return  # a newer search replaced this one
        self._loading = False
        if failure is not None or result is None:
            message = str(failure) if isinstance(failure, civitai.CivitaiError) else "Civitai could not be reached."
            self.form.galleryStatus.setText(f"Could not load the gallery. {message}")
            diagnostic_event(log, "gallery.list_failed", level=logging.WARNING)
            self.form.galleryMoreButton.setEnabled(self._cursor is not None)
            return
        self._cursor = result.next_cursor
        known = {card.version_id for card in self.cards}
        for card in result.cards:
            if card.version_id in known:
                continue
            known.add(card.version_id)
            self.cards.append(card)
            item = QListWidgetItem()
            item.setData(CARD_ROLE, card)
            item.setSizeHint(QSize(CARD, CARD))
            item.setToolTip(card.tooltip())
            self._view.addItem(item)
            self._load_cover(card)
        if result.withheld_note:
            self.form.galleryNote.setText(
                "These results are all general-audience. Civitai ignores the request for mature content in some "
                "regions, so it may be withheld here."
            )
            self.form.galleryNote.show()
        self.form.galleryMoreButton.setEnabled(self._cursor is not None)
        count = len(self.cards)
        if not count:
            self.form.galleryStatus.setText("No supported LoRAs matched.")
        else:
            more = "" if self._cursor is None else " Scroll or press Load more for more."
            self.form.galleryStatus.setText(f"{count} supported LoRAs shown.{more}")
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
            ratio = self._view.devicePixelRatioF()
            side = round((CARD - 4) * ratio)
            scaled = image.scaled(
                side, side, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation
            )
            crop = scaled.copy((scaled.width() - side) // 2, (scaled.height() - side) // 2, side, side)
            pixmap = QPixmap.fromImage(crop)
            pixmap.setDevicePixelRatio(ratio)
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
        button.setToolTip("Download, then convert automatically." if ready else "Click cards to select them.")

    def download_selected(self) -> None:
        ready = [card for card in self._selected_cards() if card.downloadable and not self._state_of(card)]
        if ready:
            self._download_cards(ready)
            self._view.clearSelection()

    def _download_cards(self, cards: list[gallery.GalleryCard]) -> None:
        started = self._download(cards)
        diagnostic_event(log, "gallery.download", data={"count": started})
        self.refresh_states()
