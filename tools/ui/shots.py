import os, sys, tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(os.environ.get("DTFLOW_ROOT", Path.cwd()))
sys.path.insert(0, str(ROOT / "tests"))
from support.network_guard import install

install()
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from support.fake_vault import Vault
from support.cloud_assets import DtPlus
from dtflow.backends.fake import FakeBackend
from dtflow.services import session
from dtflow.services.credentials import SavedCredentials
from dtflow.ui.main_window import MainWindow
from dtflow.ui.theme import apply_theme

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
