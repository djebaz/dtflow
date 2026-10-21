"""Exact-file metadata composition and provider observation never authorize generation."""

from dtflow.core.models import InventoryEntry, InventorySnapshot
from dtflow.services.catalogue import Catalogue
from dtflow.services import checkpoint_metadata as resolver


def _inventory(*files):
    return InventorySnapshot(
        "drawthings-cloud",
        "offline-server",
        "2026-10-21T12:00:00+00:00",
        True,
        tuple(InventoryEntry(name, "unknown", name, None, "listed", "echo.files") for name in files),
        (("model_metadata", "absent"),),
    )


def test_exact_checkpoint_spec_does_not_cross_quantization(monkeypatch, tmp_path):
    published = {"file": "sample_q8p.ckpt", "version": "flux2", "modifier": "none"}
    monkeypatch.setattr(resolver, "exact_model_spec", lambda name: published if name == published["file"] else None)
    catalogue = Catalogue(tmp_path)
    matched = resolver.resolve_checkpoint_metadata("sample_q8p.ckpt", catalogue=catalogue, inventory=None)
    other = resolver.resolve_checkpoint_metadata("sample_q6p.ckpt", catalogue=catalogue, inventory=None)
    assert matched.published_spec_json is not None
    assert "flux2" in matched.as_text()
    assert other.published_spec_json is None
    assert "Not available for this exact checkpoint" in other.as_text()
    assert "Q6P" in other.as_text()


def test_provider_evidence_is_separate_from_published_metadata(monkeypatch, tmp_path):
    monkeypatch.setattr(resolver, "exact_model_spec", lambda _: None)
    catalogue = Catalogue(tmp_path)
    identity = "offline_q8p.ckpt"
    listed = resolver.resolve_checkpoint_metadata(identity, catalogue=catalogue, inventory=_inventory(identity))
    absent = resolver.resolve_checkpoint_metadata(identity, catalogue=catalogue, inventory=_inventory("other.ckpt"))
    unknown = resolver.resolve_checkpoint_metadata(identity, catalogue=catalogue, inventory=None)
    assert "Listed in last observation" in listed.as_text()
    assert "Not listed in last observation" in absent.as_text()
    assert "Not observed" in unknown.as_text()
    assert all(item.published_spec_json is None for item in (listed, absent, unknown))


def test_empty_selection_never_resolves_another_model(tmp_path):
    result = resolver.resolve_checkpoint_metadata("", catalogue=Catalogue(tmp_path), inventory=None)
    assert result.fields == (("Checkpoint", "Not selected", "Selection"),)
