from __future__ import annotations

import importlib.util
from pathlib import Path

from drawthings_py.generated.dt_grpc import image_service

MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "drawthings" / "grpc_capabilities_probe.py"
SPEC = importlib.util.spec_from_file_location("grpc_capabilities_probe", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


def test_decode_metadata_override_preserves_lora_fields() -> None:
    override = image_service.MetadataOverride(
        loras=(
            b'[{"name":"Custom","file":"custom_lora_f16.ckpt","prefix":"trigger",'
            b'"version":"z_image","weight":{"value":0.8,"lower_bound":0.0,"upper_bound":1.5},'
            b'"note":"keep-me"}]'
        )
    )

    decoded = probe.decode_metadata_override(override)

    loras = decoded["loras"]
    assert loras["present"] is True
    assert loras["count"] == 1
    assert loras["items"][0]["file"] == "custom_lora_f16.ckpt"
    assert loras["items"][0]["weight"]["value"] == 0.8
    assert loras["items"][0]["note"] == "keep-me"
    assert "file" in loras["field_names"]
    assert "weight" in loras["field_names"]


def test_files_exist_results_preserve_hash_alignment() -> None:
    digest = bytes.fromhex("ab" * 32)
    response = image_service.FileExistenceResponse(
        files=["a.ckpt", "custom_lora.json", "missing.ckpt"],
        existences=[True, True, False],
        hashes=[b"", digest, b""],
    )

    results = probe.files_exist_results(
        response,
        hash_requested={"custom_lora.json"},
    )

    assert results == [
        {
            "file": "a.ckpt",
            "exists": True,
            "hash_requested": False,
            "sha256": None,
            "hash_byte_count": 0,
        },
        {
            "file": "custom_lora.json",
            "exists": True,
            "hash_requested": True,
            "sha256": "ab" * 32,
            "hash_byte_count": 32,
        },
        {
            "file": "missing.ckpt",
            "exists": False,
            "hash_requested": False,
            "sha256": None,
            "hash_byte_count": 0,
        },
    ]


def test_probe_contract_excludes_mutating_or_generation_rpcs() -> None:
    assert set(probe.SAFE_RPCS) == {"Echo", "FilesExist", "Pubkey", "Hours"}
    assert set(probe.DELIBERATELY_SKIPPED_RPCS) == {"GenerateImage", "UploadFile"}
    assert set(probe.SAFE_RPCS).isdisjoint(probe.DELIBERATELY_SKIPPED_RPCS)


def test_metadata_registry_candidates_include_custom_lora_registry() -> None:
    candidates = dict(probe.METADATA_FILE_CANDIDATES)

    assert candidates["custom_lora.json"] == "local_lora_registry"
    assert candidates["custom.json"] == "local_model_registry"
    assert candidates["loras.json"] == "community_cache_basename"
    assert candidates["loras_sha256.json"] == "community_checksum_cache_basename"
