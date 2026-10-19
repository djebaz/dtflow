"""Window-owned polling receiver; writer threads never invoke Qt or dialogs."""

from PySide6.QtCore import QObject, QTimer, Slot

from dtflow.ui.activity_strip import activity_strip


class DiagnosticNoticeReceiver(QObject):
    def __init__(self, window, mailbox):
        super().__init__(window.form)
        self.window = window
        self.mailbox = mailbox
        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self.deliver)
        window.runner.shutdown_started.connect(self.stop)
        self.timer.start()

    @Slot()
    def stop(self):
        self.timer.stop()

    @Slot()
    def deliver(self):
        if self.window.runner.closed:
            self.stop()
            return
        message = self.mailbox.take()
        if message is not None:
            activity_strip(self.window.form).complete("diagnostics", "Diagnostic logs", message, "warning")
            self.stop()
