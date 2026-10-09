"""LoRA selection and cloud-asset presentation (5A-O3, 5A-O5) against in-memory fixtures; nothing is uploaded for real.

Compatibility rules are owned by the preflight tests and receipt/upload semantics by the asset suites; these cover only
what the page renders and which service each action calls.
"""

import asyncio
import dataclasses
import os

import pytest

pytest.importorskip("pytestqt")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QDoubleSpinBox, QLineEdit, QListWidget  # noqa: E402
from support.cloud_assets import DtPlus, lora_file  # noqa: E402
from support.fake_vault import Vault  # noqa: E402

from dtflow.backends.fake import FakeBackend  # noqa: E402
from dtflow.core.models import InventoryEntry, InventorySnapshot  # noqa: E402
from dtflow.persistence.assets import AssetRegistry  # noqa: E402
from dtflow.services import session  # noqa: E402
from dtflow.services.credentials import SavedCredentials  # noqa: E402
from dtflow.ui.lora_panel import UPLOAD_NOTICE  # noqa: E402
from dtflow.ui.main_window import MainWindow  # noqa: E402


class LoraFake(FakeBackend):
    """Fake whose inventory lists LoRAs of known, mismatching and unknown architecture."""

    def __init__(self):
        super().__init__()
        self._capabilities = dataclasses.replace(self._capabilities, lora_support="selection")

    async def get_inventory(self, refresh: bool = False) -> InventorySnapshot:
        def entry(name, kind, architecture, weight=None):
            return InventoryEntry(name, kind, name, architecture, "present", "test", None, None, weight)

        return InventorySnapshot(
            "fake",
            "fixture-v1",
            "2026-10-04T00:00:00+00:00",
            refresh,
            (
                entry("fake-fixture", "model", "sd"),
                entry("ok", "lora", "sd", 0.7),
                entry("bad", "lora", "sdxl"),
                entry("unk", "lora", None),
            ),
        )


@pytest.fixture
def window(qtbot, tmp_path):
    shell = MainWindow(saved=SavedCredentials(Vault()), environ={}, state_dir=tmp_path / "state")
    qtbot.addWidget(shell.form)
    shell.form.backendCombo.setCurrentText("fake")
    shell.generation_page.promptEdit.setPlainText("a cat")
    yield shell
    shell.runner.shutdown()


def settled(page):
    return lambda: page.generateButton.isEnabled() and not page.cancelButton.isEnabled()


def add_lora(window, qtbot, name, weight=0.5):
    window.generation_page.addLoraButton.click()
    editor = window.inline._editor
    editor.findChild(QLineEdit, "inlineSelectionIdentifier").setText(name)
    editor.findChild(QDoubleSpinBox, "inlineSelectionWeight").setValue(weight)
    window.inline.form.inlineEditorAcceptButton.click()


def test_lora_diagnostics_are_rendered_not_computed_and_block_generation(window, qtbot, monkeypatch):
    monkeypatch.setattr(session, "create_backend", lambda selection, **_: LoraFake())
    page, panel = window.generation_page, window.generation.lora
    page.refreshInventoryButton.click()
    qtbot.waitUntil(lambda: page.refreshInventoryButton.isEnabled(), timeout=5000)
    page.addLoraButton.click()
    editor = window.inline._editor
    choices = editor.findChild(QListWidget, "inlineSelectionChoices")
    assert {choices.item(i).text() for i in range(choices.count())} == {"ok", "bad", "unk"}
    choices.setCurrentRow(next(i for i in range(choices.count()) if choices.item(i).text() == "ok"))
    weight = editor.findChild(QDoubleSpinBox, "inlineSelectionWeight")
    assert weight.value() == 0.7  # inventory suggestion, alongside identifier rather than a second dialog
    assert (weight.minimum(), weight.maximum(), weight.singleStep(), weight.decimals()) == (0.0, 3.0, 0.1, 1)
    assert not panel.loras  # selection alone does not commit
    window.inline.form.inlineEditorAcceptButton.click()
    assert panel.loras[0].weight == 0.7
    for name in ("bad", "unk"):
        add_lora(window, qtbot, name)
    assert page.loraList.count() == 3

    page.checkLorasButton.click()
    label = page.compatibilityStatusLabel
    qtbot.waitUntil(lambda: label.text().startswith("Blocked before dispatch"), timeout=5000)
    text = label.text()
    assert "bad: architecture mismatch" in text and "ok: architecture match" in text
    assert "unk: architecture unknown" in text and "application unconfirmed" in text
    assert label.property("state") == "error"

    page.generateButton.click()  # the shared service rejects before any attempt exists; the page shows its codes
    qtbot.waitUntil(settled(page), timeout=5000)
    assert page.generationStatusLabel.text().startswith("Request rejected")
    assert "bad: architecture_mismatch" in page.generationStatusLabel.text()
    assert not (window.state_dir / "outputs").exists()

    page.loraList.setCurrentRow(1)  # remove "bad"
    page.removeLoraButton.click()
    assert [window.generation.lora.loras[i].identifier for i in range(2)] == ["ok", "unk"]
    page.checkLorasButton.click()
    qtbot.waitUntil(lambda: label.text().startswith("Compatibility not confirmed"), timeout=5000)
    assert label.property("state") == "unknown"


def test_invalid_selection_is_reported_where_it_belongs(window, qtbot):
    page = window.generation_page
    page.generationBackendCombo.setCurrentText("fake")
    add_lora(window, qtbot, "x", 0.5)
    page.seedEdit.setText("not-a-seed")
    page.checkLorasButton.click()
    assert page.compatibilityStatusLabel.text().startswith("Cannot check")
    assert page.seedEdit.property("invalid") is True


