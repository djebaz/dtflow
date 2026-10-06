"""Wire conversion fidelity: what is serialized equals the resolved request, including seed zero."""

import pytest
from drawthings_py.configs.gen_config import GenConfig

from dtflow.backends.drawthings.wire import SAMPLER_WIRE_VALUE, build_generation_message, float32
from dtflow.core.models import BackendError, ErrorCategory, LoRASelection, ResolvedGenerationRequest, Sampler


def request(**overrides):
    values = {"request_id": "r", "prompt": "a prompt", "model_id": "model.ckpt", "seed": 0}
    return ResolvedGenerationRequest(**{**values, **overrides})


@pytest.mark.parametrize("seed", [0, 1, 4294967295])
@pytest.mark.parametrize("sampler,wire", [(Sampler.EULER_A, 1), (Sampler.DPMPP_2M_KARRAS, 0)])
@pytest.mark.parametrize("size", [(128, 128), (512, 768), (2048, 2048)])
def test_serialized_configuration_equals_the_resolved_request(seed, sampler, wire, size):
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
