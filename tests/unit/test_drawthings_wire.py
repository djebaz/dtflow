"""Wire conversion fidelity: what is serialized equals the resolved request, including seed zero."""

import pytest
from drawthings_py.configs.gen_config import GenConfig

from dtflow.backends.drawthings.wire import SAMPLER_WIRE_VALUE, build_generation_message, float32
from dtflow.core.model_profiles import profile_for
from dtflow.core.models import (
    BackendError,
    ErrorCategory,
    LoRASelection,
    PreparedAsset,
    ResolvedGenerationRequest,
    Sampler,
)
from dtflow.services.presets import SAMPLER_TO_DRAW_THINGS  # noqa: E402


def request(**overrides):
    values = {"request_id": "r", "prompt": "a prompt", "model_id": "model.ckpt", "seed": 0}
    return ResolvedGenerationRequest(**{**values, **overrides})


SEEDS = (0, 1, 4294967295)
SIZES = ((128, 128), (512, 768), (2048, 2048))


# Seed, sampler and size are serialized independently, so every sampler is covered once and the seed and size edge
# values rotate across those cases instead of forming a full cross product.
@pytest.mark.parametrize(
    "sampler,wire,seed,size",
    [
        (item, SAMPLER_TO_DRAW_THINGS[item.value], SEEDS[index % len(SEEDS)], SIZES[index // len(SEEDS) % len(SIZES)])
        for index, item in enumerate(Sampler)
    ],
)
def test_serialized_configuration_equals_the_resolved_request(sampler, wire, seed, size):
    resolved = request(
        seed=seed, sampler_id=sampler, width_px=size[0], height_px=size[1], steps=7, guidance=1.1, negative_prompt="no"
    )
    wire_request = build_generation_message(resolved, None)
    effective = dict(wire_request.effective_configuration)
    assert (effective["seed"], effective["width"], effective["height"], effective["steps"]) == (
        str(seed),
        str(size[0]),
        str(size[1]),
        "7",
    )
    assert effective["guidance"] == str(float32(1.1)) and float32(1.1) != 1.1
    assert effective["sampler_wire_value"] == str(wire) == str(SAMPLER_WIRE_VALUE[effective["sampler"]])
    assert (effective["batch_size"], effective["batch_count"], effective["strength"]) == ("1", "1", "1.0")
    assert effective["model"] == "model.ckpt" and effective["config_source"].startswith("drawthings-py ")
    message = wire_request.message
    assert (message.prompt, message.negative_prompt, message.chunked) == ("a prompt", "no", False)
    assert message.shared_secret is None and not message.image and not message.hints


def test_selected_loras_are_serialized_with_explicit_weights_and_verified():
    selections = (LoRASelection("a_lora_f16.ckpt", 0.7), LoRASelection("b_lora_f16.ckpt", 0))
    wire = build_generation_message(request(loras=selections), None)
    decoded = GenConfig.from_fbs(wire.message.configuration)
    assert [(x["file"], x["weight"], x["mode"]) for x in decoded["loras"]] == [
        ("a_lora_f16.ckpt", float32(0.7), "All"),
        ("b_lora_f16.ckpt", 0.0, "All"),
    ]
    assert dict(wire.effective_configuration)["loras"] == "a_lora_f16.ckpt@0.7,b_lora_f16.ckpt@0"
    assert dict(build_generation_message(request(), None).effective_configuration)["loras"] == ""


def test_shared_secret_is_sent_only_when_configured():
    assert build_generation_message(request(), "secret").message.shared_secret == "secret"


@pytest.mark.parametrize(
    "overrides,code",
    [
        ({"batch_size": 2}, "unsupported_batch"),
        ({"batch_count": 2}, "unsupported_batch"),
    ],
)
def test_unsupported_requests_are_rejected_before_serialization(overrides, code):
    with pytest.raises(BackendError) as caught:
        build_generation_message(request(**overrides), None)
    failure = caught.value.failure
    assert (failure.category, failure.code, failure.submission_stage.value) == (
        ErrorCategory.VALIDATION,
        code,
        "not_submitted",
    )


@pytest.mark.parametrize("weight", [0.6, 0.35, 1.2, 2.0])
def test_lora_weights_that_the_library_rounds_on_decode_still_verify(weight):
    wire = build_generation_message(request(loras=(LoRASelection("a_lora_f16.ckpt", weight),)), None)
    assert dict(wire.effective_configuration)["loras"] == f"a_lora_f16.ckpt@{weight}"


def test_prepared_loras_are_named_by_their_sha256_on_the_wire():
    digest = "ab" * 32
    prepared = PreparedAsset(
        "cloud", "origin", "mine_lora_f16.ckpt", "mine_lora_f16.ckpt", digest, 10, "r1", "2026-10-06T00:00:00Z"
    )
    selections = (LoRASelection("mine_lora_f16.ckpt", 0.5), LoRASelection("builtin_lora_f16.ckpt", 1.0))
    wire = build_generation_message(request(loras=selections, prepared_assets=(prepared,)), None)
    decoded = GenConfig.from_fbs(wire.message.configuration)
    assert [x["file"] for x in decoded["loras"]] == [digest, "builtin_lora_f16.ckpt"]


def test_prepared_custom_loras_are_described_in_the_override_with_the_base_version():
    import json

    from dtflow.core.models import PreparedAsset

    asset = PreparedAsset("cloud", "o", "mine_lora_f16.ckpt", "ref", "a" * 64, 1, "rc", "2026-01-01T00:00:00+00:00")
    selections = (LoRASelection("mine_lora_f16.ckpt", 1.0), LoRASelection("other_lora_f16.ckpt", 1.0))
    sent = build_generation_message(request(loras=selections, prepared_assets=(asset,)), None, "z_image")
    assert json.loads(sent.message.override.loras) == [
        {"name": "mine_lora_f16.ckpt", "file": "a" * 64, "prefix": "", "version": "z_image"}
    ]
    assert not build_generation_message(
        request(loras=selections, prepared_assets=(asset,)), None
    ).message.override.loras
    # The catalogue's family name is mapped to Draw Things' version string; an exact description's version wins.
    flux = build_generation_message(request(loras=selections, prepared_assets=(asset,)), None, "flux_1")
    assert json.loads(flux.message.override.loras)[0]["version"] == "flux1"
    exact = build_generation_message(
        request(loras=selections, prepared_assets=(asset,)), None, "flux_2", {"version": "flux2_9b", "file": "x"}
    )
    assert json.loads(exact.message.override.loras)[0]["version"] == "flux2_9b"
    assert not build_generation_message(request(loras=selections), None, "z_image").message.override.loras


def test_bundled_catalogue_supplies_a_base_version_when_inventory_has_none():
    from dtflow.backends.drawthings.versions import bundled_base_version

    assert bundled_base_version("z_image_turbo_1.0_q6p.ckpt") == "z_image"
    assert bundled_base_version("unknown.ckpt") is None


def test_flux_profiles_send_the_family_fields_and_other_models_stay_bare():
    dev = dict(
        build_generation_message(
            request(model_id="flux_1_dev_q8p.ckpt", sampler_id=Sampler.EULER_A_TRAILING), None
        ).effective_configuration
    )
    schnell = dict(
        build_generation_message(
            request(model_id="flux_1_schnell_f16.ckpt", sampler_id=Sampler.EULER_A_TRAILING), None
        ).effective_configuration
    )
    assert (dev["guidance_embed"], dev["resolution_dependent_shift"], dev["t5_text_encoder"]) == ("4.5", "True", "True")
    assert (schnell["guidance_embed"], schnell["resolution_dependent_shift"]) == ("3.5", "False")
    # Unknown checkpoints use the family default; epsilon models send no extras.
    assert profile_for("someone_flux_finetune_q8p.ckpt", "flux_1").name == "FLUX.1 [dev]"
    assert profile_for("sd_v1.5_f16.ckpt").extras == () and profile_for("unknown.ckpt") is None
    assert Sampler.EULER_A_TRAILING in profile_for("flux_1_dev_q8p.ckpt").samplers
    assert Sampler.EULER_A not in profile_for("flux_1_dev_q8p.ckpt").samplers
