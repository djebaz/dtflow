"""Production saved-output controller for the existing Grid/List/Detail Images page."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import deque
from dataclasses import asdict, replace
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QIcon, QImage, QImageReader, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QComboBox,
    QFrame,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from dtflow.core.gallery import GalleryItem, GalleryQuery
from dtflow.core.models import ValidationError
from dtflow.persistence.viewer import load_viewer, save_viewer
from dtflow.services.gallery import filter_choices, inspect_output, locate_output, query_outputs
from dtflow.ui.gallery_detail_preview import CoverPainter, RowHoverDelegate, apply_gallery
from dtflow.ui.runner import AsyncRunner

log = logging.getLogger(__name__)

PAGE_SIZE = 80
THUMBNAIL_SIDE = 320
THUMBNAIL_CONCURRENCY = 6
AUTO_REFRESH_MS = 5000
STANDARD_THUMBNAIL_PX = 160
MIN_THUMBNAIL_PERCENT = 25
MAX_THUMBNAIL_PERCENT = 250

SORT_LABELS = {
    "Newest first": "newest",
    "Oldest first": "oldest",
    "Model": "model",
    "Pixel size": "size",
    "LoRAs": "loras",
}
SORT_NAMES = {value: key for key, value in SORT_LABELS.items()}
DIMENSIONS = re.compile(r"^\s*(\d+)\s*[x×]\s*(\d+)\s*$")
DATE_RANGE = re.compile(r"^\s*(\d{4}-\d{2}-\d{2})\s*\.\.\s*(\d{4}-\d{2}-\d{2})\s*$")


def _image(path: str, max_side: int) -> tuple[QImage | None, str]:
    """Decode one image off the GUI thread, optionally asking Qt for a bounded decode."""

    reader = QImageReader(path)
    reader.setAutoTransform(True)
    if max_side:
        source = reader.size()
        if source.isValid() and (source.width() > max_side or source.height() > max_side):
            reader.setScaledSize(source.scaled(max_side, max_side, Qt.AspectRatioMode.KeepAspectRatio))
    value = reader.read()
    if value.isNull():
        return None, reader.errorString() or "Image could not be decoded."
    return value, ""


def _human_size(value: int | None) -> str:
    if value is None:
        return "Unknown"
    amount = float(value)
    for unit in ("B", "KB", "MB", "GB"):
        if amount < 1024 or unit == "GB":
            return f"{amount:.0f} {unit}" if unit in ("B", "KB") else f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{value} B"


def _local_timestamp(value: str) -> str:
    try:
        return datetime.fromisoformat(value).astimezone().strftime("%b %d, %Y\n%H:%M:%S")
    except (TypeError, ValueError):
        return "Not recorded"


def _local_midnight(day: date) -> str:
    # A naive local wall time lets the OS apply the date-specific DST rule before normalization to UTC.
    return datetime.combine(day, time.min).astimezone().astimezone(UTC).isoformat()


def _date_bounds(text: str) -> tuple[str | None, str | None]:
    value = text.strip()
    if not value or value.casefold() == "all time":
        return None, None
    today = datetime.now().astimezone().date()
    lowered = value.casefold()
    if lowered == "today":
        return _local_midnight(today), _local_midnight(today + timedelta(days=1))
    if lowered == "last 7 days":
        return _local_midnight(today - timedelta(days=6)), _local_midnight(today + timedelta(days=1))
    if lowered == "last 30 days":
        return _local_midnight(today - timedelta(days=29)), _local_midnight(today + timedelta(days=1))
    match = DATE_RANGE.match(value)
    try:
        if match:
            first = date.fromisoformat(match.group(1))
            last = date.fromisoformat(match.group(2))
            if last < first:
                raise ValueError
            return _local_midnight(first), _local_midnight(last + timedelta(days=1))
        single = date.fromisoformat(value)
        return _local_midnight(single), _local_midnight(single + timedelta(days=1))
    except ValueError as exc:
        raise ValidationError(
            "Date filter must be All time, Today, Last 7 days, Last 30 days, YYYY-MM-DD, or START..END."
        ) from exc


class GalleryPage(QObject):
    """Bind saved-output services to the existing Designer gallery without owning generation."""

    def __init__(self, page: QWidget, *, runner: AsyncRunner, state_dir: Path):
        super().__init__(page)
        self.page = page
        self.runner = runner
        self.state_dir = Path(state_dir)
        self._active = False
        self._loading = False
        self._pending_refresh: tuple[bool, bool] | None = None
        self._restoring = True
        self._query_generation = 0
        self._image_generation = 0
        self._thumb_generation = 0
        self._thumb_active = 0
        self._thumb_waiting: deque[tuple[int, str, str]] = deque()
        self._save_running = False
        self._save_dirty = False
        self._save_future = None
        self._pending_page_selection: str | None = None
        self._page_scroll_target: str | None = None
        self.items: list[GalleryItem] = []
        self.total = 0
        self.offset = 0
        self.selected_id: str | None = None
        self._origin_identity: str | None = None
        self._grid_buttons: dict[str, QPushButton] = {}
        self._grid_painters: dict[str, CoverPainter] = {}
        self._film_buttons: list[QPushButton] = []
        self._film_painters: list[CoverPainter] = []

        self._presentation_painters = apply_gallery(page, sample=False)
        self.surface_painter = self._presentation_painters[0]
        self.surface_painter.pixmap = QPixmap()
        self.surface = self._widget("galleryImageSurface", QLabel)
        self.surface.setToolTip("Saved output preview")
        self._widget("galleryFixtureOverlay").hide()

        self.stack = self._widget("galleryContentStack")
        self.grid_scroll = self._widget("galleryGridScroll")
        self.grid_cards = self._widget("galleryGridCards")
        self.cards_layout = self.grid_cards.layout()
        self.table = self._widget("galleryListTable", QTableWidget)
        self.grid_viewport = self.grid_scroll.viewport()
        self.table_viewport = self.table.viewport()
        self.slider = self._widget("galleryThumbnailSizeSlider")
        self.film_scroll = self._widget("galleryFilmstripScroll")
        self._scroll = {"grid": 0, "list": 0, "filmstrip": 0}

        self.filter_timer = QTimer(self)
        self.filter_timer.setSingleShot(True)
        self.filter_timer.setInterval(300)
        self.filter_timer.timeout.connect(lambda: self.refresh(reset=True))
        self.reflow_timer = QTimer(self)
        self.reflow_timer.setSingleShot(True)
        self.reflow_timer.timeout.connect(self._layout_cards)
        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.setInterval(250)
        self.save_timer.timeout.connect(self._flush_save)
        self.auto_timer = QTimer(self)
        self.auto_timer.setInterval(AUTO_REFRESH_MS)
        self.auto_timer.timeout.connect(lambda: self.refresh(preserve=True))

        self._setup_filters()
        self._setup_views()
        self._setup_filmstrip()
        self._setup_actions()
        self._restore_preferences()
        self._clear_detail()
        self.show_view(self.mode, persist=False)
        self._sync_auto_refresh()
        self._restoring = False

    def _widget(self, name: str, cls=QWidget):
        value = self.page.findChild(cls, name)
        if value is None:
            raise RuntimeError(f"Gallery form is missing {name}.")
        return value

    def _setup_filters(self) -> None:
        self.prompt = self._widget("galleryPromptSearch", QLineEdit)
        self.model = self._widget("galleryModelFilter", QComboBox)
        self.lora = self._widget("galleryLoRAFilter", QComboBox)
        self.date_filter = self._widget("galleryDateFilter", QComboBox)
        self.dimensions = self._widget("galleryDimensionsFilter", QComboBox)
        self.ratio = self._widget("galleryRatioFilter", QComboBox)
        self.sort = self._widget("gallerySortFilter", QComboBox)

        for control in (
            self.prompt,
            self.model,
            self.lora,
            self.date_filter,
            self.dimensions,
            self.ratio,
            self.sort,
        ):
            control.setEnabled(True)

        for combo in (self.model, self.lora, self.date_filter, self.dimensions, self.ratio):
            combo.setEditable(True)

        self.model.clear()
        self.model.addItem("All models")
        self.lora.clear()
        self.lora.addItem("All LoRAs")
        self.date_filter.clear()
        self.date_filter.addItems(["All time", "Today", "Last 7 days", "Last 30 days"])
        self.dimensions.clear()
        self.dimensions.addItem("Any size")
        self.ratio.clear()
        self.ratio.addItems(["Any ratio", "Square", "Portrait", "Landscape", "1:1", "4:3", "3:2", "16:9"])
        self.sort.clear()
        self.sort.addItems(SORT_LABELS)

        self.prompt.setToolTip("Case-insensitive literal search in recorded positive prompts.")
        self.model.setToolTip("Exact recorded model identifier; the list is a bounded convenience facet.")
        self.lora.setToolTip("Exact recorded LoRA identifier; the list is a bounded convenience facet.")
        self.date_filter.setToolTip("Local calendar date or range. Examples: 2026-10-06 or 2026-10-01..2026-10-06.")
        self.dimensions.setToolTip("Exact dimensions, for example 1024 × 1024.")
        self.ratio.setToolTip("Orientation or exact W:H aspect ratio.")
        self.sort.setToolTip("Deterministic saved-output ordering.")

        self.prompt.textChanged.connect(self._filter_edited)
        for combo in (self.model, self.lora, self.date_filter, self.dimensions, self.ratio, self.sort):
            combo.currentTextChanged.connect(self._filter_edited)

    def _setup_views(self) -> None:
        self.modes: dict[str, QWidget] = {}
        self.mode_buttons = QButtonGroup(self)
        self.mode_buttons.setExclusive(True)
        for mode in ("Grid", "List", "Detail"):
            button = self._widget(f"gallery{mode}Button", QPushButton)
            self.mode_buttons.addButton(button)
            button.clicked.connect(lambda checked=False, name=mode: self.show_view(name))
            self.modes[mode] = self._widget(f"gallery{mode}Content")

        self.table.setColumnCount(8)
        self.table.setHorizontalHeaderLabels(
            [
                "Image",
                "Filename",
                "Model",
                "LoRAs / weights / status",
                "Dimensions",
                "File size",
                "Date / time",
                "Positive prompt",
            ]
        )
        self.table.verticalHeader().hide()
        self.table.setProperty("hoverRow", -1)
        self.table.setItemDelegate(RowHoverDelegate(self.table))
        self.table.setIconSize(QSize(64, 48))
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        for column, width in enumerate((80, 160, 180, 300, 120, 90, 145, 360)):
            self.table.setColumnWidth(column, width)
        self.table.itemClicked.connect(lambda item: self._open_from_list(str(item.data(Qt.ItemDataRole.UserRole))))
        self.table.itemActivated.connect(lambda item: self._open_from_list(str(item.data(Qt.ItemDataRole.UserRole))))

        self.table_viewport.setMouseTracking(True)
        self.table_viewport.installEventFilter(self)
        self.grid_viewport.installEventFilter(self)
        self.slider.valueChanged.connect(self._density_changed)
        self.grid_scroll.verticalScrollBar().valueChanged.connect(lambda value: self._scroll_changed("grid", value))
        self.table.verticalScrollBar().valueChanged.connect(lambda value: self._scroll_changed("list", value))
        self.film_scroll.verticalScrollBar().valueChanged.connect(
            lambda value: self._scroll_changed("filmstrip", value)
        )

        for key, callback in (
            ("Left", lambda: self.step(-1)),
            ("Right", lambda: self.step(1)),
            ("Ctrl+PgUp", lambda: self._change_page(-1)),
            ("Ctrl+PgDown", lambda: self._change_page(1)),
        ):
            shortcut = QShortcut(QKeySequence(key), self.page)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(callback)

    def _setup_filmstrip(self) -> None:
        self._film_buttons = [self._widget(f"galleryThumbnail{i}", QPushButton) for i in range(1, 7)]
        self._film_painters = list(self._presentation_painters[1:7])
        for button in self._film_buttons:
            button.clicked.connect(lambda checked=False, source=button: self.select(str(source.property("identity"))))
            button.hide()

    def _setup_actions(self) -> None:
        self.refresh_button = self._widget("galleryRefreshButton", QPushButton)
        self.auto_button = self._widget("galleryAutoRefreshButton", QPushButton)
        self.refresh_button.setEnabled(True)
        self.refresh_button.setToolTip("Refresh saved outputs while keeping the current browsing anchor when possible.")
        self.refresh_button.clicked.connect(lambda: self.refresh(preserve=True))
        self.auto_button.setEnabled(True)
        self.auto_button.setCheckable(True)
        self.auto_button.toggled.connect(self._auto_refresh_changed)

        self.previous_button = self._widget("galleryPreviousButton", QPushButton)
        self.next_button = self._widget("galleryNextButton", QPushButton)
        self.previous_button.setEnabled(False)
        self.next_button.setEnabled(False)
        self.previous_button.clicked.connect(lambda: self.step(-1))
        self.next_button.clicked.connect(lambda: self.step(1))
        self.copy_button = self._widget("galleryCopyPathButton", QPushButton)
        self.folder_button = self._widget("galleryOpenFolderButton", QPushButton)
        self.external_button = self._widget("galleryExternalViewerButton", QPushButton)
        self.copy_button.clicked.connect(self._copy_path)
        self.folder_button.clicked.connect(self._open_folder)
        self.external_button.clicked.connect(self._open_external)

    def _restore_preferences(self) -> None:
        document = load_viewer(self.state_dir)
        query_doc = document.get("query", {})
        try:
            stored_query = GalleryQuery(**query_doc)
        except (TypeError, ValidationError):
            stored_query = GalleryQuery(limit=PAGE_SIZE)
        self.offset = stored_query.offset
        filters = document.get("filters", {}) if isinstance(document.get("filters"), dict) else {}
        self.prompt.setText(str(filters.get("prompt", stored_query.prompt)))
        self.model.setCurrentText(str(filters.get("model", stored_query.model or "All models")))
        self.lora.setCurrentText(str(filters.get("lora", stored_query.lora or "All LoRAs")))
        self.date_filter.setCurrentText(str(filters.get("date", "All time")))
        dimensions = filters.get("dimensions")
        if dimensions is None and stored_query.width and stored_query.height:
            dimensions = f"{stored_query.width} × {stored_query.height}"
        self.dimensions.setCurrentText(str(dimensions or "Any size"))
        self.ratio.setCurrentText(str(filters.get("ratio", stored_query.ratio or "Any ratio")))
        self.sort.setCurrentText(str(filters.get("sort", SORT_NAMES.get(stored_query.sort, "Newest first"))))
        self.mode = str(document.get("view", "Grid"))
        if self.mode not in self.modes:
            self.mode = "Grid"
        scale_percent = document.get("thumbnail_scale_percent")
        if type(scale_percent) is int and MIN_THUMBNAIL_PERCENT <= scale_percent <= MAX_THUMBNAIL_PERCENT:
            self.slider.setValue(scale_percent)
        else:
            # PR #34 initially persisted the raw pixel size under "density".
            # Preserve those local smoke-test preferences while moving the UI
            # contract to a percentage based on the 160 px standard size.
            legacy_density = document.get("density")
            if type(legacy_density) is int and legacy_density > 0:
                migrated = round(legacy_density * 100 / STANDARD_THUMBNAIL_PX)
                self.slider.setValue(max(MIN_THUMBNAIL_PERCENT, min(MAX_THUMBNAIL_PERCENT, migrated)))
        selected = document.get("selected")
        self.selected_id = selected if isinstance(selected, str) and selected else None
        self.auto_button.setChecked(bool(document.get("auto_refresh", True)))
        scroll = document.get("scroll")
        if isinstance(scroll, dict):
            for name in self._scroll:
                value = scroll.get(name)
                if type(value) is int and value >= 0:
                    self._scroll[name] = value

    def set_active(self, active: bool) -> None:
        first_activation = active and not self._active and not self.items
        self._active = active
        self._sync_auto_refresh()
        if first_activation:
            self.refresh(preserve=True)

    def _sync_auto_refresh(self) -> None:
        enabled = self.auto_button.isChecked()
        self.auto_button.setText(f"●  Auto-refresh: {'On' if enabled else 'Off'}")
        if enabled and self._active:
            self.auto_timer.start()
        else:
            self.auto_timer.stop()

    def _auto_refresh_changed(self, _checked: bool) -> None:
        self._sync_auto_refresh()
        self._schedule_save()

    def _filter_edited(self, *_args) -> None:
        if self._restoring:
            return
        self.filter_timer.start()
        self._schedule_save()

    def _date_query(self) -> tuple[str | None, str | None]:
        return _date_bounds(self.date_filter.currentText())

    def _query(self, *, offset: int | None = None) -> GalleryQuery:
        since, until = self._date_query()
        dimensions_text = self.dimensions.currentText().strip()
        width = height = None
        if dimensions_text and dimensions_text.casefold() != "any size":
            match = DIMENSIONS.match(dimensions_text)
            if not match:
                raise ValidationError("Dimensions must look like WIDTH × HEIGHT.")
            width, height = int(match.group(1)), int(match.group(2))

        ratio_text = self.ratio.currentText().strip()
        ratio = ""
        if ratio_text and ratio_text.casefold() != "any ratio":
            lowered = ratio_text.casefold()
            ratio = lowered if lowered in ("square", "portrait", "landscape") else ratio_text.replace("×", ":")
        model = self.model.currentText().strip()
        lora = self.lora.currentText().strip()
        sort = SORT_LABELS.get(self.sort.currentText(), "newest")
        return GalleryQuery(
            prompt=self.prompt.text(),
            negative="",
            model="" if model.casefold() == "all models" else model,
            lora="" if lora.casefold() == "all loras" else lora,
            since=since,
            until=until,
            ratio=ratio,
            width=width,
            height=height,
            sort=sort,
            offset=self.offset if offset is None else offset,
            limit=PAGE_SIZE,
        )

    def refresh(self, *, reset: bool = False, preserve: bool = True) -> None:
        if self._loading:
            if self._pending_refresh is not None:
                reset = reset or self._pending_refresh[0]
                preserve = preserve and self._pending_refresh[1]
            self._pending_refresh = (reset, preserve)
            return
        self._capture_scroll()
        if reset:
            self.offset = 0
        try:
            query = self._query(offset=self.offset)
        except ValidationError as exc:
            self.refresh_button.setToolTip(str(exc))
            return

        anchor = self._browsing_anchor() if preserve else None
        self._submit_query(query, anchor=anchor)

    def _submit_query(self, query: GalleryQuery, *, anchor: str | None) -> None:
        self._query_generation += 1
        generation = self._query_generation
        self._loading = True
        self.refresh_button.setEnabled(False)

        async def work():
            current = query
            anchor_found = False
            if anchor:
                position = await locate_output(self.state_dir, replace(current, offset=0), anchor)
                if position is not None:
                    anchor_found = True
                    current = replace(current, offset=(position // PAGE_SIZE) * PAGE_SIZE)
            page = await query_outputs(self.state_dir, current)
            if page.total and not page.items and current.offset >= page.total:
                current = replace(current, offset=((page.total - 1) // PAGE_SIZE) * PAGE_SIZE)
                page = await query_outputs(self.state_dir, current)
            choices = await filter_choices(self.state_dir)
            return current, page, choices, anchor_found

        self.runner.submit(work(), lambda result, error: self._query_done(generation, anchor, result, error))

    def _query_done(self, generation: int, anchor: str | None, result, error) -> None:
        if generation != self._query_generation:
            return
        self._loading = False
        self.refresh_button.setEnabled(True)
        if self._pending_refresh is not None:
            pending = self._pending_refresh
            self._pending_refresh = None
            self.refresh(reset=pending[0], preserve=pending[1])
            return
        if error is not None:
            log.warning("gallery refresh failed reason=%s", type(error).__name__)
            self.refresh_button.setToolTip(f"Saved outputs could not be refreshed: {error}")
            return

        query, page, choices, anchor_found = result
        self.offset = query.offset
        self.items = list(page.items)
        self.total = page.total
        if self.mode == "Detail" and anchor == self.selected_id and anchor and not anchor_found:
            self.selected_id = None
        self._populate_facets(*choices)
        self._render_page()

        pending = self._pending_page_selection
        self._pending_page_selection = None
        if pending and self.items:
            self.select(self.items[0 if pending == "first" else -1].identity)
        elif self.mode == "Detail":
            if self.selected_id and any(item.identity == self.selected_id for item in self.items):
                self.select(self.selected_id, open_detail=False)
            elif self.items:
                self.select(self.items[0].identity, open_detail=False)
            else:
                self._clear_detail()

        self.refresh_button.setToolTip(self._page_summary())
        self._restore_scroll_later(anchor if anchor_found else None)
        self._schedule_save()

    def _populate_facets(self, models: tuple[str, ...], loras: tuple[str, ...]) -> None:
        for combo, placeholder, values in (
            (self.model, "All models", models),
            (self.lora, "All LoRAs", loras),
        ):
            current = combo.currentText()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(placeholder)
            combo.addItems(values)
            combo.setCurrentText(current or placeholder)
            combo.blockSignals(False)

    def _page_summary(self) -> str:
        if not self.total:
            return "No committed saved outputs match these filters."
        first = self.offset + 1
        last = self.offset + len(self.items)
        return (
            f"Showing {first}–{last} of {self.total}. "
            "Scroll beyond a Grid/List page edge or use Ctrl+PageUp/PageDown to paginate."
        )

    def _render_page(self) -> None:
        self._clear_grid()
        self._grid_buttons.clear()
        self._grid_painters.clear()
        self.table.setRowCount(len(self.items))
        self._ensure_filmstrip(len(self.items))

        if not self.items:
            empty = QLabel("No saved outputs match these filters.", self.grid_cards)
            empty.setProperty("role", "secondary")
            self.cards_layout.addWidget(empty, 0, 0)
            for button in self._film_buttons:
                button.hide()
            self._clear_detail()
            return

        for row, item in enumerate(self.items):
            dimensions = self._dimensions(item)
            size = _human_size(item.file_size)
            loras = "; ".join(f"{identifier} · {weight:g} · Unknown" for identifier, weight in item.loras) or "None"
            filename = Path(item.path).name
            tooltip = item.issue or item.path

            card = QFrame(self.grid_cards)
            card.setProperty("role", "galleryCard")
            layout = QVBoxLayout(card)
            layout.setContentsMargins(0, 0, 0, 8)
            layout.setSpacing(5)
            button = QPushButton(card)
            button.setProperty("role", "galleryCardImage")
            button.setAccessibleName(f"Open {filename} in Detail")
            button.setToolTip(tooltip)
            button.clicked.connect(lambda checked=False, identity=item.identity: self._open_from_grid(identity))
            painter = CoverPainter(button, "", (dimensions, size))
            painter.mode = "Fit"
            self._grid_buttons[item.identity] = button
            self._grid_painters[item.identity] = painter
            layout.addWidget(button)
            caption = QLabel(f"{filename}  ·  {size}", card)
            caption.setProperty("role", "galleryCardCaption")
            caption.setToolTip(item.path)
            layout.addWidget(caption)
            model = QLabel(item.model or "Model not recorded", card)
            model.setProperty("role", "secondary")
            model.setProperty("textRole", "supporting")
            layout.addWidget(model)

            icon = QTableWidgetItem()
            fields = [
                icon,
                filename,
                item.model or "Not recorded",
                loras,
                dimensions,
                size,
                _local_timestamp(item.saved_at_utc).replace("\n", " "),
                item.prompt or "Not recorded",
            ]
            for column, value in enumerate(fields):
                cell = value if isinstance(value, QTableWidgetItem) else QTableWidgetItem(value)
                cell.setData(Qt.ItemDataRole.UserRole, item.identity)
                cell.setToolTip(tooltip if column == 0 else str(value))
                self.table.setItem(row, column, cell)

            film_button = self._film_buttons[row]
            film_painter = self._film_painters[row]
            film_button.setProperty("identity", item.identity)
            film_button.setAccessibleName(f"Open {filename}")
            film_button.setToolTip(tooltip)
            film_button.setChecked(item.identity == self.selected_id)
            film_button.show()
            film_painter.pixmap = QPixmap()
            film_painter.badges = (dimensions, size)
            film_button.update()

        for index in range(len(self.items), len(self._film_buttons)):
            self._film_buttons[index].hide()

        self._density_changed()
        self._queue_thumbnails()

    def _clear_grid(self) -> None:
        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _ensure_filmstrip(self, count: int) -> None:
        layout = self._widget("galleryFilmstripScrollContents").layout()
        parent = self._widget("galleryFilmstripScrollContents")
        while len(self._film_buttons) < count:
            button = QPushButton(parent)
            button.setProperty("role", "galleryThumbnail")
            button.setCheckable(True)
            button.setAutoExclusive(True)
            button.clicked.connect(lambda checked=False, source=button: self.select(str(source.property("identity"))))
            layout.insertWidget(max(0, layout.count() - 1), button)
            painter = CoverPainter(button, "", None)
            self._film_buttons.append(button)
            self._film_painters.append(painter)

    def _queue_thumbnails(self) -> None:
        self._thumb_generation += 1
        generation = self._thumb_generation
        self._thumb_waiting.clear()
        for item in self.items:
            if not item.issue:
                self._thumb_waiting.append((generation, item.identity, item.path))
        self._pump_thumbnails()

    def _pump_thumbnails(self) -> None:
        while self._thumb_active < THUMBNAIL_CONCURRENCY and self._thumb_waiting:
            generation, identity, path = self._thumb_waiting.popleft()
            self._thumb_active += 1

            async def work(filename=path):
                return await asyncio.to_thread(_image, filename, THUMBNAIL_SIDE)

            self.runner.submit(
                work(),
                lambda result, error, gen=generation, ident=identity: self._thumbnail_done(gen, ident, result, error),
            )

    def _thumbnail_done(self, generation: int, identity: str, result, error) -> None:
        self._thumb_active = max(0, self._thumb_active - 1)
        if generation == self._thumb_generation and error is None and result:
            image, issue = result
            if image is not None:
                pixmap = QPixmap.fromImage(image)
                painter = self._grid_painters.get(identity)
                if painter is not None:
                    painter.pixmap = pixmap
                    painter.widget.update()
                try:
                    row = next(i for i, item in enumerate(self.items) if item.identity == identity)
                except StopIteration:
                    row = -1
                if row >= 0:
                    table_item = self.table.item(row, 0)
                    if table_item is not None:
                        table_item.setIcon(QIcon(pixmap))
                    if row < len(self._film_painters):
                        self._film_painters[row].pixmap = pixmap
                        self._film_buttons[row].update()
            elif issue:
                button = self._grid_buttons.get(identity)
                if button is not None:
                    button.setToolTip(f"Image is corrupt or unreadable: {issue}")
        self._pump_thumbnails()

    def _open_from_grid(self, identity: str) -> None:
        self._origin_identity = identity
        self.select(identity)

    def _open_from_list(self, identity: str) -> None:
        self._origin_identity = None
        self.select(identity)

    def select(self, identity: str, *, open_detail: bool = True) -> None:
        item = next((record for record in self.items if record.identity == identity), None)
        if item is None:
            return
        self.selected_id = identity
        if open_detail:
            self.show_view("Detail")
        for index, record in enumerate(self.items):
            if index < len(self._film_buttons):
                self._film_buttons[index].setChecked(record.identity == identity)
        self._present_item(item)
        self._update_navigation()
        self._schedule_save()

    def _update_navigation(self) -> None:
        if not self.items or not self.selected_id:
            self.previous_button.setEnabled(False)
            self.next_button.setEnabled(False)
            return
        try:
            index = next(i for i, item in enumerate(self.items) if item.identity == self.selected_id)
        except StopIteration:
            self.previous_button.setEnabled(False)
            self.next_button.setEnabled(False)
            return
        self.previous_button.setEnabled(index > 0 or self.offset > 0)
        self.next_button.setEnabled(index + 1 < len(self.items) or self.offset + len(self.items) < self.total)

    def _present_item(self, item: GalleryItem) -> None:
        request = self._request(item)
        position = self.offset + self.items.index(item) + 1
        self._widget("galleryPositionOverlay", QLabel).setText(f"{position} / {self.total}")
        self._widget("galleryDimensionsOverlay", QLabel).setText(self._dimensions(item))
        self._widget("galleryModelValue", QLabel).setText(item.model or "Not recorded")
        self._widget("galleryModelIdentifier", QLabel).hide()
        self._present_loras(item)
        self._widget("galleryStepsValue", QLabel).setText(self._value(request.get("steps")))
        self._widget("galleryGuidanceValue", QLabel).setText(self._value(request.get("guidance")))
        self._widget("gallerySamplerValue", QLabel).setText(self._value(request.get("sampler_id")))
        self._widget("galleryDateValue", QLabel).setText(_local_timestamp(item.saved_at_utc))
        self._widget("galleryPositivePrompt").setPlainText(item.prompt or "Not recorded")
        self._widget("galleryAdditionalNegativePromptValue", QLabel).setText(item.negative or "Not recorded")
        self._widget("galleryAdditionalSeedValue", QLabel).setText(self._value(request.get("seed")))
        self._widget("galleryAdditionalFilenameValue", QLabel).setText(Path(item.path).name)
        self._widget("galleryAdditionalProviderValue", QLabel).setText(item.backend_id or "Not recorded")
        self._widget("galleryAdditionalFileSizeValue", QLabel).setText(_human_size(item.file_size))
        self._widget("galleryAdditionalDurationValue", QLabel).setText("Not recorded")
        self._widget("galleryAdditionalPathValue", QLabel).setText(item.path)
        self.copy_button.setEnabled(bool(item.path))
        available = not item.issue
        self.folder_button.setEnabled(available)
        self.external_button.setEnabled(available)
        self.surface.setToolTip(item.issue or item.path)

        self.surface_painter.pixmap = QPixmap()
        self.surface_painter.zoom = 1.0
        self.surface_painter.offset = [0.0, 0.0]
        self.surface.update()

        self._image_generation += 1
        generation = self._image_generation

        async def read_image():
            return await asyncio.to_thread(_image, item.path, 0)

        self.runner.submit(
            read_image(),
            lambda result, error: self._image_done(generation, item.identity, result, error),
        )
        self.runner.submit(
            inspect_output(self.state_dir, item.identity),
            lambda result, error: self._detail_done(generation, item.identity, result, error),
        )

    def _image_done(self, generation: int, identity: str, result, error) -> None:
        if generation != self._image_generation or identity != self.selected_id:
            return
        if error is not None or not result or result[0] is None:
            issue = type(error).__name__ if error is not None else (result[1] if result else "decode failed")
            self.surface_painter.pixmap = QPixmap()
            self.surface.setToolTip(f"Image is corrupt or unreadable: {issue}")
            self.external_button.setEnabled(False)
            self.surface.update()
            return
        self.surface_painter.pixmap = QPixmap.fromImage(result[0])
        self.surface.update()

    def _detail_done(self, generation: int, identity: str, detail, error) -> None:
        if generation != self._image_generation or identity != self.selected_id:
            return
        raw = self._widget("galleryRawMetadata")
        if error is not None or detail is None:
            raw.setPlainText(json.dumps({"identity": identity, "metadata_status": "unavailable"}, indent=2))
            return
        raw.setPlainText(detail.metadata_json)
        try:
            metadata = json.loads(detail.metadata_json)
        except (TypeError, ValueError):
            return
        duration = self._recorded_duration(metadata)
        if duration is not None:
            self._widget("galleryAdditionalDurationValue", QLabel).setText(duration)

    @staticmethod
    def _recorded_duration(metadata: dict) -> str | None:
        containers = [metadata]
        effective = metadata.get("effective_configuration")
        if isinstance(effective, dict):
            containers.append(effective)
        artifacts = metadata.get("artifacts")
        if isinstance(artifacts, list):
            containers.extend(item.get("metadata", {}) for item in artifacts if isinstance(item, dict))
        for container in containers:
            if not isinstance(container, dict):
                continue
            for key in ("duration_seconds", "generation_seconds", "elapsed_seconds"):
                value = container.get(key)
                if value is not None:
                    return f"{value} s"
            if container.get("duration_ms") is not None:
                return f"{container['duration_ms']} ms"
        return None

    def _present_loras(self, item: GalleryItem) -> None:
        chips = [self._widget(f"galleryLoRAChip{i}") for i in (1, 2)]
        for chip in chips:
            chip.hide()
        if not item.loras:
            self._widget("galleryLoRALabel", QLabel).setText("LoRAs · none")
            return
        self._widget("galleryLoRALabel", QLabel).setText("LoRAs")
        full = "; ".join(f"{identifier} · {weight:g}" for identifier, weight in item.loras)
        first = item.loras[0]
        chips[0].show()
        self._widget("galleryLoRAName1", QLabel).setText(first[0])
        self._widget("galleryLoRAWeight1", QLabel).setText(f"{first[1]:g}")
        self._widget("galleryLoRAStatus1", QLabel).setText("Unknown")
        self._widget("galleryLoRAStatus1", QLabel).setToolTip(
            "No persisted LoRA application/compatibility evidence is available in the saved request."
        )
        if len(item.loras) > 1:
            chips[1].show()
            rest = "; ".join(f"{identifier} · {weight:g}" for identifier, weight in item.loras[1:])
            self._widget("galleryLoRAName2", QLabel).setText(rest)
            self._widget("galleryLoRAName2", QLabel).setToolTip(full)
            self._widget("galleryLoRAWeight2", QLabel).setText("")
            self._widget("galleryLoRAStatus2", QLabel).setText("Unknown")
            self._widget("galleryLoRAStatus2", QLabel).setToolTip(
                "No persisted LoRA application/compatibility evidence is available in the saved request."
            )

    @staticmethod
    def _request(item: GalleryItem) -> dict:
        try:
            value = json.loads(item.request_json)
            return value if isinstance(value, dict) else {}
        except (TypeError, ValueError):
            return {}

    @staticmethod
    def _value(value) -> str:
        return "Not recorded" if value is None or value == "" else str(value)

    @staticmethod
    def _dimensions(item: GalleryItem) -> str:
        return f"{item.width} × {item.height}" if item.width and item.height else "Unknown"

    def step(self, delta: int) -> None:
        if not self.items:
            return
        try:
            index = next(i for i, item in enumerate(self.items) if item.identity == self.selected_id)
        except StopIteration:
            index = 0
        target = index + delta
        if 0 <= target < len(self.items):
            self.select(self.items[target].identity, open_detail=False)
            return
        if delta > 0 and self.offset + len(self.items) < self.total:
            self._pending_page_selection = "first"
            self._change_page(1)
        elif delta < 0 and self.offset > 0:
            self._pending_page_selection = "last"
            self._change_page(-1)

    def _change_page(self, delta: int) -> bool:
        if self._loading:
            return False
        new_offset = self.offset + delta * PAGE_SIZE
        if new_offset < 0 or (delta > 0 and new_offset >= self.total):
            return False
        try:
            query = self._query(offset=max(0, new_offset))
        except ValidationError as exc:
            self.refresh_button.setToolTip(str(exc))
            return False
        self._capture_scroll()
        self._page_scroll_target = "start" if delta > 0 else "end"
        self._submit_query(query, anchor=None)
        return True

    def show_view(self, mode: str, *, persist: bool = True) -> None:
        if mode not in self.modes:
            return
        self.mode = mode
        self.stack.setCurrentWidget(self.modes[mode])
        self._widget(f"gallery{mode}Button", QPushButton).setChecked(True)
        if mode == "Detail":
            selected = next((item for item in self.items if item.identity == self.selected_id), None)
            if selected is None and self.items:
                selected = self.items[0]
            if selected is not None:
                self.select(selected.identity, open_detail=False)
            else:
                self._clear_detail()
        self._widget("galleryThumbnailSizeLabel", QLabel).setText("Row density" if mode == "List" else "Thumbnail size")
        self._density_changed()
        if mode == "Grid" and self._origin_identity:
            button = self._grid_buttons.get(self._origin_identity)
            if button is not None:
                QTimer.singleShot(0, lambda: self._focus_grid_origin(button))
        if persist:
            self._schedule_save()

    def _focus_grid_origin(self, button: QPushButton) -> None:
        button.setFocus(Qt.FocusReason.OtherFocusReason)
        self.grid_scroll.ensureWidgetVisible(button)

    @staticmethod
    def _thumbnail_pixels(scale_percent: int) -> int:
        """Convert the user-facing percentage to the internal logical-pixel thumbnail size."""

        return max(1, round(STANDARD_THUMBNAIL_PX * scale_percent / 100))

    def _density_changed(self, *_args) -> None:
        scale_percent = self.slider.value()
        density = self._thumbnail_pixels(scale_percent)

        # CoverPainter owns the visible pixels. One shared transparent icon
        # preserves QPushButton's content size hint across asynchronous relayouts
        # without allocating one full-size placeholder pixmap per Grid card.
        grid_placeholder = QPixmap(density, density)
        grid_placeholder.fill(Qt.GlobalColor.transparent)
        grid_icon = QIcon(grid_placeholder)
        for identity, button in self._grid_buttons.items():
            card = button.parentWidget()
            card.setFixedWidth(density + 16)
            button.setFixedSize(density + 16, density + 16)
            button.setIcon(grid_icon)
            button.setIconSize(grid_placeholder.size())
            painter = self._grid_painters.get(identity)
            if painter is not None:
                painter.widget.update()

        row_height = max(36, round(density * 0.35))
        self.table.verticalHeader().setDefaultSectionSize(row_height)
        self.table.setIconSize(QSize(row_height, max(24, row_height - 10)))
        for row in range(self.table.rowCount()):
            self.table.setRowHeight(row, row_height)

        self._widget("galleryThumbnailSizeValue", QLabel).setText(f"{scale_percent}%")

        self.film_scroll.setFixedWidth(density + 20)
        film_width, film_height = density + 8, round((density + 8) * 0.63)
        film_placeholder = QPixmap(max(1, film_width - 8), max(1, film_height - 8))
        film_placeholder.fill(Qt.GlobalColor.transparent)
        film_icon = QIcon(film_placeholder)
        for button in self._film_buttons:
            button.setFixedSize(film_width, film_height)
            button.setIcon(film_icon)
            button.setIconSize(film_placeholder.size())

        self.reflow_timer.start(0)
        self._schedule_save()

    def _layout_cards(self) -> None:
        if not self._grid_buttons:
            return
        width = self._thumbnail_pixels(self.slider.value()) + 16
        columns = max(1, (self.grid_viewport.width() + 10) // (width + 10))
        cards = [button.parentWidget() for button in self._grid_buttons.values()]
        for card in cards:
            self.cards_layout.removeWidget(card)
        for index, card in enumerate(cards):
            self.cards_layout.addWidget(card, index // columns, index % columns, Qt.AlignmentFlag.AlignTop)
        self.cards_layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

    def eventFilter(self, watched, event) -> bool:
        kind = event.type()
        if watched is self.table_viewport:
            if kind == QEvent.Type.MouseMove:
                row = self.table.indexAt(event.position().toPoint()).row()
                if row != self.table.property("hoverRow"):
                    self.table.setProperty("hoverRow", row)
                    self.table_viewport.update()
            elif kind == QEvent.Type.Leave:
                self.table.setProperty("hoverRow", -1)
                self.table_viewport.update()
        if watched is self.grid_viewport and kind == QEvent.Type.Resize:
            self.reflow_timer.start(0)
        if kind == QEvent.Type.Wheel and watched in (self.grid_viewport, self.table_viewport):
            bar = (
                self.grid_scroll.verticalScrollBar()
                if watched is self.grid_viewport
                else self.table.verticalScrollBar()
            )
            delta = event.angleDelta().y()
            if delta < 0 and bar.value() >= bar.maximum() and self._change_page(1):
                return True
            if delta > 0 and bar.value() <= bar.minimum() and self._change_page(-1):
                return True
        return False

    def _scroll_changed(self, name: str, value: int) -> None:
        self._scroll[name] = max(0, value)
        self._schedule_save()

    def _capture_scroll(self) -> None:
        self._scroll["grid"] = self.grid_scroll.verticalScrollBar().value()
        self._scroll["list"] = self.table.verticalScrollBar().value()
        self._scroll["filmstrip"] = self.film_scroll.verticalScrollBar().value()

    def _browsing_anchor(self) -> str | None:
        if not self.items:
            return None
        if self.mode == "Detail":
            return self.selected_id
        if self.mode == "List":
            row = self.table.rowAt(0)
            if row < 0:
                row = 0
            item = self.table.item(row, 0)
            return str(item.data(Qt.ItemDataRole.UserRole)) if item is not None else None
        position = self.grid_scroll.verticalScrollBar().value()
        for item in self.items:
            button = self._grid_buttons.get(item.identity)
            if button is not None and button.parentWidget().geometry().bottom() >= position:
                return item.identity
        return self.items[-1].identity

    def _restore_scroll_later(self, anchor: str | None = None) -> None:
        target = self._page_scroll_target
        self._page_scroll_target = None

        def restore():
            for name, bar in (
                ("grid", self.grid_scroll.verticalScrollBar()),
                ("list", self.table.verticalScrollBar()),
                ("filmstrip", self.film_scroll.verticalScrollBar()),
            ):
                if target == "start":
                    bar.setValue(0)
                elif target == "end":
                    bar.setValue(bar.maximum())
                else:
                    bar.setValue(min(self._scroll[name], bar.maximum()))

            if not anchor or target is not None:
                return
            if self.mode == "Grid":
                button = self._grid_buttons.get(anchor)
                if button is not None:
                    self.grid_scroll.verticalScrollBar().setValue(button.parentWidget().y())
            elif self.mode == "List":
                try:
                    row = next(i for i, item in enumerate(self.items) if item.identity == anchor)
                except StopIteration:
                    return
                cell = self.table.item(row, 0)
                if cell is not None:
                    self.table.scrollToItem(cell, QAbstractItemView.ScrollHint.PositionAtTop)
            elif self.mode == "Detail":
                try:
                    row = next(i for i, item in enumerate(self.items) if item.identity == anchor)
                except StopIteration:
                    return
                if row < len(self._film_buttons):
                    self.film_scroll.ensureWidgetVisible(self._film_buttons[row])

        QTimer.singleShot(0, restore)

    def _copy_path(self) -> None:
        item = self._selected()
        if item is not None:
            QApplication.clipboard().setText(item.path)

    def _open_folder(self) -> None:
        item = self._selected()
        if item is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(item.path).parent)))

    def _open_external(self) -> None:
        item = self._selected()
        if item is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(item.path))

    def _selected(self) -> GalleryItem | None:
        return next((item for item in self.items if item.identity == self.selected_id), None)

    def _clear_detail(self) -> None:
        self.surface_painter.pixmap = QPixmap()
        self.surface.update()
        self._widget("galleryPositionOverlay", QLabel).setText("0 / 0")
        self._widget("galleryDimensionsOverlay", QLabel).setText("—")
        self._widget("galleryModelValue", QLabel).setText("No saved output selected")
        self._widget("galleryModelIdentifier", QLabel).hide()
        self._widget("galleryLoRALabel", QLabel).setText("LoRAs")
        self._widget("galleryLoRAChip1").hide()
        self._widget("galleryLoRAChip2").hide()
        for name in ("galleryStepsValue", "galleryGuidanceValue", "gallerySamplerValue", "galleryDateValue"):
            self._widget(name, QLabel).setText("—")
        self._widget("galleryPositivePrompt").setPlainText("No saved output selected.")
        for name in (
            "galleryAdditionalNegativePromptValue",
            "galleryAdditionalSeedValue",
            "galleryAdditionalFilenameValue",
            "galleryAdditionalProviderValue",
            "galleryAdditionalFileSizeValue",
            "galleryAdditionalDurationValue",
            "galleryAdditionalPathValue",
        ):
            self._widget(name, QLabel).setText("—")
        self._widget("galleryRawMetadata").setPlainText("{}")
        self.copy_button.setEnabled(False)
        self.folder_button.setEnabled(False)
        self.external_button.setEnabled(False)
        self._update_navigation()

    def _schedule_save(self) -> None:
        if self._restoring:
            return
        self._save_dirty = True
        self.save_timer.start()

    def _state_document(self) -> dict:
        try:
            query = self._query(offset=self.offset)
        except ValidationError:
            query = GalleryQuery(offset=self.offset, limit=PAGE_SIZE)
        return {
            "query": asdict(query),
            "filters": {
                "prompt": self.prompt.text(),
                "model": self.model.currentText(),
                "lora": self.lora.currentText(),
                "date": self.date_filter.currentText(),
                "dimensions": self.dimensions.currentText(),
                "ratio": self.ratio.currentText(),
                "sort": self.sort.currentText(),
            },
            "view": self.mode,
            "thumbnail_scale_percent": self.slider.value(),
            "selected": self.selected_id,
            "auto_refresh": self.auto_button.isChecked(),
            "scroll": dict(self._scroll),
        }

    def _flush_save(self) -> None:
        if not self._save_dirty or self._save_running:
            return
        document = self._state_document()
        self._save_dirty = False
        self._save_running = True

        async def write():
            await asyncio.to_thread(save_viewer, self.state_dir, document)

        try:
            self._save_future = self.runner.submit(write(), self._save_done)
        except RuntimeError:
            # The owner may shut down the shared runner directly (for example
            # during Qt teardown). A stale debounce must never raise into the
            # Qt event loop after that lifecycle boundary.
            self._save_running = False
            self._save_future = None
            self._save_dirty = True
            log.info("viewer preference save skipped because async runner is closed")

    def save_now(self) -> None:
        """Persist the latest viewer state before the shared async runner is shut down."""

        self.save_timer.stop()
        future = self._save_future
        if future is not None and not future.done():
            try:
                future.result(timeout=5)
            except Exception as exc:
                log.warning("final viewer preferences not saved reason=%s", type(exc).__name__)
                return
        try:
            save_viewer(self.state_dir, self._state_document())
            self._save_dirty = False
        except Exception as exc:
            log.warning("final viewer preferences not saved reason=%s", type(exc).__name__)

    def _save_done(self, _result, error) -> None:
        self._save_running = False
        self._save_future = None
        if error is not None:
            log.warning("viewer preferences not saved reason=%s", type(error).__name__)
        if self._save_dirty:
            self.save_timer.start(0)
