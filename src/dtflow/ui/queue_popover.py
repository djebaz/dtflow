"""One-click Queue popover (#135): queue Generate's request from any tab without opening the Queues tab.

The popover owns no queue rules. It offers Repeat N or the composer's last Pair/Cross plan, and hands the choice to
``QueuePage.quick_create``, the same creation and dispatch path as the composer's Start now and Add to queue.
"""

from collections.abc import Callable

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from dtflow.ui.activity import Activity

DISCLOSURE = "Uses Generate's current request and backend. A cloud run may use credits."


class QueuePopover(QFrame):
    """A compact popup: Repeat N or the last plan, then Start now or Add to queue behind the running queue."""

    def __init__(self, queue, activity: Activity, parent: QWidget, *, changed: Callable[[str], None] = lambda _: None):
        super().__init__(parent, Qt.WindowType.Popup)
        self._queue, self._activity, self._changed = queue, activity, changed
        self.setObjectName("queuePopover")
        self.setProperty("role", "card")
        self.setAccessibleName("Queue Generate's request")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        title = QLabel("Queue Generate's request", self)
        title.setObjectName("queuePopoverTitle")
        layout.addWidget(title)

        self.repeat_option = QRadioButton("Repeat", self)
        self.repeat_option.setObjectName("queuePopoverRepeat")
        self.repeat_spin = QSpinBox(self)
        self.repeat_spin.setObjectName("queuePopoverCount")
        self.repeat_spin.setRange(1, 1000)
        self.repeat_spin.setAccessibleName("Number of queue items")
        self.repeat_spin.setToolTip("Item N uses the Generate seed + N - 1.")
        repeat_row = QHBoxLayout()
        repeat_row.setSpacing(8)
        repeat_row.addWidget(self.repeat_option)
        repeat_row.addWidget(self.repeat_spin)
        repeat_row.addWidget(QLabel("times", self))
        repeat_row.addStretch(1)
        layout.addLayout(repeat_row)

        self.plan_option = QRadioButton("Last plan", self)
        self.plan_option.setObjectName("queuePopoverPlan")
        self.plan_label = QLabel(self)
        self.plan_label.setObjectName("queuePopoverPlanText")
        self.plan_label.setProperty("role", "secondary")
        self.plan_label.setWordWrap(True)
        plan_row = QHBoxLayout()
        plan_row.setSpacing(8)
        plan_row.addWidget(self.plan_option)
        plan_row.addWidget(self.plan_label, 1)
        layout.addLayout(plan_row)
        self._choice = QButtonGroup(self)
        self._choice.addButton(self.repeat_option)
        self._choice.addButton(self.plan_option)
        self.repeat_option.setChecked(True)

        self.note_label = QLabel(self)
        self.note_label.setObjectName("queuePopoverNote")
        self.note_label.setProperty("role", "secondary")
        self.note_label.setWordWrap(True)
        layout.addWidget(self.note_label)

        self.add_button = QPushButton("Add to queue", self)
        self.add_button.setObjectName("queuePopoverAddButton")
        self.add_button.setToolTip("Waits up next and starts by itself when the running queue ends.")
        self.start_button = QPushButton("Start now", self)
        self.start_button.setObjectName("queuePopoverStartButton")
        self.start_button.setProperty("role", "primary")
        self.start_button.setToolTip("Creates the queue and starts it at once.")
        actions = QHBoxLayout()
        actions.setSpacing(8)
        actions.addStretch(1)
        actions.addWidget(self.add_button)
        actions.addWidget(self.start_button)
        layout.addLayout(actions)
        self.setFixedWidth(340)

        self.repeat_spin.valueChanged.connect(lambda _: self.repeat_option.setChecked(True))
        self._choice.buttonToggled.connect(lambda *_: self._sync())
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

    def open_at(self, anchor: QWidget) -> None:
        """Show below ``anchor``, kept on its screen."""
        self.refresh()
        point = anchor.mapToGlobal(QPoint(0, anchor.height() + 4))
        screen = (anchor.screen() or QGuiApplication.primaryScreen()).availableGeometry()
        x = max(screen.left(), min(point.x(), screen.right() - self.width()))
        y = point.y() if point.y() + self.height() <= screen.bottom() else anchor.mapToGlobal(QPoint(0, 0)).y() - 4
        if y != point.y():
            y -= self.height()
        self.move(x, y)
        self.show()
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
        self.note_label.setText(f"{reason} {DISCLOSURE}")
        # Wrapped labels need the height for the fixed width, which a plain size hint does not give.
        self.setFixedHeight(self.layout().totalHeightForWidth(self.width()))
        self._changed(self.label())

    def _go(self, action: str) -> None:
        self.hide()
        self._queue.quick_create(action, count=None if self.plan_option.isChecked() else self.repeat_spin.value())
