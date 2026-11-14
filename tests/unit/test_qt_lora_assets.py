"""LoRA selection and cloud-asset presentation (5A-O3, 5A-O5) against in-memory fixtures; nothing is uploaded for real.

Compatibility rules are owned by the preflight tests and receipt/upload semantics by the asset suites; these cover only
what the page renders and which service each action calls.
"""

import asyncio
import dataclasses
import json
import os

import httpx
import pytest

pytest.importorskip("pytestqt")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QCheckBox, QLineEdit, QPushButton, QSlider, QWidget  # noqa: E402
from support.cloud_assets import API_KEY, DtPlus, lora_file  # noqa: E402
from support.fake_vault import Vault  # noqa: E402

from dtflow.backends.fake import FakeBackend  # noqa: E402
from dtflow.core.models import InventoryEntry, InventorySnapshot  # noqa: E402
from dtflow.core.models import AssetContent  # noqa: E402
from dtflow.persistence.assets import AssetRegistry  # noqa: E402
from dtflow.services import session  # noqa: E402
from dtflow.services.credentials import SavedCredentials  # noqa: E402
from dtflow.services.dt_storage import storage_size  # noqa: E402
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


def settled(window):
    return lambda: window.generation_page.generateButton.isEnabled() and not window.activity.generating


def add_lora(window, qtbot, name, weight=0.5):
    window.generation_page.addLoraButton.click()
    editor = window.inline._editor
    editor.findChild(QLineEdit, "loraSelectionCustomIdentifier").setText(name)
    editor.findChild(QPushButton, "loraSelectionCustomAddButton").click()
    row = next(r for r in editor.findChildren(QWidget, "loraSelectionRow") if r.property("identifier") == name)
    row.findChild(QSlider, "loraSelectionSlider").setValue(round(weight * 20))
    window.inline.form.inlineEditorAcceptButton.click()


def test_lora_diagnostics_are_rendered_not_computed_and_block_generation(window, qtbot, monkeypatch):
    monkeypatch.setattr(session, "create_backend", lambda selection, **_: LoraFake())
    page, panel = window.generation_page, window.generation.lora
    page.refreshInventoryButton.click()
    qtbot.waitUntil(lambda: page.refreshInventoryButton.isEnabled(), timeout=5000)
    page.addLoraButton.click()
    editor = window.inline._editor
    rows = {r.property("identifier"): r for r in editor.findChildren(QWidget, "loraSelectionRow")}
    assert set(rows) == {"ok", "bad", "unk"}
    assert not any(r.findChild(QCheckBox).isChecked() for r in rows.values())
    assert not panel.loras  # nothing commits until Apply
    rows["ok"].findChild(QCheckBox).setChecked(True)
    rows["ok"].findChild(QSlider, "loraSelectionSlider").setValue(14)
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
    qtbot.waitUntil(settled(window), timeout=5000)
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


MANAGED = "Style_ZiT_lora_f16.ckpt"  # what Prepare names style.ckpt once its short name and architecture are approved


def receipt_bound(page):
    """The selection's first row names a confirmed receipt in its tooltip (the list is briefly empty while redrawn)."""
    item = page.loraList.item(0)
    return item is not None and "Confirmed receipt" in item.toolTip()


def prepare(window, qtbot, file, *, choose=True):
    panel, page = window.generation.lora, window.generation_page
    asked = []

    def select_file(parent, directory):
        asked.append(file.name)
        return [str(file)] if choose else []

    panel.choose_asset_file = select_file
    original = page.prepareAssetButton.text()
    page.prepareAssetButton.click()
    assert page.prepareAssetButton.text() == original
    assert not page.prepareAssetButton.property("confirmationPending")
    if choose:
        # Choosing a file only opens the metadata editor: approve its one row (first offered architecture), then Prepare.
        editor = panel._prepare_editor
        qtbot.waitUntil(lambda: editor.inspected, timeout=30000)
        editor.rows[0].widget.prepareArchitecture.setCurrentIndex(1)
        accept = window.inline.form.inlineEditorAcceptButton
        qtbot.waitUntil(lambda: accept.isEnabled() and accept.text().startswith("Prepare 1 ready"), timeout=30000)
        accept.click()
        # Prepare completion is independent of whether a checkpoint enables Generate.
        qtbot.waitUntil(lambda: not panel.preparing and page.prepareAssetButton.isEnabled(), timeout=30000)
    return asked


