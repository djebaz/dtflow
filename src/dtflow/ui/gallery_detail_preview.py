"""Reference-matched, offline gallery presentation shared by the Images tab and launcher.

No output discovery, persistence, credentials or provider calls. All artwork and
metadata here are explicitly illustrative fixtures.
"""

from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QIcon, QKeySequence, QPainter, QPainterPath, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QStyle,
    QButtonGroup,
    QFrame,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from shiboken6 import isValid

from dtflow.ui.theme import load_tokens

ASSETS = Path(__file__).parents[1] / "resources/images/gallery"


class CoverPainter(QObject):
    """Paint aspect-preserving images with rounded clipping and optional thumbnail badges."""

    def __init__(self, widget, path, badges=None):
        super().__init__(widget)
        self.widget = widget
        self.tokens = load_tokens()
        self.pixmap = QPixmap(str(path))
        self.badges = badges
        self.mode = "Cover"
        self.zoom = 1.0
        self.offset = [0.0, 0.0]
        self.drag = None
        widget.installEventFilter(self)
        widget.setMouseTracking(True)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Paint:
            painter = QPainter(obj)
            painter.setRenderHint(QPainter.Antialiasing)
            rect = QRectF(obj.rect())
            clip = QPainterPath()
            clip.addRoundedRect(rect, 10, 10)
            painter.setClipPath(clip)
            painter.fillRect(rect, QColor(self.tokens["color.canvas"]))
            if not self.pixmap.isNull():
                sx, sy = rect.width() / self.pixmap.width(), rect.height() / self.pixmap.height()
                scale = (min(sx, sy) if self.mode == "Fit" else max(sx, sy)) * self.zoom
                if self.mode == "100%":
                    scale = self.zoom
                w, h = self.pixmap.width() * scale, self.pixmap.height() * scale
                dest = QRectF((rect.width() - w) / 2 + self.offset[0], (rect.height() - h) / 2 + self.offset[1], w, h)
                painter.drawPixmap(dest, self.pixmap, QRectF(self.pixmap.rect()))
            if obj.property("role") == "galleryCardImage" and (obj.underMouse() or obj.hasFocus()):
                painter.setBrush(Qt.NoBrush)
                painter.setPen(QColor(self.tokens["color.focus"]))
                painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 9, 9)
            if self.badges:
                painter.setFont(QFont("Segoe UI", 9))
                for x, text in [(6, self.badges[0]), (rect.width() - 58, self.badges[1])]:
                    width = painter.fontMetrics().horizontalAdvance(text) + 12
                    badge = QRectF(x, rect.height() - 28, width, 22)
                    painter.setBrush(QColor(self.tokens["color.canvas"]))
                    painter.setPen(Qt.NoPen)
                    painter.drawRoundedRect(badge, 6, 6)
                    painter.setPen(QColor(self.tokens["color.text"]))
                    painter.drawText(badge, Qt.AlignCenter, text)
                if obj.isChecked():
                    painter.setBrush(Qt.NoBrush)
                    painter.setPen(QColor(self.tokens["color.focus" if obj.hasFocus() else "color.accent"]))
                    painter.drawRoundedRect(rect.adjusted(1.5, 1.5, -1.5, -1.5), 9, 9)
                    painter.setPen(QColor(self.tokens["color.focus" if obj.hasFocus() else "color.accent"]))
                    painter.drawRoundedRect(rect.adjusted(2.5, 2.5, -2.5, -2.5), 8, 8)
            painter.end()
            return True
        if not self.badges:
            if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                self.drag = event.position()
                return True
            if event.type() == QEvent.MouseMove and self.drag is not None:
                delta = event.position() - self.drag
                self.offset[0] += delta.x()
                self.offset[1] += delta.y()
                self.drag = event.position()
                obj.update()
                return True
            if event.type() == QEvent.MouseButtonRelease:
                self.drag = None
            if event.type() == QEvent.Wheel:
                self.zoom = max(0.1, min(8, self.zoom * (1.1 if event.angleDelta().y() > 0 else 1 / 1.1)))
                obj.update()
                return True
        return False


