from dtflow.core.models import InventoryEntry, InventorySnapshot
from dtflow.services.catalogue import Catalogue


def _snapshot(names):
    entries = tuple(InventoryEntry(name, "unknown", name, None, "listed", "echo.files") for name in names)
    return InventorySnapshot(
        "drawthings-cloud", "s", "2026-10-10T00:00:00+00:00", True, entries, (("model_metadata", "absent"),)
    )


def test_metadata_less_cloud_files_use_explicit_checkpoint_classification(tmp_path):
    catalogue = Catalogue(tmp_path)
    catalogue.set_inventory(
        _snapshot(
            [
                "flux_2_dev_i8x.ckpt",
                "flux_3_klein_i8x.ckpt",
                "moss_image_sft_i8x.ckpt",
                "fasth3_omni_i4x.ckpt",
                "some_vae_f16.ckpt",
                "unknown_lora_f16.ckpt",
            ]
        )
    )
    view = catalogue.view()
    assert {entry.identifier: entry.base_model for entry in view.checkpoints} == {
        "flux_2_dev_i8x.ckpt": "flux_2",
        "flux_3_klein_i8x.ckpt": "flux_3",
        "moss_image_sft_i8x.ckpt": "moss_image",
        "fasth3_omni_i4x.ckpt": "minimax_h3",
    }
    assert not view.unclassified_checkpoints
    assert view.other_files == ("unknown_lora_f16.ckpt",)  # The VAE is a hidden companion file.