def test_prepare_starts_after_file_selection_and_lists_a_local_receipt(window, cloud, qtbot, tmp_path):
    page = window.generation_page
    assert "upload" in UPLOAD_NOTICE and "credits" in UPLOAD_NOTICE and "consume credits" in UPLOAD_NOTICE
    assert UPLOAD_NOTICE == page.prepareAssetButton.toolTip()
    assert "not DT+ account inventory" in page.assetNoteLabel.text()
    assert "do not consume credits" in page.assetNoteLabel.text()
    file = lora_file(tmp_path)
    assert prepare(window, qtbot, file, choose=False) == ["style.ckpt"]
    assert cloud.puts == [] and cloud.api_calls == [] and page.assetList.count() == 0  # picker cancelled

    prepare(window, qtbot, file)
    qtbot.waitUntil(lambda: page.assetList.count() == 1, timeout=30000)
    assert "confirmed" in page.assetList.item(0).text() and "Style[ZiT]" in page.assetList.item(0).text()
    assert page.assetStatusLabel.text().startswith(("Receipt confirmed", "Receipt bound")) and len(cloud.puts) == 1


def test_unconfirmed_upload_is_unknown_with_its_code_and_not_retried(window, cloud, qtbot, tmp_path):
    cloud.store_on_put = False  # the PUT "succeeds" but the server never lists the file
    prepare(window, qtbot, lora_file(tmp_path))
    page = window.generation_page
    qtbot.waitUntil(lambda: page.assetList.count() == 1, timeout=30000)
    assert page.assetStatusLabel.text().startswith("Outcome unknown")
    visible = page.assetStatusLabel.text().replace("\u200b", "")  # ignore intentional identifier wrapping
    assert "asset_upload_unconfirmed" in visible and "not retried" in visible
    assert "unknown" in page.assetList.item(0).text() and len(cloud.puts) == 1


def test_asset_writer_contention_is_a_clear_busy_message(window, cloud, qtbot, tmp_path):
    loop = asyncio.new_event_loop()
    other = AssetRegistry(window.state_dir, writer=True)
    loop.run_until_complete(other.open())
    try:
        prepare(window, qtbot, lora_file(tmp_path))
        page = window.generation_page
        qtbot.waitUntil(lambda: page.assetStatusLabel.text().startswith("Assets busy"), timeout=30000)
        assert cloud.puts == []
        # Inspection may read advisory key headroom without the writer lock. The actual selected file must
        # still never negotiate or upload while another writer owns the registry.
        assert all(call["entry"]["file"] == "dtflow-storage-probe.ckpt" for call in cloud.api_calls)
    finally:
        loop.run_until_complete(other.aclose())
        loop.close()


def test_prepare_adds_and_binds_the_lora_and_removing_it_drops_the_binding(window, cloud, qtbot, tmp_path):
    page = window.generation_page
    prepare(window, qtbot, lora_file(tmp_path))
    qtbot.waitUntil(lambda: page.loraList.count() == 1, timeout=30000)  # Prepare adds the LoRA and binds its receipt
    assert page.assetStatusLabel.text().startswith("Receipt bound") and receipt_bound(page)
    assert len(window.generation.lora.receipt_ids()) == 1
    page.loraList.setCurrentRow(0)
    page.removeLoraButton.click()
    assert window.generation.lora.receipt_ids() == ()  # removing the LoRA drops its binding
    window.form.backendCombo.setCurrentText("fake")  # receipts are cloud-only; actions are disabled elsewhere
    assert not page.prepareAssetButton.isEnabled() and page.assetStatusLabel.text().startswith("Cloud only")


