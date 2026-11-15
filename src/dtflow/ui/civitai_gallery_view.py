"""The Browse Civitai tab of the Downloads view (#466, epic #327).

Presentation and wiring only. Listing and thumbnails live in ``dtflow.services.civitai_gallery`` and run off the GUI
thread; Download hands cards to the Downloads page, which uses the same download-and-convert path as Links / IDs.
Covers and listing metadata stay in memory while the gallery is open; decoded pixmaps use a bounded LRU.
"""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from collections.abc import Callable

from PySide6.QtCore import QEvent, QObject, QRect, QSize, Qt, Signal, QTimer
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
    QCheckBox,
    QHeaderView,
    QLabel,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QScrollArea,
    QStyle,
    QStyledItemDelegate,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
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
MAX_PIXMAPS = 180  # decoded square covers (encoded thumbnails have their own bounded LRU)

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
    """Paint one model card, its supported-family chips and a separate per-card Download action."""

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
        return QRect(rect.right() - 108, rect.bottom() - 69, 100, 24)

    def editorEvent(self, event, model, option, index) -> bool:  # noqa: N802
        card = index.data(CARD_ROLE)
        if (
            event.type() == QEvent.Type.MouseButtonPress
            and event.button() == Qt.MouseButton.LeftButton
            and isinstance(card, gallery.GalleryCard)
            and card.downloadable
            and bool(option.state & QStyle.StateFlag.State_MouseOver)
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
            f"{len({option.version.version_id for option in card.options})} versions" if card.options else "",
        ]
        painter.drawText(
            QRect(inner.left(), inner.top() + 18, inner.width(), 16),
            Qt.AlignmentFlag.AlignVCenter,
            QFontMetrics(small).elidedText(
                "   ".join(part for part in parts if part), Qt.TextElideMode.ElideRight, inner.width()
            ),
        )
        # A model can have versions for different Civitai base models and converter families.
        painter.setFont(tiny)
        y = rect.top() + 6
        for family in card.families:
            y = self._chip(painter, family, rect, y, SELECTION, SELECTION_BORDER, SELECTION_TEXT, right=True)
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
        # Download remains available for other versions even if one version is already in the LoRA store.
        state = self._state_of(card)
        painter.setFont(small)
        if state or (hovered and card.problem and not card.downloadable):
            label = state or "Unavailable"
            width = min(QFontMetrics(small).horizontalAdvance(label) + 14, rect.width() - 16)
            pill = QRect(rect.right() - width - 8, rect.bottom() - 98, width, 22)
            painter.setPen(QPen(QColor(255, 255, 255, 46), 1))
            painter.setBrush(QColor(21, 21, 21, 199))
            painter.drawRoundedRect(pill, 3, 3)
            painter.setPen(TEXT)
            painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, QFontMetrics(small).elidedText(label, Qt.TextElideMode.ElideRight, width - 8))
        if hovered and card.downloadable:
            action = self.button_rect(rect)
            painter.setPen(QPen(ACCENT_HOVER, 1))
            painter.setBrush(ACCENT)
            painter.drawRoundedRect(action, 3, 3)
            painter.setPen(QColor("#ffffff"))
            painter.drawText(action, Qt.AlignmentFlag.AlignCenter, "Download ▾")
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


class _CardActionFilter(QObject):
    """Consume a direct Download click before QListWidget can change card selection."""

    def __init__(self, view: QListWidget, on_download: Callable[[gallery.GalleryCard], None]):
        super().__init__(view.viewport())
        self._view = view
        self._on_download = on_download

    def eventFilter(self, _watched, event) -> bool:  # noqa: N802
        if event.type() != QEvent.Type.MouseButtonPress or event.button() != Qt.MouseButton.LeftButton:
            return False
        position = event.position().toPoint()
        item = self._view.itemAt(position)
        if item is None:
            return False
        card = item.data(CARD_ROLE)
        rect = self._view.visualItemRect(item).adjusted(1, 1, -1, -1)
        if isinstance(card, gallery.GalleryCard) and card.downloadable and CardDelegate.button_rect(rect).contains(position):
            self._on_download(card)
            return True
        return False


