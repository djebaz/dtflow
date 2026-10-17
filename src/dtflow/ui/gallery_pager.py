"""Compact Option 2a gallery pager presentation (no persistence or provider access)."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget


class GalleryPager:
    """Present visible range, progressive load status and accessible batch jump chips.

    This class owns only the visual controls. The gallery controller owns paging,
    async reads and identity-anchored state across view and fullscreen changes.
    """

    def __init__(self, page: QWidget, on_jump: Callable[[int], None]) -> None:
        self.page = page
        self.on_jump = on_jump
        self.range = self._widget("galleryPagerRange", QLabel)
        self.status = self._widget("galleryPagerStatus", QLabel)
        self.layout = self._widget("galleryPagerChips", QWidget).layout()
        assert isinstance(self.layout, QHBoxLayout)
        self.buttons: dict[int, QPushButton] = {}
        self._gaps: list[QLabel] = []
        self._rendered_pages: tuple[int, ...] = ()

    def _widget(self, name: str, expected):
        widget = self.page.findChild(expected, name)
        if widget is None:
            raise ValueError(f"Missing gallery pager Designer object: {name}")
        return widget

    @staticmethod
    def _chip_pages(active: int, count: int) -> tuple[int, ...]:
        """Keep a bounded chip window, including the ends and current neighbors."""
        if count <= 0:
            return ()
        if count <= 7:
            return tuple(range(1, count + 1))
        return tuple(sorted({1, count, *(n for n in range(active - 2, active + 3) if 1 <= n <= count)}))

    def present(
        self,
        *,
        total: int,
        visible_start: int,
        visible_end: int,
        loaded: int,
        active_page: int,
        batch_size: int = 80,
        loading: bool = False,
        enabled: bool = True,
    ) -> None:
        """Update the persistent widget without resetting its surrounding layout."""
        total = max(0, total)
        page_count = (total + batch_size - 1) // batch_size
        active_page = max(1, min(active_page, page_count)) if page_count else 0
        if total == 0:
            self.range.setText("0–0 of 0")
        else:
            self.range.setText(f"{visible_start:,}–{visible_end:,} of {total:,}")
        self.status.setText(f"Loading {batch_size} more…" if loading else f"{loaded:,} loaded")
        pages = self._chip_pages(active_page, page_count)
        if pages != self._rendered_pages:
            for button in self.buttons.values():
                self.layout.removeWidget(button)
                button.deleteLater()
            self.buttons.clear()
            for gap in self._gaps:
                self.layout.removeWidget(gap)
                gap.deleteLater()
            self._gaps.clear()
            previous = 0
            for number in pages:
                if previous and number > previous + 1:
                    gap = QLabel("…", self.page)
                    gap.setAccessibleName("Pages omitted")
                    self.layout.addWidget(gap)
                    self._gaps.append(gap)
                button = QPushButton(str(number), self.page)
                button.setObjectName(f"galleryPagerPage{number}")
                button.setProperty("role", "galleryPagerChip")
                button.setAccessibleName(f"Jump to image batch {number}")
                first = (number - 1) * batch_size + 1
                last = min(total, number * batch_size)
                button.setToolTip(f"Jump to images {first}–{last}")
                button.setCheckable(True)
                button.clicked.connect(lambda _checked=False, n=number: self.on_jump(n))
                self.layout.addWidget(button)
                self.buttons[number] = button
                previous = number
            self._rendered_pages = pages
        for number, button in self.buttons.items():
            button.setChecked(number == active_page)
            button.setEnabled(enabled and not loading)
