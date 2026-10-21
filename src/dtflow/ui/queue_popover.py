"""One-click Queue popover (#135): queue Generate's request from any tab without opening the Queues tab.

The presentation is the Designer form ``forms/queue_popover.ui``. This controller owns no queue rules: it offers Repeat N or
the Queues composer's current plan and hands the choice to ``QueuePage.quick_create``, the same creation and dispatch
path as the composer's Start now and Add to queue.
"""

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QFile, QIODevice, QObject, QPoint, Qt
from PySide6.QtGui import QGuiApplication, QPixmap
from PySide6.QtUiTools import QUiLoader
from PySide6.QtWidgets import QWidget

from dtflow.ui.activity import Activity

DISCLOSURE = "Uses Generate's current request and backend. A cloud run may use credits."
FORM = Path(__file__).with_name("forms") / "queue_popover.ui"


class QueuePopover(QObject):
    """A compact popup: Repeat N or the composer plan, then Start now or Add to queue behind the running work."""

    def __init__(self, queue, activity: Activity, parent: QWidget, *, changed: Callable[[str], None] = lambda _: None):
        super().__init__(parent)
        self._queue, self._activity, self._changed = queue, activity, changed
        file = QFile(str(FORM))
        if not file.open(QIODevice.OpenModeFlag.ReadOnly):
            raise OSError(f"{FORM.name} could not be opened.")
        try:
            self.form = QUiLoader().load(file, parent)
        finally:
            file.close()
        if self.form is None:
            raise OSError(f"{FORM.name} could not be loaded.")
        self.form.setWindowFlags(Qt.WindowType.Popup)
        form = self.form
        self.repeat_option, self.repeat_spin = form.queuePopoverRepeat, form.queuePopoverCount
        self.plan_option, self.plan_label = form.queuePopoverPlan, form.queuePopoverPlanText
        self.note_label = form.queuePopoverNote
        self.add_button, self.start_button = form.queuePopoverAddButton, form.queuePopoverStartButton

        # The two radios share a parent, so Qt keeps them exclusive without a button group.
        self.repeat_option.toggled.connect(lambda _: self._sync())
        self.repeat_spin.valueChanged.connect(lambda _: self.repeat_option.setChecked(True))
        self.repeat_spin.valueChanged.connect(lambda _: self._sync())
        self.start_button.clicked.connect(lambda: self._go("start"))
        self.add_button.clicked.connect(lambda: self._go("add"))
        activity.listen(self._activity_changed)
        self.destroyed.connect(lambda *_: activity.unlisten(self._activity_changed))

    def label(self) -> str:
        """The trigger text for the current choice, such as ``Queue ×4``."""
        if self.plan_option.isChecked():
            total = self._queue.last_plan_total()
            return f"Queue ×{total}" if total else "Queue"
        return f"Queue ×{self.repeat_spin.value()}"

    def hide(self) -> None:
        self.form.hide()

    def isVisible(self) -> bool:
        return self.form.isVisible()

    def grab(self) -> QPixmap:
        return self.form.grab()

    def placement(self, anchor: QWidget) -> QPoint:
        """Top-left in the anchor's window: under the anchor, right-aligned when the anchor is on the right."""
        window = anchor.window()
        point = anchor.mapTo(window, QPoint(0, anchor.height() + 4))
        if point.x() + anchor.width() // 2 > window.width() // 2:
            point.setX(point.x() + anchor.width() - self.form.width())
        return point

    def sync_label(self) -> None:
        """The composer plan changed: keep the trigger's count true without opening the popover."""
        if self.isVisible():
            self.refresh()
        else:
            self._changed(self.label())

    def open_at(self, anchor: QWidget) -> None:
        """Show below ``anchor``, kept on its screen."""
        self.refresh()
        form = self.form
        point = anchor.window().mapToGlobal(self.placement(anchor))
        screen = (anchor.screen() or QGuiApplication.primaryScreen()).availableGeometry()
        x = max(screen.left(), min(point.x(), screen.right() - form.width()))
        y = point.y() if point.y() + form.height() <= screen.bottom() else anchor.mapToGlobal(QPoint(0, 0)).y() - 4
        if y != point.y():
            y -= form.height()
        form.move(x, y)
        form.show()
        (self.start_button if self.start_button.isEnabled() else self.add_button).setFocus()

    def refresh(self) -> None:
        level, text = self._queue.last_plan()
        self.plan_option.setEnabled(level == "ok")
        if level != "ok" and self.plan_option.isChecked():
            self.repeat_option.setChecked(True)
        self.plan_label.setText(text)
        self._sync()

    def _activity_changed(self) -> None:
        if self.isVisible():
            self._sync()

    def _sync(self) -> None:
        start, add, reason = self._queue.quick_state()
        self.start_button.setEnabled(start)
        self.add_button.setEnabled(add)
        self.note_label.setText(f"{reason} Max at once: {self._queue.quick_cap()}. {DISCLOSURE}")
        count = self.label().removeprefix("Queue").strip(" ×")
        self.start_button.setText(f"Start now ({count})" if count else "Start now")
        # Wrapped labels need the height for the fixed width, which a plain size hint does not give.
        self.form.setFixedHeight(self.form.layout().totalHeightForWidth(self.form.width()))
        self._changed(self.label())

    def _go(self, action: str) -> None:
        self.hide()
        self._queue.quick_create(action, count=None if self.plan_option.isChecked() else self.repeat_spin.value())
