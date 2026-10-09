"""Production saved-output controller for the existing Grid/List/Detail Images page."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
from collections import deque
from dataclasses import asdict, replace
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QBrush, QColor, QDesktopServices, QIcon, QImage, QImageReader, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QAbstractSpinBox,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFrame,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QProgressDialog,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from dtflow.core.gallery import GalleryItem, GalleryQuery
from dtflow.core.gallery_selection import GallerySelection, MAX_CHECKED_IMAGES
from dtflow.core.models import ValidationError
from dtflow.persistence import gallery as gallery_storage
from dtflow.persistence.viewer import load_viewer, save_viewer
from dtflow.services.gallery import filter_choices, inspect_output, locate_output, query_outputs
from dtflow.ui.button_focus import KeyboardFocusGuard
from dtflow.ui.gallery_detail_preview import CoverPainter, RowHoverDelegate, apply_gallery
from dtflow.ui.runner import AsyncRunner
from dtflow.ui.theme import load_tokens

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


def _display_output_path(path: str) -> str:
    """Show a saved image path from the output directory onward."""
    parts = Path(path).parts
    for index, part in enumerate(parts):
        if part.lower() in {"output", "outputs"}:
            return str(Path(*parts[index:]))
    return path


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

    def __init__(
        self, page: QWidget, *, runner: AsyncRunner, state_dir: Path, reuse_callback=None,
        regenerate_callback=None, export_callback=None, recycle_callback=None
    ):
        super().__init__(page)
        self.page = page
        self.runner = runner
        self.state_dir = Path(state_dir)
        self._reuse_callback = reuse_callback
        self._regenerate_callback = regenerate_callback
        self._export_callback = export_callback
        self._recycle_callback = recycle_callback
        self.selection = GallerySelection()
        self._tokens = load_tokens()
        self._hidden_checks = 0
        self._selection_generation = 0
        self._selection_busy = False
        self._syncing_selection = False
        self._selection_progress: QProgressDialog | None = None
        self._check_boxes: dict[str, QCheckBox] = {}
        self._active = False
        self._loading = False
        self._pending_refresh: tuple[bool, bool] | None = None
        self._restoring = True
        self._query_generation = 0
        self._image_generation = 0
        self._detail_generation = 0
        self._presented_item: GalleryItem | None = None
        self._thumb_generation = 0
        self._thumb_versions: dict[str, int] = {}
        self._thumb_active = 0
        self._thumb_waiting: deque[tuple[int, str, str]] = deque()
        self._save_running = False
        self._save_dirty = False
        self._save_future = None
        self._pending_page_selection: str | None = None
        self._page_scroll_target: str | None = None
        self.items: list[GalleryItem] = []
        self._rendered_page: tuple[int, tuple[GalleryItem, ...]] | None = None
        self.total = 0
        self.offset = 0
        self.selected_id: str | None = None
        self._origin_identity: str | None = None
        self._grid_buttons: dict[str, QPushButton] = {}
        self._grid_painters: dict[str, CoverPainter] = {}
        self._grid_labels: dict[str, tuple[QLabel, QLabel]] = {}
        self._grid_empty: QLabel | None = None
        self._film_buttons: list[QPushButton] = []
        self._film_painters: list[CoverPainter] = []
        self._film_entries: dict[str, tuple[QPushButton, CoverPainter]] = {}

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
        self._setup_selection_actions()
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

        self.table.setColumnCount(9)
        self.table.setHorizontalHeaderLabels(
            [
                "",
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
        for column, width in enumerate((36, 80, 160, 180, 300, 120, 90, 145, 360)):
            self.table.setColumnWidth(column, width)
        self.table.itemClicked.connect(self._table_clicked)
        self.table.itemActivated.connect(self._table_activated)
        self.table.itemChanged.connect(self._table_item_changed)
        self._header_check = QCheckBox(self.table.horizontalHeader())
        self._header_check.setTristate(True)
        self._header_check.setToolTip("Select all images on this loaded page only.")
        self._header_check.setAccessibleName("Check the current gallery page")
        self._header_check.setGeometry(10, 6, 22, 22)
        self._header_check.clicked.connect(self._toggle_page)
        self.table.horizontalHeader().sectionResized.connect(lambda *_: self._position_header_check())

        self.table_viewport.setMouseTracking(True)
        self.table_viewport.installEventFilter(self)
        self.grid_viewport.installEventFilter(self)
        self.page.installEventFilter(self)
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
        buttons = [self._widget(f"galleryThumbnail{i}", QPushButton) for i in range(1, 7)]
        self._designer_film_buttons = set(buttons)
        self._film_spares = list(zip(buttons, self._presentation_painters[1:7], strict=True))
        layout = self._widget("galleryFilmstripScrollContents").layout()
        for button in buttons:
            button.clicked.connect(lambda checked=False, source=button: self.select(str(source.property("identity"))))
            button.hide()
            layout.removeWidget(button)
            button.setAutoExclusive(False)
            button.setChecked(False)
            button.setAutoExclusive(True)

    def _setup_actions(self) -> None:
        self.refresh_button = self._widget("galleryRefreshButton", QPushButton)
        self.auto_button = self._widget("galleryAutoRefreshButton", QPushButton)
        self.refresh_button.setEnabled(True)
        self.refresh_button.setToolTip("Refresh saved outputs while keeping the current browsing anchor when possible.")
        self.refresh_button.clicked.connect(lambda: self.refresh(preserve=True))
        self.auto_button.setEnabled(True)
        self.auto_button.setCheckable(True)
        self.auto_button.installEventFilter(KeyboardFocusGuard(self.auto_button))
        self.auto_button.toggled.connect(self._auto_refresh_changed)

        self.previous_button = self._widget("galleryPreviousButton", QPushButton)
        self.next_button = self._widget("galleryNextButton", QPushButton)
        self.previous_button.setEnabled(False)
        self.next_button.setEnabled(False)
        self.previous_button.clicked.connect(lambda: self.step(-1))
        self.next_button.clicked.connect(lambda: self.step(1))
        self.use_settings_button = self._widget("galleryUseSettingsButton", QPushButton)
        self.regenerate_button = self._widget("galleryRegenerateButton", QPushButton)
        self.use_settings_button.clicked.connect(self._use_settings)
        self.regenerate_button.clicked.connect(self._regenerate)
        self.copy_button = self._widget("galleryCopyPathButton", QPushButton)
        self.folder_button = self._widget("galleryOpenFolderButton", QPushButton)
        self.external_button = self._widget("galleryExternalViewerButton", QPushButton)
        self.copy_button.clicked.connect(self._copy_path)
        self.folder_button.clicked.connect(self._open_folder)
        self.external_button.clicked.connect(self._open_external)


    def _setup_selection_actions(self) -> None:
        """Wire the canonical single-row contextual toolbar and keyboard affordances."""
        self.selection_bar = self._widget("gallerySelectionBar", QFrame)
        self.selection_count = self._widget("gallerySelectedCountLabel", QLabel)
        self.hidden_count_label = self._widget("galleryHiddenCountLabel", QLabel)
        self.select_page_button = self._widget("gallerySelectPageButton", QPushButton)
        self.select_filtered_button = self._widget("gallerySelectFilteredButton", QPushButton)
        self.clear_selection_button = self._widget("galleryClearButton", QPushButton)
        self.copy_paths_button = self._widget("galleryCopyPathsButton", QPushButton)
        self.more_button = self._widget("galleryMoreButton", QPushButton)
        self.export_button = self._widget("galleryExportButton", QPushButton)
        self.delete_button = self._widget("galleryDeleteButton", QPushButton)
        self.detail_check = self._widget("galleryDetailSelectionCheck", QCheckBox)

        self.select_page_button.clicked.connect(self._select_page)
        self.select_filtered_button.clicked.connect(self._select_all_filtered)
        self.copy_paths_button.clicked.connect(self._copy_checked_paths)
        self.export_button.clicked.connect(self._open_checked_export)
        self.delete_button.clicked.connect(self._request_recycle)
        self.detail_check.toggled.connect(self._detail_check_toggled)

        clear_menu = QMenu(self.clear_selection_button)
        clear_menu.addAction("Clear hidden", self._clear_hidden)
        clear_menu.addAction("Clear all", self._clear_all)
        self.clear_selection_button.setText("Clear")
        self.clear_selection_button.setMenu(clear_menu)
        more_menu = QMenu(self.more_button)
        more_menu.addAction("Select page", self._select_page)
        more_menu.addAction("Select all filtered", self._select_all_filtered)
        more_menu.addAction("Copy paths", self._copy_checked_paths)
        more_menu.addSeparator()
        more_menu.addAction("Clear hidden", self._clear_hidden)
        more_menu.addAction("Clear all", self._clear_all)
        self.more_button.setText("More")
        self.more_button.setMenu(more_menu)
        self.more_button.hide()

        for key, callback in (
            ("Ctrl+A", self._select_page),
            ("Escape", self._clear_all),
            ("Delete", self._request_recycle),
            ("Space", self._toggle_focused_check),
        ):
            shortcut = QShortcut(QKeySequence(key), self.page)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(lambda action=callback: self._selection_shortcut(action))
        self._sync_selection_ui()
        QTimer.singleShot(0, self._responsive_selection)

    def _selection_shortcut(self, callback) -> None:
        """Do not steal standard editing, pop-up or global generation shortcuts."""
        focus = QApplication.focusWidget()
        if focus is None or isinstance(focus, (QLineEdit, QComboBox, QAbstractSpinBox, QPlainTextEdit, QTextEdit, QCheckBox)):
            return
        if self.page.findChild(QWidget, "galleryPromptSearch").hasFocus():
            return
        if focus is self.detail_check:
            return
        if isinstance(focus, QPushButton) and focus in (self.export_button, self.delete_button):
            return
        callback()

    def _responsive_selection(self) -> None:
        """Keep one toolbar row: secondary actions collapse into a Qt menu."""
        width = self.selection_bar.width()
        compact = width < 1190
        narrow = width < 790
        for button in (self.select_filtered_button, self.clear_selection_button, self.copy_paths_button):
            button.setVisible(not compact)
        self.select_page_button.setVisible(not narrow)
        self.more_button.setVisible(compact)
        self._position_header_check()

    def _position_header_check(self) -> None:
        header = self.table.horizontalHeader()
        self._header_check.setGeometry(max(3, header.sectionViewportPosition(0) + 9), 5, 24, 24)

    def _selection_notice(self, text: str) -> None:
        self.selection_count.setToolTip(text)
        self.selection_bar.setToolTip(text)

    def _sync_selection_ui(self) -> None:
        count = len(self.selection.checked)
        self.selection_count.setText(f"{count} selected")
        self.hidden_count_label.setText(f"{self._hidden_checks} hidden by filters")
        self.hidden_count_label.setVisible(self._hidden_checks > 0)
        self.clear_selection_button.setEnabled(count > 0)
        self.copy_paths_button.setEnabled(count > 0)
        self.export_button.setEnabled(count > 0 and self._export_callback is not None)
        self.delete_button.setEnabled(count > 0 and self._recycle_callback is not None)
        self._sync_detail_checkbox()
        for row, item in enumerate(self.items):
            self._paint_checked_item(row, item.identity)
        self._sync_header_check()

    def _sync_detail_checkbox(self) -> None:
        if not hasattr(self, "detail_check"):
            return
        checked = self.selected_id in self.selection.checked if self.selected_id else False
        blocked = self.detail_check.blockSignals(True)
        self.detail_check.setChecked(checked)
        self.detail_check.setEnabled(self.selected_id is not None)
        self.detail_check.blockSignals(blocked)

    def _sync_header_check(self) -> None:
        if not hasattr(self, "_header_check"):
            return
        total = len(self.items)
        selected = sum(item.identity in self.selection.checked for item in self.items)
        state = (
            Qt.CheckState.Unchecked if selected == 0 else
            Qt.CheckState.Checked if selected == total else Qt.CheckState.PartiallyChecked
        )
        blocked = self._header_check.blockSignals(True)
        self._header_check.setCheckState(state)
        self._header_check.setEnabled(total > 0)
        self._header_check.setToolTip(f"{selected} of {total} images checked on this page")
        self._header_check.blockSignals(blocked)

    def _paint_checked_item(self, row: int, identity: str) -> None:
        checked = identity in self.selection.checked
        check = self._check_boxes.get(identity)
        if check is not None:
            blocked = check.blockSignals(True)
            check.setChecked(checked)
            check.blockSignals(blocked)
        button = self._grid_buttons.get(identity)
        if button is not None and button.property("checkedSelection") != checked:
            button.setProperty("checkedSelection", checked)
            button.update()
        card = button.parentWidget() if button is not None else None
        if card is not None and card.property("checkedSelection") != checked:
            card.setProperty("checkedSelection", checked)
            card.style().unpolish(card)
            card.style().polish(card)
            card.update()
        brush = QBrush(QColor(self._tokens["color.selection"] if checked else self._tokens["color.surface"]))
        for col in range(self.table.columnCount()):
            cell = self.table.item(row, col)
            if cell is None:
                continue
            cell.setBackground(brush)
            if col == 0:
                blocked = self.table.blockSignals(True)
                cell.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
                self.table.blockSignals(blocked)

    def _after_selection_edit(self) -> None:
        if self._selection_busy:
            self._cancel_selection_scan()
        self._selection_generation += 1
        self._sync_selection_ui()
        self._refresh_hidden_count()

    def _set_checked(self, identity: str, value: bool) -> None:
        if self._syncing_selection or (identity in self.selection.checked) == value:
            return
        if value and len(self.selection.checked) >= MAX_CHECKED_IMAGES:
            self._selection_notice("Selection limit reached: 10,000 images. Clear some checks first.")
            self._sync_selection_ui()
            return
        self.selection.toggle(identity)
        self._after_selection_edit()

    def _toggle_checked(self, identity: str) -> None:
        self._set_checked(identity, identity not in self.selection.checked)

    def _detail_check_toggled(self, value: bool) -> None:
        if self.selected_id:
            self._set_checked(self.selected_id, value)

    def _toggle_focused_check(self) -> None:
        if self.selected_id:
            self._toggle_checked(self.selected_id)

    def _select_range(self, identity: str) -> None:
        ids = [item.identity for item in self.items]
        anchor = self.selection.anchor
        if anchor not in ids or identity not in ids:
            self._set_checked(identity, True)
            return
        a, b = ids.index(anchor), ids.index(identity)
        self.selection.add(ids[min(a, b): max(a, b) + 1])
        self._after_selection_edit()

    def _table_item_changed(self, cell: QTableWidgetItem) -> None:
        if not self._syncing_selection and cell.column() == 0:
            identity = cell.data(Qt.ItemDataRole.UserRole)
            if identity:
                self._set_checked(str(identity), cell.checkState() == Qt.CheckState.Checked)

    def _table_clicked(self, cell: QTableWidgetItem) -> None:
        if cell.column() == 0:
            return
        identity = str(cell.data(Qt.ItemDataRole.UserRole))
        modifiers = QApplication.keyboardModifiers()
        if modifiers & Qt.KeyboardModifier.ShiftModifier:
            self._select_range(identity)
        elif modifiers & Qt.KeyboardModifier.ControlModifier:
            self._toggle_checked(identity)
        else:
            self._open_from_list(identity)

    def _table_activated(self, cell: QTableWidgetItem) -> None:
        if cell.column() != 0:
            self._open_from_list(str(cell.data(Qt.ItemDataRole.UserRole)))

    def _toggle_page(self, *_args) -> None:
        ids = [item.identity for item in self.items]
        if ids and all(identity in self.selection.checked for identity in ids):
            self.selection.checked.difference_update(ids)
            self._after_selection_edit()
        else:
            self._select_page()

    def _select_page(self) -> None:
        wanted = [item.identity for item in self.items]
        added = self.selection.add(wanted)
        omitted = sum(identity not in self.selection.checked for identity in wanted)
        if omitted:
            self._selection_notice(f"{added} page images added; {omitted} omitted at the 10,000-image limit.")
        self._after_selection_edit()

    def _clear_all(self) -> None:
        if self.selection.checked:
            self.selection.clear()
            self._hidden_checks = 0
            self._after_selection_edit()

    def _begin_selection_scan(self, *, mode: str) -> None:
        if self._selection_busy:
            return
        try:
            query = self.export_query()
        except ValidationError as exc:
            self._selection_notice(str(exc))
            return
        checked = self.selection.snapshot()
        self._selection_generation += 1
        generation = self._selection_generation
        self._selection_busy = True
        self.select_filtered_button.setEnabled(False)
        self._selection_progress = QProgressDialog(
            "Resolving saved output identities…", "Cancel", 0, 0, self.page
        )
        self._selection_progress.setWindowTitle("Gallery selection")
        self._selection_progress.setWindowModality(Qt.WindowModality.NonModal)
        self._selection_progress.setMinimumDuration(0)
        self._selection_progress.canceled.connect(self._cancel_selection_scan)
        self._selection_progress.show()
        self.runner.submit(
            asyncio.to_thread(
                gallery_storage.selection_candidates,
                self.state_dir, query, checked,
                add=mode == "add", maximum=MAX_CHECKED_IMAGES,
            ),
            lambda result, error: self._selection_scan_done(generation, mode, result, error),
        )

    def _cancel_selection_scan(self) -> None:
        self._selection_generation += 1
        self._selection_busy = False
        self.select_filtered_button.setEnabled(True)
        if self._selection_progress is not None:
            self._selection_progress.blockSignals(True)
            self._selection_progress.close()
            self._selection_progress = None
        self._selection_notice("Selection operation canceled; previous checks are retained.")

    def _selection_scan_done(self, generation, mode, result, error) -> None:
        if generation != self._selection_generation:
            return
        self._selection_busy = False
        self.select_filtered_button.setEnabled(True)
        if self._selection_progress is not None:
            self._selection_progress.blockSignals(True)
            self._selection_progress.close()
            self._selection_progress = None
        if error is not None:
            self._selection_notice(f"Selection query failed: {type(error).__name__}")
            return
        additions, hidden, omitted, matching = result
        if mode == "add":
            added = self.selection.add(additions)
            self._hidden_checks = hidden
            self._selection_notice(
                f"{added} added; {omitted} matching images not added at the 10,000-image limit."
                if omitted else f"{added} images added to the checked selection."
            )
        elif mode == "clear-hidden":
            self.selection.checked.intersection_update(matching)
            if self.selection.anchor not in self.selection.checked:
                self.selection.anchor = None
            self._hidden_checks = 0
        self._after_selection_edit()

    def _select_all_filtered(self) -> None:
        self._begin_selection_scan(mode="add")

    def _clear_hidden(self) -> None:
        if self._hidden_checks:
            self._begin_selection_scan(mode="clear-hidden")

    def _refresh_hidden_count(self) -> None:
        if self._selection_busy:
            return
        checked = self.selection.snapshot()
        if not checked:
            self._hidden_checks = 0
            self._sync_selection_ui()
            return
        try:
            query = self.export_query()
        except ValidationError:
            return
        self._selection_generation += 1
        generation = self._selection_generation
        self.runner.submit(
            asyncio.to_thread(gallery_storage.selection_candidates, self.state_dir, query, checked),
            lambda result, error: self._hidden_count_done(generation, result, error),
        )

    def _hidden_count_done(self, generation, result, error) -> None:
        if generation != self._selection_generation:
            return
        if error is not None:
            self._selection_notice("Hidden checked-image count could not be refreshed.")
            return
        self._hidden_checks = result[1]
        self._sync_selection_ui()

    def _copy_checked_paths(self) -> None:
        ids = self.selection.snapshot()
        if not ids:
            return
        self.runner.submit(
            asyncio.to_thread(gallery_storage.selection_paths, self.state_dir, ids),
            self._paths_resolved,
        )

    def _paths_resolved(self, result, error) -> None:
        if error is not None or result is None:
            self._selection_notice("Checked paths could not be resolved.")
            return
        paths, missing = result
        if paths:
            QApplication.clipboard().setText("\n".join(paths))
        self._selection_notice(f"Copied {len(paths)} paths; {missing} records unavailable.")

    def _open_checked_export(self) -> None:
        if self.selection.checked and self._export_callback is not None:
            self._export_callback()

    def _request_recycle(self) -> None:
        """Never recycle from a bare click; the service must own confirmation and mutation."""
        if self.selection.checked and self._recycle_callback is not None:
            self._recycle_callback(self.selection.snapshot(), self._hidden_checks)

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
        visible_page = (query.offset, page.items)
        if (
            visible_page == self._rendered_page
            and self._pending_page_selection is None
            and self._page_scroll_target is None
        ):
            # Polling identical results must not destroy cards/cells, discard
            # in-flight decodes or reset Detail zoom/pan and browsing position.
            # The total can change outside this page without changing its pixels.
            self._update_navigation()
            if self.mode == "Detail" and self._selected() is not None:
                position = (
                    self.offset + next(i for i, item in enumerate(self.items) if item.identity == self.selected_id) + 1
                )
                self._widget("galleryPositionOverlay", QLabel).setText(f"{position} / {self.total}")
            self.refresh_button.setToolTip(self._page_summary())
            self._refresh_hidden_count()
            self._schedule_save()
            return
        self._render_page()
        self._rendered_page = visible_page
        self._refresh_hidden_count()

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
        elif not self.items:
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
            if tuple(combo.itemText(i) for i in range(combo.count())) == (placeholder, *values):
                continue
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
        previous = {item.identity: item for item in self._rendered_page[1]} if self._rendered_page else {}
        identities = {item.identity for item in self.items}
        image_changes = [
            item
            for item in self.items
            if item.identity not in previous or self._image_key(previous[item.identity]) != self._image_key(item)
        ]
        containers = (self.grid_cards, self.table, self._widget("galleryFilmstripScrollContents"))
        updates = [widget.updatesEnabled() for widget in containers]
        for widget in containers:
            widget.setUpdatesEnabled(False)
        self._syncing_selection = True
        try:
            if self._rendered_page is None:
                self._clear_grid()  # Remove Designer fixture cards only on the first render.
                self.table.setRowCount(0)
            for identity in set(self._grid_buttons) - identities:
                card = self._grid_buttons.pop(identity).parentWidget()
                self.cards_layout.removeWidget(card)
                card.hide()
                card.deleteLater()
                del self._grid_painters[identity]
                del self._grid_labels[identity]
                self._check_boxes.pop(identity, None)
                self._thumb_versions.pop(identity, None)

            self._reconcile_rows()
            self._reconcile_filmstrip()
            for row, item in enumerate(self.items):
                if item.identity not in self._grid_buttons:
                    self._create_card(item)
                if previous.get(item.identity) != item:
                    self._update_entry(row, item)
                self._paint_checked_item(row, item.identity)
            for item in image_changes:
                self._grid_painters[item.identity].pixmap = QPixmap()
                self._film_entries[item.identity][1].pixmap = QPixmap()
                row = next(i for i, record in enumerate(self.items) if record.identity == item.identity)
                self.table.item(row, 1).setIcon(QIcon())

            if self.items:
                if self._grid_empty is not None:
                    self.cards_layout.removeWidget(self._grid_empty)
                    self._grid_empty.hide()
                    self._grid_empty.deleteLater()
                    self._grid_empty = None
                # Dict order is the query order consumed by the shared grid reflow.
                self._grid_buttons = {item.identity: self._grid_buttons[item.identity] for item in self.items}
            elif self._grid_empty is None:
                self._grid_empty = QLabel("No saved outputs match these filters.", self.grid_cards)
                self._grid_empty.setProperty("role", "secondary")
                self.cards_layout.addWidget(self._grid_empty, 0, 0)
            self._density_changed()
        finally:
            self._syncing_selection = False
            for widget, enabled in zip(containers, updates, strict=True):
                widget.setUpdatesEnabled(enabled)
        self._queue_thumbnails(image_changes)

    @staticmethod
    def _image_key(item: GalleryItem) -> tuple[str, int | None, str]:
        return item.path, item.file_size, item.issue

    def _create_card(self, item: GalleryItem) -> None:
        card = QFrame(self.grid_cards)
        card.setProperty("role", "galleryCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 8)
        layout.setSpacing(5)
        button = QPushButton(card)
        button.setProperty("role", "galleryCardImage")
        button.clicked.connect(lambda checked=False, identity=item.identity: self._open_from_grid(identity))
        check = QCheckBox(button)
        check.setObjectName("galleryCardCheck")
        check.setToolTip("Check image for bulk actions")
        check.setAccessibleName(f"Check {Path(item.path).name}")
        check.setGeometry(9, 9, 28, 28)
        check.setChecked(item.identity in self.selection.checked)
        check.toggled.connect(lambda value, identity=item.identity: self._set_checked(identity, value))
        self._check_boxes[item.identity] = check
        check.raise_()
        painter = CoverPainter(button, "", ("", ""))
        painter.mode = "Fit"
        self._grid_buttons[item.identity] = button
        self._grid_painters[item.identity] = painter
        layout.addWidget(button)
        caption = QLabel(card)
        caption.setProperty("role", "galleryCardCaption")
        layout.addWidget(caption)
        model = QLabel(card)
        model.setProperty("role", "secondary")
        model.setProperty("textRole", "supporting")
        layout.addWidget(model)
        self._grid_labels[item.identity] = caption, model

    def _reconcile_rows(self) -> None:
        """Keep the actual table items (and icons) when inserting or moving rows."""
        desired = [item.identity for item in self.items]
        current = [str(self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)) for row in range(self.table.rowCount())]
        selected = self.table.currentItem()
        selected_identity = selected.data(Qt.ItemDataRole.UserRole) if selected is not None else None
        selected_column = selected.column() if selected is not None else 0
        for row in reversed(range(len(current))):
            if current[row] not in desired:
                self.table.removeRow(row)
                current.pop(row)
        for row, identity in enumerate(desired):
            if row < len(current) and current[row] == identity:
                continue
            cells = None
            if identity in current:
                source = current.index(identity)
                cells = [self.table.takeItem(source, column) for column in range(self.table.columnCount())]
                self.table.removeRow(source)
                current.pop(source)
            self.table.insertRow(row)
            current.insert(row, identity)
            if cells is not None:
                for column, cell in enumerate(cells):
                    self.table.setItem(row, column, cell)
        if selected_identity in desired:
            cell = self.table.item(desired.index(selected_identity), selected_column)
            if self.table.currentItem() is not cell:
                self.table.setCurrentItem(cell)
        self.table.setProperty("hoverRow", -1)

    def _update_entry(self, row: int, item: GalleryItem) -> None:
        """Update only changed metadata, without replacing retained image widgets/cells."""
        dimensions, size = self._dimensions(item), _human_size(item.file_size)
        filename, tooltip = Path(item.path).name, item.issue or item.path
        button = self._grid_buttons[item.identity]
        button.setAccessibleName(f"Open {filename} in Detail")
        button.setToolTip(tooltip)
        painter = self._grid_painters[item.identity]
        painter.badges = (dimensions, size)
        caption, model = self._grid_labels[item.identity]
        caption.setText(f"{filename}  ·  {size}")
        caption.setToolTip(item.path)
        model.setText(item.model or "Model not recorded")
        loras = "; ".join(f"{identifier} · {weight:g} · Unknown" for identifier, weight in item.loras) or "None"
        fields = (
            "",
            "",
            filename,
            item.model or "Not recorded",
            loras,
            dimensions,
            size,
            _local_timestamp(item.saved_at_utc).replace("\n", " "),
            item.prompt or "Not recorded",
        )
        for column, value in enumerate(fields):
            cell = self.table.item(row, column)
            if cell is None:
                cell = QTableWidgetItem()
                cell.setData(Qt.ItemDataRole.UserRole, item.identity)
                self.table.setItem(row, column, cell)
            cell.setText(value)
            if column == 0:
                cell.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsUserCheckable)
                cell.setCheckState(Qt.CheckState.Checked if item.identity in self.selection.checked else Qt.CheckState.Unchecked)
            cell.setToolTip(tooltip if column == 1 else value)
        film_button, film_painter = self._film_entries[item.identity]
        film_button.setAccessibleName(f"Open {filename}")
        film_button.setToolTip(tooltip)
        film_painter.badges = (dimensions, size)
        button.update()
        film_button.update()

    def _clear_grid(self) -> None:
        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _reconcile_filmstrip(self) -> None:
        layout = self._widget("galleryFilmstripScrollContents").layout()
        parent = self._widget("galleryFilmstripScrollContents")
        identities = {item.identity for item in self.items}
        for identity in set(self._film_entries) - identities:
            button, painter = self._film_entries.pop(identity)
            layout.removeWidget(button)
            button.hide()
            button.setAutoExclusive(False)
            button.setChecked(False)
            button.setAutoExclusive(True)
            if button in self._designer_film_buttons:
                button.setProperty("identity", None)
                painter.pixmap = QPixmap()
                self._film_spares.append((button, painter))
            else:
                button.deleteLater()
        for row, item in enumerate(self.items):
            if item.identity not in self._film_entries:
                if self._film_spares:
                    button, painter = self._film_spares.pop(0)
                else:
                    button = QPushButton(parent)
                    button.setProperty("role", "galleryThumbnail")
                    button.setCheckable(True)
                    button.setAutoExclusive(True)
                    button.clicked.connect(
                        lambda checked=False, source=button: self.select(str(source.property("identity")))
                    )
                    painter = CoverPainter(button, "", ("", ""))
                button.setProperty("identity", item.identity)
                self._film_entries[item.identity] = button, painter
            button, _painter = self._film_entries[item.identity]
            if layout.indexOf(button) != row:
                layout.insertWidget(row, button)
            button.setChecked(item.identity == self.selected_id)
            button.show()
        self._film_buttons = [self._film_entries[item.identity][0] for item in self.items]
        self._film_painters = [self._film_entries[item.identity][1] for item in self.items]

    def _queue_thumbnails(self, changed: list[GalleryItem]) -> None:
        for item in changed:
            self._thumb_generation += 1
            generation = self._thumb_generation
            self._thumb_versions[item.identity] = generation
            if not item.issue:
                self._thumb_waiting.append((generation, item.identity, item.path))
        self._thumb_waiting = deque(
            entry for entry in self._thumb_waiting if self._thumb_versions.get(entry[1]) == entry[0]
        )
        self._pump_thumbnails()

    def _pump_thumbnails(self) -> None:
        while self._thumb_active < THUMBNAIL_CONCURRENCY and self._thumb_waiting:
            generation, identity, path = self._thumb_waiting.popleft()
            if self._thumb_versions.get(identity) != generation:
                continue
            self._thumb_active += 1

            async def work(filename=path):
                return await asyncio.to_thread(_image, filename, THUMBNAIL_SIDE)

            self.runner.submit(
                work(),
                lambda result, error, gen=generation, ident=identity: self._thumbnail_done(gen, ident, result, error),
            )

    def _thumbnail_done(self, generation: int, identity: str, result, error) -> None:
        self._thumb_active = max(0, self._thumb_active - 1)
        if generation == self._thumb_versions.get(identity) and error is None and result:
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
                    table_item = self.table.item(row, 1)
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
        modifiers = QApplication.keyboardModifiers()
        if modifiers & Qt.KeyboardModifier.ShiftModifier:
            self._select_range(identity)
            return
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            self._toggle_checked(identity)
            return
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
        self._sync_detail_checkbox()
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
        position = self.offset + self.items.index(item) + 1
        self._widget("galleryPositionOverlay", QLabel).setText(f"{position} / {self.total}")
        previous = self._presented_item
        if previous == item:
            return
        image_changed = (
            previous is None or previous.identity != item.identity or self._image_key(previous) != self._image_key(item)
        )
        self._presented_item = item
        request = self._request(item)
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
        self._widget("galleryAdditionalPathValue", QLabel).setText(_display_output_path(item.path))
        reusable = bool(request.get("model_id"))
        self.use_settings_button.setEnabled(reusable and self._reuse_callback is not None)
        self.regenerate_button.setEnabled(reusable and self._regenerate_callback is not None)
        self.copy_button.setEnabled(bool(item.path))
        available = not item.issue
        self.folder_button.setEnabled(available)
        if image_changed:
            self.external_button.setEnabled(available)
            self.surface.setToolTip(item.issue or item.path)
            self.surface_painter.pixmap = QPixmap()
            self.surface_painter.zoom = 1.0
            self.surface_painter.offset = [0.0, 0.0]
            self.surface.update()
            self._image_generation += 1
            image_generation = self._image_generation

            async def read_image():
                return await asyncio.to_thread(_image, item.path, 0)

            self.runner.submit(
                read_image(),
                lambda result, error: self._image_done(image_generation, item.identity, result, error),
            )
        self._detail_generation += 1
        detail_generation = self._detail_generation
        self.runner.submit(
            inspect_output(self.state_dir, item.identity),
            lambda result, error: self._detail_done(detail_generation, item.identity, result, error),
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
        if generation != self._detail_generation or identity != self.selected_id:
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
        timing = metadata.get("timing")
        if not isinstance(timing, dict):
            return None
        value = timing.get("backend_elapsed_seconds")
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            return None
        return f"{value:.2f} s"

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

    def _use_settings(self) -> None:
        item = self._selected()
        if item is not None and self._reuse_callback is not None:
            self._reuse_callback(self._request(item), item.identity)

    def _regenerate(self) -> None:
        item = self._selected()
        if item is not None and self._regenerate_callback is not None:
            self._regenerate_callback(self._request(item), item.identity)

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
        self.detail_check.setVisible(mode == "Detail")
        self._responsive_selection()
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
        if watched is self.page and kind == QEvent.Type.Resize:
            QTimer.singleShot(0, self._responsive_selection)
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

    def export_query(self) -> GalleryQuery:
        """Capture current filters without changing selection, paging or viewer state."""
        return replace(self._query(offset=0), limit=200)

    def export_identity(self) -> str | None:
        """Expose only the committed identity of the current single selection."""
        item = self._selected()
        return item.identity if item is not None else None

    def export_checked(self) -> tuple[str, ...]:
        """Freeze explicitly checked image identities for Connection export."""
        return self.selection.snapshot()

    def _selected(self) -> GalleryItem | None:
        return next((item for item in self.items if item.identity == self.selected_id), None)

    def _clear_detail(self) -> None:
        self._presented_item = None
        self._image_generation += 1
        self._detail_generation += 1
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
        self.use_settings_button.setEnabled(False)
        self.regenerate_button.setEnabled(False)
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