class StorageQuotaFixture(DtPlus):
    """Synthetic per-key quota on the existing HTTP mock; never opens a socket."""

    def __init__(self):
        super().__init__()
        self.full = True
        self.deletes = []

    async def api(self, request):
        if request.url.path == "/delete_uploaded_files":
            keys = json.loads(request.content)["keys"]
            assert keys == ["fixture-old-storage-key"]
            self.deletes.append(keys)
            self.full = False
            return httpx.Response(200, json={"code": 200})
        if request.url.path == "/get_byom_urls":
            entry = json.loads(request.content)["files"][0]
            if entry["file"] == "dtflow-storage-probe.ckpt" or self.full:
                self.api_calls.append({"path": request.url.path, "entry": entry})
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "storageLimitExceeded": {
                                "freeSpace": 0 if self.full else 1_000_000,
                                "files": (
                                    [{"file": "old.ckpt", "key": "fixture-old-storage-key", "size": 1_000_000}]
                                    if self.full
                                    else []
                                ),
                            }
                        }
                    },
                )
        return await super().api(request)


@pytest.fixture
def quota(window, monkeypatch):
    fixture = StorageQuotaFixture()
    monkeypatch.setattr(session, "create_backend", lambda *args, **kwargs: fixture.backend())
    window.form.backendCombo.setCurrentText("cloud")
    return fixture


def test_storage_refusal_free_space_and_explicit_reprepare_without_reselection(window, quota, qtbot, tmp_path):
    # The UI and in-memory backend must agree on the listed owner before deletion can be armed.
    window._saved.save(API_KEY, "Fixture key")
    window.form.show()
    file = lora_file(tmp_path, data=bytes(range(256)) * 512)
    assert prepare(window, qtbot, file) == [file.name]
    panel = window.generation.lora
    editor = panel._prepare_editor
    assert editor is not None and editor.active()
    row = editor.rows[0]
    assert row.outcome == "Failed" and row.storage_failed and row.can_reprepare
    assert not row.reprepare  # storage recovery did not grant accepted-name replacement
    assert not row.widget.prepareReprepare.isHidden()
    assert f"Last refused file needs {storage_size(file.stat().st_size)}" in editor.form.prepareStorageStatus.text()
    assert "0.0 MB free" in editor.form.prepareStorageStatus.text()
    assert quota.put_attempts == 0 and quota.deletes == []

    # Storage is a smaller child popup above the still-visible, inactive Prepare editor.
    editor.form.prepareStorageButton.click()
    overlay = window.dt_storage_overlay
    qtbot.waitUntil(lambda: not overlay._busy and overlay.form.dtStorageList.count() == 1, timeout=10000)
    assert not overlay.overlay.isHidden()
    assert overlay.overlay.parentWidget() is window.inline.strip.form
    assert panel._prepare_editor is editor and editor.active()
    assert window.inline.form.isVisible() and not window.inline.form.isEnabled()
    assert window.inline.strip.scrim.isVisible()
    assert overlay.form.width() < window.inline.strip.form.width()
    assert overlay.form.height() < window.inline.strip.form.height()
    overlay.search.setFocus()
    qtbot.waitUntil(lambda: QApplication.focusWidget() is overlay.search, timeout=5000)
    overlay.form.dtStorageList.item(0).setSelected(True)
    overlay.form.dtStorageDelete.click()
    assert quota.deletes == [] and overlay._armed
    overlay.form.dtStorageDelete.click()
    qtbot.waitUntil(lambda: len(quota.deletes) == 1 and not overlay._busy, timeout=10000)
    assert quota.put_attempts == 0
    overlay.form.dtStorageClose.click()
    qtbot.waitUntil(lambda: "1.0 MB free" in editor.form.prepareStorageStatus.text(), timeout=10000)
    assert not window.inline.form.isHidden()
    assert panel._prepare_editor is editor and editor.active()
    assert row.outcome == "Failed" and quota.put_attempts == 0  # freeing never retries Prepare

    row.widget.prepareReprepare.click()
    assert not row.admitted and not row.reprepare and row.outcome is None
    assert quota.put_attempts == 0  # row unlock is not upload consent either
    accept = window.inline.form.inlineEditorAcceptButton
    qtbot.waitUntil(lambda: accept.isEnabled() and accept.text().startswith("Prepare 1 ready"), timeout=10000)
    # A picker invocation would fail: the same selected row must be reused.
    panel.choose_asset_file = lambda *_: pytest.fail("Recovery must not reopen the file picker")
    accept.click()
    qtbot.waitUntil(lambda: not panel.preparing, timeout=30000)
    assert len(quota.deletes) == 1 and quota.put_attempts == 1
    assert panel._prepare_last[1] is None and panel._prepare_last[0]["state"] == "confirmed"


