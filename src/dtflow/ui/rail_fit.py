"""Keep lateral-panel content inside the rail: wrap text with spaces, clip (with a tooltip) text without.

The rails never scroll sideways. Qt lets a widget's own minimum size push a layout wider than its container, so wide
labels and buttons are given an ``Ignored`` horizontal policy: they take the width the layout offers and no more.
"""

from PySide6.QtWidgets import QLabel, QPushButton, QSizePolicy, QWidget

WIDE = 110  # px: a label or button wider than this may not dictate the rail's width


def fit_rail(root: QWidget) -> None:
    for widget in root.findChildren(QWidget):
        if isinstance(widget, QLabel):
            text = widget.text()
            if not text or widget.pixmap() is not None and not widget.pixmap().isNull():
                continue
            if " " in text.strip() and not widget.wordWrap():
                widget.setWordWrap(True)
            if widget.sizeHint().width() > WIDE or len(text) > 24:
                _ignore_width(widget)
                if " " not in text.strip():
                    widget.setToolTip(widget.toolTip() or text)  # clipped value: the tooltip carries all of it
        elif isinstance(widget, QPushButton) and widget.sizeHint().width() > WIDE:
            _ignore_width(widget)


def _ignore_width(widget: QWidget) -> None:
    policy = widget.sizePolicy()
    if policy.horizontalPolicy() != QSizePolicy.Policy.Ignored:
        policy.setHorizontalPolicy(QSizePolicy.Policy.Ignored)
        widget.setSizePolicy(policy)
    widget.setMinimumWidth(0)
