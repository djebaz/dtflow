from dtflow.core.models import InventoryEntry, InventorySnapshot
from dtflow.services.catalogue import Catalogue


def _snapshot(names):
    entries = tuple(InventoryEntry(name, "unknown", name, None, "listed", "echo.files") for name in names)
    return InventorySnapshot(
        "drawthings-cloud", "s", "2026-10-10T00:00:00+00:00", True, entries, (("model_metadata", "absent"),)
    )


def test_metadata_less_cloud_files_are_models_only_with_community_evidence(tmp_path):
    catalogue = Catalogue(tmp_path)
    catalogue.set_inventory(_snapshot(["flux_2_dev_i8x.ckpt", "some_vae_f16.ckpt", "unknown_lora_f16.ckpt"]))
    view = catalogue.view()
    assert view.unclassified_checkpoints == ("flux_2_dev_i8x.ckpt",)
    assert view.other_files == ("some_vae_f16.ckpt", "unknown_lora_f16.ckpt")