class GalleryTab(QObject):
    """Continuous-scroll Civitai model gallery and explicit version/file selection."""

    def __init__(
        self,
        form: QWidget,
        *,
        runner: AsyncRunner,
        download: Callable[[list[civitai.CivitaiVersion]], int],
        state_of: Callable[[gallery.GalleryCard], str],
        version_state: Callable[[civitai.CivitaiVersion], str],
    ):
        super().__init__(form)
        self.form, self._runner, self._download = form, runner, download
        self._state_of, self._version_state = state_of, version_state
        self.cards: list[gallery.GalleryCard] = []
        self._cursor: str | None = None
        self._seen_cursors: set[str] = set()
        self._has_more = True
        self._empty_batches = 0
        self._paused_empty = False
        self._generation = 0
        self._loading = False
        self._loaded = False
        self._pending: set[str] = set()
        self._pixmaps: OrderedDict[str, QPixmap] = OrderedDict()
        self._cache = gallery.ThumbnailCache()
        self._thumb_client = None
        self._bulk_rows: list[tuple[gallery.GalleryCard, gallery.VersionOption]] = []
        self._bulk_selected: set[tuple[int, int | None]] = set()
        self._building_table = False

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
        view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        view.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        view.setMouseTracking(True)
        view.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self._delegate = CardDelegate(view, self._pixmaps, state_of)
        self._delegate.download_requested.connect(self._download_card)
        view.setItemDelegate(self._delegate)
        self._card_action_filter = _CardActionFilter(view, self._download_card)
        view.viewport().installEventFilter(self._card_action_filter)
        self._spinner = _SpinningRing(view.viewport())
        self._spinner.hide()
        self._resized = _Resized(self._fit, view)
        view.viewport().installEventFilter(self._resized)
        view.verticalScrollBar().valueChanged.connect(self._on_scroll)

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
        form.galleryDownloadButton.clicked.connect(self.download_selected)
        form.galleryContinueButton.clicked.connect(self._continue_after_empty)
        form.galleryContinueButton.hide()
        view.itemSelectionChanged.connect(self._selection_changed)

        table = form.galleryVersionsTable
        table.verticalHeader().hide()
        table.verticalHeader().setDefaultSectionSize(28)
        table.setColumnWidth(0, 32)
        table.setColumnWidth(2, 135)
        table.setColumnWidth(3, 115)
        table.setColumnWidth(4, 82)
        table.setColumnWidth(5, 175)
        table.horizontalHeader().setStretchLastSection(False)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        table.itemChanged.connect(self._bulk_changed)
        form.gallerySelectAllVersions.clicked.connect(self._select_all_versions)
        form.galleryClearVersions.clicked.connect(self._clear_versions)
        form.galleryVersionsBox.hide()
        form.galleryNote.hide()
        self._selection_changed()
        runner.shutdown_started.connect(self.shutdown)

    def _fit(self) -> None:
        """Fit square cells across the viewport, retaining the original compact gallery density."""
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
        self._load_visible_covers()

    def ensure_loaded(self) -> None:
        if not self._loaded and not self._loading:
            self.search()

    def shutdown(self) -> None:
        self._generation += 1
        self._spinner.timer.stop()
        if self._thumb_client is not None:
            try:
                self._thumb_client.close()
            except Exception:
                log.debug("thumbnail client close failed", exc_info=True)
            self._thumb_client = None

    def refresh_states(self) -> None:
        """Repaint exact version states after any download state change."""
        self._view.viewport().update()
        self._selection_changed()

    # ---------------------------------------------------------------------------------------- continuous listing
    def search(self) -> None:
        """Start a new continuous result stream. Existing downloaded files are unaffected."""
        if self._runner.closed:
            return
        self._generation += 1
        self._cursor = None
        self._seen_cursors.clear()
        self._has_more = True
        self._empty_batches = 0
        self._paused_empty = False
        self.form.galleryContinueButton.setText("Search further")
        self.form.galleryContinueButton.hide()
        self._loaded = True
        self.cards.clear()
        self._pending.clear()
        self._view.clear()
        self._bulk_selected.clear()
        self._selection_changed()
        self.form.galleryNote.hide()
        # Keep bounded cover caches across searches; no Civitai image is written to disk.
        self._busy(False)
        self._fetch()

    def _busy(self, busy: bool) -> None:
        self._loading = busy
        if busy and not self.cards:
            self._fit()
            self._spinner.show()
            self._spinner.raise_()
            self._spinner.timer.start()
        else:
            self._spinner.timer.stop()
            self._spinner.hide()

    def _fetch(self) -> None:
        """Fetch the next cursor (100 source models). Only scrolling or an unfilled viewport advances."""
        if self._runner.closed or self._loading or not self._has_more:
            return
        self._busy(True)
        generation = self._generation
        query = self.form.gallerySearchEdit.text()
        sort = self.form.gallerySortCombo.currentText()
        family = self.form.galleryFamilyCombo.currentData()
        period = self.form.galleryPeriodCombo.currentData()
        cursor = self._cursor
        self.form.galleryStatus.setText(
            f"{len(self.cards)} LoRAs loaded · Loading more…" if self.cards else "Loading Civitai LoRAs…"
        )

        async def work():
            def blocking():
                client = civitai.new_client()
                try:
                    return gallery.list_page(client, query, sort, cursor, family=family, period=period)
                finally:
                    client.close()

            return await asyncio.to_thread(blocking)

        self._runner.submit(work(), lambda result, failure: self._listed(generation, cursor, result, failure))

    def _listed(self, generation: int, cursor: str | None, result, failure) -> None:
        if generation != self._generation:
            return
        self._busy(False)
        if failure is not None or result is None:
            message = str(failure) if isinstance(failure, civitai.CivitaiError) else "Civitai could not be reached."
            self.form.galleryStatus.setText(f"Could not load more LoRAs. {message}")
            self._paused_empty = True
            self.form.galleryContinueButton.setText("Retry")
            self.form.galleryContinueButton.show()
            diagnostic_event(log, "gallery.list_failed", level=logging.WARNING)
            return
        if cursor is not None:
            self._seen_cursors.add(cursor)
        new_cursor = result.next_cursor
        self._has_more = bool(new_cursor and new_cursor not in self._seen_cursors and new_cursor != cursor)
        self._cursor = new_cursor if self._has_more else None
        existing = {card.model_id for card in self.cards}
        for card in result.cards:
            if card.model_id in existing:
                continue
            existing.add(card.model_id)
            self.cards.append(card)
            item = QListWidgetItem()
            item.setData(CARD_ROLE, card)
            item.setSizeHint(QSize(self._delegate.side, self._delegate.side))
            self._view.addItem(item)
        self._fit()
        self._load_visible_covers()
        if result.withheld_note:
            self.form.galleryNote.setText(
                "Civitai may withhold mature content in your region; results can be incomplete."
            )
            self.form.galleryNote.show()
        count = len(self.cards)
        suffix = " · Scroll for more" if self._has_more else " · End of results"
        self.form.galleryStatus.setText(f"{count} LoRAs loaded{suffix}" if count else "No supported LoRAs matched.")
        diagnostic_event(log, "gallery.listed", data={"count": len(result.cards), "total": count})
        self._empty_batches = 0 if result.cards else self._empty_batches + 1
        # Never spin forever over empty or filtered-out API pages without user input.
        if self._empty_batches >= 3 and self._has_more:
            self._paused_empty = True
            self.form.galleryContinueButton.setText("Search further")
            self.form.galleryContinueButton.show()
            self.form.galleryStatus.setText(
                f"{count} LoRAs loaded. No new supported models in recent Civitai batches."
            )
        self._selection_changed()
        QTimer.singleShot(0, self._maybe_more)

    def _continue_after_empty(self) -> None:
        """Explicitly resume a rare run of empty filtered batches without creating infinite requests."""
        self._empty_batches = 0
        self._paused_empty = False
        self.form.galleryContinueButton.hide()
        self._fetch()

    def _on_scroll(self, *_args) -> None:
        self._load_visible_covers()
        self._maybe_more()

    def _maybe_more(self) -> None:
        if self._loading or self._paused_empty or not self._has_more or self._runner.closed:
            return
        bar = self._view.verticalScrollBar()
        if bar.maximum() <= 0 or bar.value() >= bar.maximum() - max(bar.pageStep() * 2, 600):
            self._fetch()

    # ------------------------------------------------------------------------------------- lazy bounded covers
    def _load_visible_covers(self) -> None:
        """Fetch covers near the viewport, rather than scheduling hundreds at once."""
        count = self._view.count()
        if not count or self._runner.closed:
            return
        view = self._view
        cell = max(1, self._delegate.side + GAP)
        columns = max(1, view.viewport().width() // cell)
        start = min(count - 1, max(0, (view.verticalScrollBar().value() // cell) * columns))
        first = max(0, start - columns)
        rows_visible = max(1, view.viewport().height() // cell + 3)
        last = min(count, start + rows_visible * columns)
        for row in range(first, last):
            card = view.item(row).data(CARD_ROLE)
            if isinstance(card, gallery.GalleryCard):
                self._load_cover(card)

    def _load_cover(self, card: gallery.GalleryCard) -> None:
        url = card.cover_url
        if not url or url in self._pending or self._runner.closed:
            return
        if url in self._pixmaps:
            self._pixmaps.move_to_end(url)
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
                SOURCE, SOURCE, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            crop = scaled.copy((scaled.width() - SOURCE) // 2, (scaled.height() - SOURCE) // 2, SOURCE, SOURCE)
            self._pixmaps[url] = QPixmap.fromImage(crop)
            self._pixmaps.move_to_end(url)
            while len(self._pixmaps) > MAX_PIXMAPS:
                self._pixmaps.popitem(last=False)
            self._view.viewport().update()

        self._runner.submit(work(), done)

    # ------------------------------------------------------------------------------------------- bulk selection
    def _selected_cards(self) -> list[gallery.GalleryCard]:
        return [
            card for item in self._view.selectedItems()
            if isinstance((card := item.data(CARD_ROLE)), gallery.GalleryCard)
        ]

    def _selection_changed(self) -> None:
        """Offer all exact versions of selected models; do not assume the newest version was chosen."""
        chosen: list[tuple[gallery.GalleryCard, gallery.VersionOption]] = []
        seen: set[tuple[int, int | None]] = set()
        for card in self._selected_cards():
            for option in card.options:
                if option.key not in seen:
                    chosen.append((card, option))
                    seen.add(option.key)
        self._bulk_rows = chosen
        self._bulk_selected.intersection_update(seen)
        table = self.form.galleryVersionsTable
        self._building_table = True
        table.blockSignals(True)
        try:
            table.setRowCount(len(chosen))
            for row, (card, option) in enumerate(chosen):
                version = option.version
                state = self._version_state(version)
                available = state not in ("Queued", "Downloading", "Converting", "In your LoRAs")
                tick = QTableWidgetItem()
                flags = Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled if available else Qt.ItemFlag.NoItemFlags
                tick.setFlags(flags)
                tick.setCheckState(
                    Qt.CheckState.Checked if available and option.key in self._bulk_selected else Qt.CheckState.Unchecked
                )
                table.setItem(row, 0, tick)
                cells = (
                    f"{card.name} · {version.version_name or 'Version'}",
                    version.base_model or "Unknown",
                    option.family,
                    str(version.version_id),
                    version.file.name,
                )
                for col, content in enumerate(cells, 1):
                    cell = QTableWidgetItem(content)
                    cell.setToolTip(
                        f"Civitai version {version.version_id} · file {version.file.id} · {state or 'Ready'}"
                    )
                    table.setItem(row, col, cell)
            self.form.galleryVersionsHeading.setText(
                f"Versions from selected models ({len(chosen)})"
            )
        finally:
            table.blockSignals(False)
            self._building_table = False
        self.form.galleryVersionsBox.setVisible(bool(chosen))
        self._bulk_buttons()

    def _bulk_changed(self, item: QTableWidgetItem) -> None:
        if self._building_table or item.column() != 0 or item.row() >= len(self._bulk_rows):
            return
        key = self._bulk_rows[item.row()][1].key
        if item.checkState() == Qt.CheckState.Checked:
            self._bulk_selected.add(key)
        else:
            self._bulk_selected.discard(key)
        self._bulk_buttons()

    def _select_all_versions(self) -> None:
        self._bulk_selected = {
            option.key for _card, option in self._bulk_rows
            if self._version_state(option.version) not in ("Queued", "Downloading", "Converting", "In your LoRAs")
        }
        self._selection_changed()

    def _clear_versions(self) -> None:
        self._bulk_selected.clear()
        self._selection_changed()

    def _bulk_buttons(self) -> None:
        ready = [
            option for _card, option in self._bulk_rows
            if option.key in self._bulk_selected
            and self._version_state(option.version) not in ("Queued", "Downloading", "Converting", "In your LoRAs")
        ]
        button = self.form.galleryDownloadButton
        button.setText(f"Download selected ({len(ready)})" if ready else "Download selected")
        button.setEnabled(bool(ready) and not self._runner.closed)
        self.form.galleryStatus.setToolTip(
            "Choose exact Civitai versions in the table, then Download selected starts immediately."
        )

    def download_selected(self) -> None:
        """Immediately download all explicitly checked concrete version/file choices."""
        ready = [
            option.version for _card, option in self._bulk_rows
            if option.key in self._bulk_selected
            and self._version_state(option.version) not in ("Queued", "Downloading", "Converting", "In your LoRAs")
        ]
        if ready:
            self._start(ready)
            self._bulk_selected.clear()
            self._view.clearSelection()

    # ------------------------------------------------------------------------------------------ per-card dropdown
    def _download_card(self, card: gallery.GalleryCard) -> None:
        """Checkbox dropdown: several versions/files can be downloaded with one explicit action."""
        menu = QMenu(self._view)
        panel = QWidget(menu)
        panel.setMinimumWidth(400)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)
        title = QLabel(f"Choose versions · {card.name}", panel)
        title.setWordWrap(True)
        layout.addWidget(title)
        scroll = QScrollArea(panel)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setMaximumHeight(300)
        scroll.setMinimumHeight(min(280, max(70, len(card.options) * 55)))
        rows_panel = QWidget(scroll)
        rows = QVBoxLayout(rows_panel)
        rows.setContentsMargins(2, 2, 2, 2)
        rows.setSpacing(8)
        boxes: list[tuple[QCheckBox, gallery.VersionOption]] = []
        for option in card.options:
            v = option.version
            state = self._version_state(v)
            box = QCheckBox(f"{v.version_name or 'Version'} · {v.base_model or 'Unknown base'}", rows_panel)
            box.setToolTip(
                f"Family: {option.family}\nCivitai version ID: {v.version_id}\nFile: {v.file.name} (ID {v.file.id})"
            )
            box.setEnabled(state not in ("Queued", "Downloading", "Converting", "In your LoRAs"))
            rows.addWidget(box)
            detail = QLabel(
                f"{option.family} · ID {v.version_id} · {v.file.name}" + (f" · {state}" if state else ""), rows_panel
            )
            detail.setWordWrap(True)
            detail.setProperty("role", "secondary")
            rows.addWidget(detail)
            boxes.append((box, option))
        rows.addStretch()
        scroll.setWidget(rows_panel)
        layout.addWidget(scroll)
        submit = QPushButton("Download selected", panel)
        submit.setEnabled(False)
        layout.addWidget(submit)

        def update_button() -> None:
            number = sum(1 for box, _option in boxes if box.isChecked() and box.isEnabled())
            submit.setText(f"Download selected ({number})" if number else "Download selected")
            submit.setEnabled(bool(number))
        for box, _option in boxes:
            box.toggled.connect(update_button)
        chosen: list[civitai.CivitaiVersion] = []

        def accept() -> None:
            chosen.extend(option.version for box, option in boxes if box.isChecked() and box.isEnabled())
            menu.close()
        submit.clicked.connect(accept)
        action = QWidgetAction(menu)
        action.setDefaultWidget(panel)
        menu.addAction(action)
        menu.exec(QCursor.pos())
        if chosen:
            self._start(chosen)

    def _start(self, versions: list[civitai.CivitaiVersion]) -> None:
        """Both download entry points share DownloadsPage's existing queue and converter."""
        started = self._download(versions)
        diagnostic_event(log, "gallery.download", data={"count": started})
        self.refresh_states()
