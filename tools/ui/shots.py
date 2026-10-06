import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(os.environ.get("DTFLOW_ROOT", Path.cwd()))
# Bootstrap test support and install the network guard before provider imports.
sys.path.insert(0, str(ROOT / "tests"))
from support.network_guard import install  # noqa: E402

install()
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from support.cloud_assets import DtPlus  # noqa: E402
from support.fake_vault import Vault  # noqa: E402

from dtflow.backends.fake import FakeBackend  # noqa: E402
from dtflow.services import session  # noqa: E402
from dtflow.services.credentials import (  # noqa: E402
    SavedCredentials,
)
from dtflow.ui.main_window import MainWindow  # noqa: E402
from dtflow.ui.theme import apply_theme  # noqa: E402

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
app = QApplication([])
apply_theme(app)
cloud = DtPlus()


def backend(selection, **_):
    return cloud.backend() if selection.name == "cloud" else FakeBackend(scenario="success", gate=None)


with tempfile.TemporaryDirectory(prefix="dtflow-ui-") as tmp, patch.object(session, "create_backend", backend):
    shell = MainWindow(saved=SavedCredentials(Vault()), environ={}, state_dir=Path(tmp))
    shell.form.resize(1600, 1000)
    shell.show()

    def grab(name):
        app.processEvents()
        QTest.qWait(200)
        app.processEvents()
        ok = shell.form.grab().save(str(out / f"{name}.png"))
        print(name, ok)

    f = shell.form
    f.connectionNavigationButton.click()
    grab("10-connection")
    f.backendCombo.setCurrentText("fake")
    f.generationNavigationButton.click()
    grab("11-generate")
    f.queueNavigationButton.click()
    grab("12-queues")
    f.imagesNavigationButton.click()
    grab("13-images")