class RowHoverDelegate(QStyledItemDelegate):
    """Apply the light hover state across one row without selecting records."""

    def paint(self, painter, option, index):
        style = QStyleOptionViewItem(option)
        if index.row() == self.parent().property("hoverRow"):
            style.state |= QStyle.StateFlag.State_MouseOver
        else:
            style.state &= ~QStyle.StateFlag.State_MouseOver
        super().paint(painter, style, index)


def apply_gallery(page, assets=ASSETS, sample=False):
    """Apply cover painting, icons and disclosure behavior to a loaded gallery page.

    Set sample=False for application integration. Bind real data and service actions
    in the application; this helper does not claim those services are implemented.
    Retain the returned painters for as long as the page exists.
    """

    def get(name):
        return page.findChild(QWidget, name)

    get("galleryBrandSlot").hide()
    get("galleryNavigationSlot").hide()
    painters = []
    for name, icon in [
        ("galleryGridButton", "grid"),
        ("galleryListButton", "list"),
        ("galleryDetailButton", "detail"),
        ("galleryRefreshButton", "refresh"),
        ("galleryFullscreenButton", "fullscreen"),
        ("galleryAdditionalButton", "document"),
        ("galleryRawButton", "json"),
    ]:
        get(name).setIcon(QIcon(str(assets / f"{icon}.svg")))
        get(name).setIconSize(QSize(18, 18))
    get("galleryRefreshButton").setText("")
    get("galleryFullscreenButton").setText("")
    surface = get("galleryImageSurface")
    surface.setText("")
    paint = CoverPainter(surface, assets / "portrait.jpg")
    painters.append(paint)
    surface.setToolTip("Illustrative artwork — not a saved generation")
    get("galleryAdditionalScroll").hide()
    get("galleryRawMetadata").hide()
    for button, panel in [
        ("galleryAdditionalButton", "galleryAdditionalScroll"),
        ("galleryRawButton", "galleryRawMetadata"),
    ]:

        def toggle(checked, name=button, target=panel):
            if checked:
                other = "galleryRawButton" if name == "galleryAdditionalButton" else "galleryAdditionalButton"
                get(other).setChecked(False)
            get(target).setVisible(checked)

        get(button).toggled.connect(toggle)
    slider = get("galleryThumbnailSizeSlider")

    def resize_strip(value):
        get("galleryThumbnailSizeValue").setText(f"{value} px")
        get("galleryFilmstripScroll").setFixedWidth(value + 20)
        for i in range(1, 7):
            button = get(f"galleryThumbnail{i}")
            width, height = value + 8, round((value + 8) * 0.63)
            button.setFixedSize(width, height)
            # Reserve an icon's size hint; CoverPainter supplies the actual pixels.
            placeholder = QPixmap(width - 8, height - 8)
            placeholder.fill(Qt.GlobalColor.transparent)
            button.setIcon(QIcon(placeholder))
            button.setIconSize(placeholder.size())

    slider.valueChanged.connect(resize_strip)
    get("galleryViewMode").currentTextChanged.connect(lambda mode: set_view(mode))

    def set_view(mode):
        paint.mode = mode
        paint.zoom = 1
        paint.offset = [0, 0]
        surface.update()

    for name, factor in [("galleryZoomInButton", 1.1), ("galleryZoomOutButton", 1 / 1.1)]:
        get(name).setEnabled(True)
        get(name).clicked.connect(lambda checked=False, f=factor: zoom(f))

    def zoom(factor):
        paint.zoom = max(0.1, min(8, paint.zoom * factor))
        surface.update()

    get("galleryActualSizeButton").setEnabled(True)
    get("galleryActualSizeButton").clicked.connect(lambda: set_view("100%"))
    get("galleryFullscreenButton").setEnabled(True)
    get("galleryFullscreenButton").clicked.connect(
        lambda: page.window().showNormal() if page.window().isFullScreen() else page.window().showFullScreen()
    )
    paths = [assets / f"thumb{i}.png" for i in range(1, 7)]
    badges = [
        ("1536 × 1024", "Sample"),
        ("1024 × 1024", "1.9 MB"),
        ("1536 × 1024", "2.1 MB"),
        ("1024 × 1365", "3.2 MB"),
        ("1024 × 1556", "2.6 MB"),
        ("1024 × 1365", "3.4 MB"),
    ]
    for i, path in enumerate(paths, 1):
        button = get(f"galleryThumbnail{i}")
        button.setText("")
        painters.append(CoverPainter(button, path, badges[i - 1] if sample else None))
        button.setToolTip(f"Illustrative reference thumbnail {i}; badges are mockup data, not saved-output evidence")
        if sample:
            button.clicked.connect(lambda checked=False, index=i: select(index))

    def select(index):
        get(f"galleryThumbnail{index}").setChecked(True)
        paint.pixmap = QPixmap(str(assets / "portrait.jpg" if index == 1 else paths[index - 1]))
        get("galleryPositionOverlay").setText(f"{index} / 6")
        get("galleryDimensionsOverlay").setText(f"{paint.pixmap.width()} × {paint.pixmap.height()}")
        paint.zoom = 1
        paint.offset = [0, 0]
        filename = assets / "portrait.jpg" if index == 1 else paths[index - 1]
        get("galleryAdditionalFilenameValue").setText(f"{filename.name} (illustrative fixture)")
        get("galleryAdditionalFileSizeValue").setText(f"{filename.stat().st_size / 1024:.0f} KB (fixture file)")
        surface.update()

    if sample:
        get("galleryPreviousButton").setEnabled(True)
        get("galleryNextButton").setEnabled(True)

        def step(delta):
            index = next(i for i in range(1, 7) if get(f"galleryThumbnail{i}").isChecked())
            select((index - 1 + delta) % 6 + 1)

        get("galleryPreviousButton").clicked.connect(lambda: step(-1))
        get("galleryNextButton").clicked.connect(lambda: step(1))
        get("galleryPositionOverlay").setText("1 / 6")
        get("galleryDimensionsOverlay").setText("1536 × 1024")
        get("galleryAutoRefreshButton").setText("●  Auto-refresh: Off")
        get("galleryModelValue").setText("SDXL 1.0\nstabilityai/stable-diffusion-xl-base-1.0")
        get("galleryLoRAValue").setText(
            "<span >portrait_enhancer_v2　0.8　<span >● Unknown</span><br>film_photography_xl　0.4　<span >● Unknown</span></span>"
        )
        get("galleryLoRAValue").setToolTip("Offline fixture: no compatibility evidence recorded.")
        get("galleryAdditionalFilenameValue").setText("portrait.jpg (illustrative fixture)")
        get("galleryAdditionalFileSizeValue").setText(
            f"{(assets / 'portrait.jpg').stat().st_size / 1024:.0f} KB (fixture file)"
        )
        get("galleryAdditionalPathValue").setText("Bundled illustrative artwork — not a saved output")
        get("galleryRawMetadata").setPlainText(
            '{"designation": "illustrative_fixture", "saved_output": false, "provider": null, "compatibility_evidence": null}'
        )
        get("gallerySamplerValue").setText("DPM++ 2M Karras")
        get("galleryDateValue").setText("Jan 15, 2025\n14:23:31")
        get("galleryPositivePrompt").setPlainText(
            "Illustrative sample: portrait of a beautiful red-haired woman in golden hour light, natural makeup, freckles, flowing hair, looking at camera, cinematic lighting, shallow depth of field, bokeh, outdoors, film photography style"
        )
    resize_strip(slider.value())
    return painters