@pytest.fixture
def cloud(window, monkeypatch):
    fixture = DtPlus()
    monkeypatch.setattr(session, "create_backend", lambda *args, **kwargs: fixture.backend())
    window.form.backendCombo.setCurrentText("cloud")
    return fixture


def prepare(window, qtbot, file, confirm=True):
    panel, page = window.generation.lora, window.generation_page
    asked = []
    panel.choose_asset_file = lambda parent, directory: asked.append(file.name) or str(file)
    original = page.prepareAssetButton.text()
    page.prepareAssetButton.click()
    assert page.prepareAssetButton.text() == "Confirm"
    assert page.prepareAssetButton.property("confirmationPending") is True
    details = window.inline.form.inlineTextBrowser.toPlainText()
    assert file.name in details and UPLOAD_NOTICE in details
    if confirm:
        page.prepareAssetButton.click()
        qtbot.waitUntil(lambda: page.generateButton.isEnabled(), timeout=5000)
    else:
        window.inline.form.inlineDismissButton.click()
    assert page.prepareAssetButton.text() == original
    assert not page.prepareAssetButton.property("confirmationPending")
    return asked


def test_prepare_requires_confirmation_then_lists_a_local_receipt(window, cloud, qtbot, tmp_path):
    page = window.generation_page
    assert "upload" in UPLOAD_NOTICE and "credits" in UPLOAD_NOTICE and "consume credits" in UPLOAD_NOTICE
    assert "not DT+ account inventory" in page.assetNoteLabel.text()
    file = lora_file(tmp_path)
    assert prepare(window, qtbot, file, confirm=False) == ["style.ckpt"]
    assert cloud.puts == [] and cloud.api_calls == [] and page.assetList.count() == 0  # declined: nothing happened

    prepare(window, qtbot, file)
    qtbot.waitUntil(lambda: page.assetList.count() == 1, timeout=5000)
    assert "confirmed" in page.assetList.item(0).text() and "style.ckpt" in page.assetList.item(0).text()
    assert page.assetStatusLabel.text().startswith(("Receipt confirmed", "Receipt bound")) and len(cloud.puts) == 1

    page.loraList.setCurrentRow(0)  # Prepare added and bound the LoRA; adding it again reconciles the receipt
    page.removeLoraButton.click()
    add_lora(window, qtbot, "style.ckpt", 0.8)
    qtbot.waitUntil(
        lambda: "[receipt " in page.loraList.item(0).text() and page.generateButton.isEnabled(), timeout=5000
    )
    cloud.stored.clear()  # the server lost the file: the receipt goes stale and reconcile still never uploads
    page.loraList.setCurrentRow(0)
    page.removeLoraButton.click()
    add_lora(window, qtbot, "style.ckpt", 0.8)
    qtbot.waitUntil(lambda: page.assetStatusLabel.text().startswith("Reconciled: upload required"), timeout=5000)
    qtbot.waitUntil(lambda: "unknown" in page.assetList.item(0).text(), timeout=5000)
    assert len(cloud.puts) == 1 and window.generation.lora.receipt_ids() == ()


def test_unconfirmed_upload_is_unknown_with_its_code_and_not_retried(window, cloud, qtbot, tmp_path):
    cloud.store_on_put = False  # the PUT "succeeds" but the server never lists the file
    prepare(window, qtbot, lora_file(tmp_path))
    page = window.generation_page
    qtbot.waitUntil(lambda: page.assetList.count() == 1, timeout=5000)
    assert page.assetStatusLabel.text().startswith("Outcome unknown")
    assert "asset_upload_unconfirmed" in page.assetStatusLabel.text() and "not retried" in page.assetStatusLabel.text()
    assert "unknown" in page.assetList.item(0).text() and len(cloud.puts) == 1


def test_asset_writer_contention_is_a_clear_busy_message(window, cloud, qtbot, tmp_path):
    loop = asyncio.new_event_loop()
    other = AssetRegistry(window.state_dir, writer=True)
    loop.run_until_complete(other.open())
    try:
        prepare(window, qtbot, lora_file(tmp_path))
        page = window.generation_page
        qtbot.waitUntil(lambda: page.assetStatusLabel.text().startswith("Assets busy"), timeout=5000)
        assert cloud.puts == [] and cloud.api_calls == []
    finally:
        loop.run_until_complete(other.aclose())
        loop.close()


def test_prepare_adds_and_binds_the_lora_and_adding_it_again_reconciles_and_binds(window, cloud, qtbot, tmp_path):
    page = window.generation_page
    prepare(window, qtbot, lora_file(tmp_path))
    qtbot.waitUntil(lambda: page.loraList.count() == 1, timeout=5000)  # Prepare adds the LoRA and binds its receipt
    assert page.assetStatusLabel.text().startswith("Receipt bound") and "[receipt " in page.loraList.item(0).text()
    assert len(window.generation.lora.receipt_ids()) == 1
    page.loraList.setCurrentRow(0)
    page.removeLoraButton.click()
    assert window.generation.lora.receipt_ids() == ()  # removing the LoRA drops its binding
    add_lora(window, qtbot, "style.ckpt", 0.8)
    qtbot.waitUntil(lambda: "[receipt " in page.loraList.item(0).text(), timeout=5000)
    assert len(window.generation.lora.receipt_ids()) == 1
    window.form.backendCombo.setCurrentText("fake")  # receipts are cloud-only; actions are disabled elsewhere
    assert not page.prepareAssetButton.isEnabled() and page.assetStatusLabel.text().startswith("Cloud only")
