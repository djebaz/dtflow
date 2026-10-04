"""Form-to-request mapping and the error-to-field table; Qt-free, the shared resolver stays authoritative."""

import json
from dataclasses import replace

import pytest

from dtflow.core.models import LoRASelection
from dtflow.ui.generation_presenter import (
    FormError,
    FormValues,
    build_request,
    read_configuration,
    save_configuration,
    values_from_request,
)

VALID = FormValues(prompt="a cat", seed="7", width=128, height=128, steps=4, guidance=1.0)


@pytest.mark.parametrize(
    "change,widget",
    [
        ({"prompt": "  "}, "promptEdit"),
        ({"seed": "abc"}, "seedEdit"),
        ({"seed": "-1"}, "seedEdit"),
        ({"width": 100}, "widthSpinBox"),
        ({"height": 4096}, "heightSpinBox"),
        ({"steps": 500}, "stepsSpinBox"),
        ({"guidance": 99.0}, "guidanceSpinBox"),
        ({"sampler": "nope"}, "samplerCombo"),
    ],
)
def test_validation_errors_map_back_to_their_widget(change, widget):
    values = replace(VALID, **change)
    with pytest.raises(FormError) as caught:
        build_request(values, backend="fake")
    assert caught.value.widget == widget


def test_model_is_required_off_fake_and_defaults_on_fake():
    assert build_request(VALID, backend="fake").model_id == "fake-fixture"
    with pytest.raises(FormError) as caught:
        build_request(VALID, backend="local")
    assert caught.value.widget == "modelCombo"


def test_values_are_explicit_overrides_in_the_evidence():
    request = build_request(VALID, backend="fake")
    origins = dict(request.resolution_evidence)
    assert origins["field.prompt"] == origins["field.seed"] == "override"
    assert origins["field.model"] == "default"


@pytest.mark.asyncio
async def test_configuration_round_trip_is_credential_free_and_keeps_loras(tmp_path):
    values = replace(VALID, loras=(LoRASelection("x", 0.5),))
    request = build_request(values, backend="fake")
    path = tmp_path / "config.json"
    await save_configuration(path, request)
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["schema"] == "dtflow.generation-config" and "prepared" not in path.read_text(encoding="utf-8")
    loaded = await read_configuration(path, backend="fake")
    assert values_from_request(loaded) == values_from_request(request)
    assert dict(loaded.resolution_evidence)["config_file"] == "config.json"