class DetailFixture(QObject):
    """Own the presentation objects used identically by shell and standalone preview."""

    def __init__(self, page):
        super().__init__(page)
        self.page = page
        self.painters = apply_gallery(page, sample=True)
        self.additional_button = page.findChild(QPushButton, "galleryAdditionalButton")
        self.raw_button = page.findChild(QPushButton, "galleryRawButton")
        self.additional = page.findChild(QWidget, "galleryAdditionalScroll")
        self.raw = page.findChild(QPlainTextEdit, "galleryRawMetadata")
        self.setup_browsing()
        for key, name in [("Left", "galleryPreviousButton"), ("Right", "galleryNextButton")]:
            shortcut = QShortcut(QKeySequence(key), page)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(page.findChild(QPushButton, name).click)
        escape = QShortcut(QKeySequence("Escape"), page)
        escape.activated.connect(lambda: page.window().showNormal() if page.window().isFullScreen() else None)

    def setup_browsing(self):
        """Mount fixture Grid/List below the same persistent two-row header."""
        self.stack = self.page.findChild(QWidget, "galleryContentStack")
        self.grid_scroll = self.page.findChild(QWidget, "galleryGridScroll")
        self.grid_cards = self.page.findChild(QWidget, "galleryGridCards")
        self.cards_layout = self.grid_cards.layout()
        self.table = self.page.findChild(QWidget, "galleryListTable")
        # Keep viewport identity without calling deleted parent widgets during teardown.
        self.table_viewport = self.table.viewport()
        self.grid_viewport = self.grid_scroll.viewport()
        self.reflow_timer = QTimer(self)
        self.reflow_timer.setSingleShot(True)
        self.reflow_timer.timeout.connect(self.layout_cards)
        self.initial_density_timer = QTimer(self)
        self.initial_density_timer.setSingleShot(True)
        self.initial_density_timer.timeout.connect(self.update_density)
        self.slider = self.page.findChild(QWidget, "galleryThumbnailSizeSlider")
        self.mode_buttons = QButtonGroup(self)
        self.mode_buttons.setExclusive(True)
        self.modes = {}
        for mode in ("Grid", "List", "Detail"):
            button = self.page.findChild(QPushButton, f"gallery{mode}Button")
            self.mode_buttons.addButton(button)
            button.clicked.connect(lambda checked=False, name=mode: self.show_view(name))
            self.modes[mode] = self.page.findChild(QWidget, f"gallery{mode}Content")
        self.records = []
        self.cards = []
        paths = [ASSETS / "portrait.jpg"] + [ASSETS / f"thumb{i}.png" for i in range(2, 7)]
        for index, path in enumerate(paths, 1):
            image = QPixmap(str(path))
            record = {
                "index": index,
                "path": path,
                "pixmap": image,
                "dimensions": f"{image.width()} × {image.height()}",
                "size": f"{path.stat().st_size / 1024:.0f} KB",
                "model": "SDXL 1.0 (sample)",
                "loras": "portrait_enhancer_v2 · 0.8 · Unknown; film_photography_xl · 0.4 · Unknown",
                "prompt": "Illustrative sample artwork; no recorded generation request.",
            }
            self.records.append(record)
            card = QFrame(self.grid_cards)
            card.setProperty("role", "galleryCard")
            layout = QVBoxLayout(card)
            layout.setContentsMargins(0, 0, 0, 8)
            layout.setSpacing(5)
            button = QPushButton(card)
            button.setProperty("role", "galleryCardImage")
            button.setAccessibleName(f"Open {path.name} in Detail; illustrative sample")
            button.setToolTip("Illustrative artwork; no saved-output generation evidence")
            painter = CoverPainter(button, path, (record["dimensions"], "Sample"))
            painter.mode = "Fit"
            self.painters.append(painter)
            button.clicked.connect(lambda checked=False, item=index: self.open_detail(item))
            layout.addWidget(button)
            caption = QLabel(f"{path.name}  ·  {record['size']}", card)
            caption.setProperty("role", "galleryCardCaption")
            caption.setToolTip(str(path.name))
            layout.addWidget(caption)
            model = QLabel(record["model"], card)
            model.setProperty("role", "secondary")
            layout.addWidget(model)
            self.cards.append((card, button))
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
        self.table.setRowCount(len(self.records))
        self.table.verticalHeader().hide()
        self.table.setProperty("hoverRow", -1)
        self.table.setItemDelegate(RowHoverDelegate(self.table))
        self.table_viewport.setMouseTracking(True)
        self.table_viewport.installEventFilter(self)
        self.table.setIconSize(QSize(64, 48))
        for row, record in enumerate(self.records):
            icon = QTableWidgetItem()
            icon.setIcon(QIcon(record["pixmap"]))
            fields = [
                icon,
                record["path"].name,
                record["model"],
                record["loras"],
                record["dimensions"],
                record["size"],
                "Not recorded",
                record["prompt"],
            ]
            for column, value in enumerate(fields):
                item = value if isinstance(value, QTableWidgetItem) else QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, record["index"])
                item.setToolTip("Illustrative sample. " + (item.text() or record["path"].name))
                self.table.setItem(row, column, item)
        for column, width in enumerate((80, 150, 150, 270, 120, 85, 130, 320)):
            self.table.setColumnWidth(column, width)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemClicked.connect(lambda item: self.open_detail(item.data(Qt.ItemDataRole.UserRole)))
        self.table.itemActivated.connect(lambda item: self.open_detail(item.data(Qt.ItemDataRole.UserRole)))
        self.grid_viewport.installEventFilter(self)
        self.slider.valueChanged.connect(self.update_density)
        self.show_view("Detail")
        self.initial_density_timer.start(0)

    def show_view(self, mode):
        """Switch mounted content only; header widgets and scroll positions persist."""
        self.mode = mode
        self.stack.setCurrentWidget(self.modes[mode])
        self.page.findChild(QPushButton, f"gallery{mode}Button").setChecked(True)
        self.page.findChild(QWidget, "galleryThumbnailSizeLabel").setText(
            "Row density" if mode == "List" else "Thumbnail size"
        )
        self.update_density()

    def open_detail(self, index):
        """Open one sample without introducing gallery multi-selection state."""
        self.page.findChild(QPushButton, f"galleryThumbnail{index}").click()
        self.show_view("Detail")

    def update_density(self, *_):
        """Use uniform aspect-fit tiles, or compact list row height, from the shared slider."""
        if not all(isValid(widget) for widget in (self, self.page, self.slider, self.table)):
            return
        size = self.slider.value()
        for card, button in self.cards:
            if not isValid(card) or not isValid(button):
                continue
            card.setFixedWidth(size + 16)
            button.setFixedSize(size + 16, size + 16)
            placeholder = QPixmap(size, size)
            placeholder.fill(Qt.GlobalColor.transparent)
            button.setIcon(QIcon(placeholder))
            button.setIconSize(placeholder.size())
        height = max(36, min(84, round(size * 0.35)))
        self.table.verticalHeader().setDefaultSectionSize(height)
        self.table.setIconSize(QSize(height, max(24, height - 10)))
        for row in range(self.table.rowCount()):
            self.table.setRowHeight(row, height)
        self.page.findChild(QWidget, "galleryThumbnailSizeValue").setText(
            f"{height} px" if self.mode == "List" else f"{size} px"
        )
        self.layout_cards()

    def layout_cards(self):
        """Reflow a regular grid without masonry or ratio-dependent card heights."""
        if not all(isValid(widget) for widget in (self, self.slider, self.grid_viewport, self.cards_layout)):
            return
        width = self.slider.value() + 16
        columns = max(1, (self.grid_viewport.width() + 10) // (width + 10))
        for card, _ in self.cards:
            self.cards_layout.removeWidget(card)
        for index, (card, _) in enumerate(self.cards):
            self.cards_layout.addWidget(card, index // columns, index % columns, Qt.AlignmentFlag.AlignTop)
        self.cards_layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

    def eventFilter(self, watched, event):
        # Qt delivers destruction events after sibling/parent C++ objects are gone.
        # Ignore those before accessing a widget, and guard the remaining callbacks.
        kind = event.type()
        if kind not in (QEvent.Type.MouseMove, QEvent.Type.Leave, QEvent.Type.Resize):
            return False
        if watched is self.table_viewport:
            if not isValid(self.table) or not isValid(self.table_viewport):
                return False
            if event.type() == QEvent.Type.MouseMove:
                row = self.table.indexAt(event.position().toPoint()).row()
                if row != self.table.property("hoverRow"):
                    self.table.setProperty("hoverRow", row)
                    self.table_viewport.update()
            elif event.type() == QEvent.Type.Leave:
                self.table.setProperty("hoverRow", -1)
                self.table_viewport.update()
        if watched is self.grid_viewport and kind == QEvent.Type.Resize:
            if isValid(self.grid_viewport) and isValid(self.reflow_timer):
                self.reflow_timer.start(0)
        return False
