"""Reference-matched, offline gallery presentation shared by the Images tab and launcher.

No output discovery, persistence, credentials or provider calls. All artwork and
metadata here are explicitly illustrative fixtures.
"""

from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QKeySequence, QPainter, QPainterPath, QPixmap, QShortcut
from PySide6.QtWidgets import QPlainTextEdit, QPushButton, QWidget

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
            painter.fillRect(rect, QColor(self.tokens["gallery.canvas"]))
            if not self.pixmap.isNull():
                sx, sy = rect.width() / self.pixmap.width(), rect.height() / self.pixmap.height()
                scale = (min(sx, sy) if self.mode == "Fit" else max(sx, sy)) * self.zoom
                if self.mode == "100%":
                    scale = self.zoom
                w, h = self.pixmap.width() * scale, self.pixmap.height() * scale
                dest = QRectF((rect.width() - w) / 2 + self.offset[0], (rect.height() - h) / 2 + self.offset[1], w, h)
                painter.drawPixmap(dest, self.pixmap, QRectF(self.pixmap.rect()))
            if self.badges:
                painter.setFont(QFont("Segoe UI", 9))
                for x, text in [(6, self.badges[0]), (rect.width() - 58, self.badges[1])]:
                    width = painter.fontMetrics().horizontalAdvance(text) + 12
                    badge = QRectF(x, rect.height() - 28, width, 22)
                    painter.setBrush(QColor(self.tokens["gallery.overlay"]))
                    painter.setPen(Qt.NoPen)
                    painter.drawRoundedRect(badge, 6, 6)
                    painter.setPen(QColor(self.tokens["gallery.text"]))
                    painter.drawText(badge, Qt.AlignCenter, text)
                if obj.isChecked():
                    painter.setBrush(Qt.NoBrush)
                    painter.setPen(QColor(self.tokens["gallery.focus"]))
                    painter.drawRoundedRect(rect.adjusted(1.5, 1.5, -1.5, -1.5), 9, 9)
                    painter.setPen(QColor(self.tokens["gallery.focus"]))
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
        get("galleryFilmstripScroll").setFixedWidth(value + 28)
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
        get("galleryResultCount").setText("6 sample images")
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
        for key, name in [("Left", "galleryPreviousButton"), ("Right", "galleryNextButton")]:
            shortcut = QShortcut(QKeySequence(key), page)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(page.findChild(QPushButton, name).click)
        escape = QShortcut(QKeySequence("Escape"), page)
        escape.activated.connect(lambda: page.window().showNormal() if page.window().isFullScreen() else None)