def test_storage_recovery_cannot_open_or_retry_under_changed_owner(window, quota, qtbot, tmp_path):
    prepare(window, qtbot, lora_file(tmp_path))
    panel = window.generation.lora
    editor = panel._prepare_editor
    row = editor.rows[0]
    panel.invalidate_prepare_ownership()

    def ordinary_calls():
        return [c for c in quota.api_calls if c["entry"]["file"] != "dtflow-storage-probe.ckpt"]

    before = len(ordinary_calls())
    editor.form.prepareStorageButton.click()
    assert not window.dt_storage_overlay.is_open()
    row.widget.prepareReprepare.click()
    window.inline.form.inlineEditorAcceptButton.click()
    assert not panel.preparing and quota.put_attempts == 0 and quota.deletes == []
    assert len(ordinary_calls()) == before


def test_storage_snapshot_does_not_recover_interrupted_registry_operations(tmp_path, monkeypatch):
    fixture = StorageQuotaFixture()
    monkeypatch.setattr(session, "create_backend", lambda *args, **kwargs: fixture.backend())

    async def check():
        state = tmp_path / "read-only-state"
        registry = AssetRegistry(state, writer=True)
        await registry.open()
        content = AssetContent("lora", "pending.ckpt", "a" * 64, 100)
        try:
            asset_id, _ = await registry.begin(content, "cloud", fixture.origin, "prepare")
        finally:
            await registry.aclose()
        listing = await session.storage_listing(state, content)
        assert listing.free_space == 0 and fixture.put_attempts == 0
        reader = AssetRegistry(state)
        await reader.open()
        try:
            after = await reader.get(asset_id)
            assert after["state"] == "intent"  # a writer would have changed it to unknown/interrupted
        finally:
            await reader.aclose()

    asyncio.run(check())


def test_suspended_prepare_editor_is_not_resurrected_after_replacement(window, qtbot):
    first, replacement = QWidget(), QWidget()
    window.inline.editor("Original", first, "Apply", lambda: None)
    assert window.inline.suspend_editor(first)
    assert not window.inline.form.isEnabled() and not window.inline.active()
    window.inline.editor("Replacement", replacement, "Apply", lambda: None)
    assert not window.inline.resume_editor(first)
    assert window.inline._editor is replacement and window.inline.active()
    assert window.inline.form.isEnabled()


def test_prepare_source_space_warning_is_amber_and_advisory(window, quota, qtbot, tmp_path):
    prepare(window, qtbot, lora_file(tmp_path))
    editor = window.generation.lora._prepare_editor
    row = editor.rows[0]
    row.widget.prepareReprepare.click()
    # Presentation-only source scenario: no conversion is submitted by this check.
    row.source_kind = "safetensors"
    row.total_bytes = 1_000_000
    editor.set_storage(500_000)
    editor.refresh()
    label = editor.form.prepareStorageStatus
    assert label.property("state") == "warning"
    assert "This Prepare will use about 1.0 MB / 0.5 MB free" in label.text()
    assert "200.0%" in label.text() and "+ 5%" not in label.text()
    assert window.inline.form.inlineEditorAcceptButton.isEnabled()
    editor.set_storage(10_500_000)
    editor.refresh()
    assert label.property("state") == "info"
    assert window.inline.form.inlineEditorAcceptButton.isEnabled()
    editor.set_storage(1_250_000)
    editor.refresh()
    assert label.property("state") == "info"
    assert "only 80.0%" in label.text()
    editor.set_storage(1_111_111)
    editor.refresh()
    assert label.property("state") == "warning"
    assert "90.0%" in label.text() and "only" not in label.text()
    assert label.text().endswith("Warning: this leaves little free space.")
    assert window.inline.form.inlineEditorAcceptButton.isEnabled()
    editor.set_storage(None)
    editor.refresh()
    assert "free space unknown" in label.text()
    assert window.inline.form.inlineEditorAcceptButton.isEnabled()
    assert quota.put_attempts == 0
