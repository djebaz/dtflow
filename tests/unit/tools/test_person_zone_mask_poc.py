"""Cheap, non-inference checks for the standalone mask tool."""

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

SPEC = importlib.util.spec_from_file_location(
    "person_zone_mask_poc", Path(__file__).resolve().parents[3] / "tools/images/person_zone_mask_poc.py"
)
poc = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(poc)


@pytest.mark.parametrize("profile", ["torchvision", "fastseg"])
def test_mask_geometry_and_preprocessing(profile):
    spec = poc.PROFILES[profile]
    logits = np.zeros((1, spec["classes"], 320, 320), dtype=np.float32)
    logits[0, spec["person"], :, :160] = 1
    person, zone, edit = poc.masks_from_logits(logits, (8, 4), [2, 1, 6, 3], profile)
    assert np.count_nonzero(person) == 16
    assert np.count_nonzero(zone) == 8
    assert np.count_nonzero(edit) == 4
    if profile == "fastseg":
        logits[0, 12, :, 160:] = 2
        rider_person, _, _ = poc.masks_from_logits(logits, (8, 4), [0, 0, 8, 4], profile)
        assert np.count_nonzero(rider_person) == 16
    assert np.all(edit <= person) and np.all(edit <= zone)
    tensor = poc.preprocess(Image.new("RGB", (8, 4), "white"))
    assert tensor.shape == (1, 3, 320, 320) and tensor.dtype == np.float32
    np.testing.assert_allclose(
        tensor[0, :, 0, 0], (np.ones(3) - [0.485, 0.456, 0.406]) / [0.229, 0.224, 0.225], rtol=1e-6
    )
    with pytest.raises(ValueError, match="Box"):
        poc.masks_from_logits(logits, (8, 4), [-1, 0, 5, 3], profile)
    logits[0, 0, 0, 0] = np.nan
    with pytest.raises(ValueError, match="finite"):
        poc.masks_from_logits(logits, (8, 4), [0, 0, 8, 4], profile)


@pytest.mark.parametrize("profile", ["torchvision", "fastseg"])
def test_session_contract_and_cpu_options(monkeypatch, profile):
    spec = poc.PROFILES[profile]
    captured = {}

    class Options:
        def add_session_config_entry(self, key, value):
            captured[key] = value

    class Session:
        def __init__(self, data, sess_options, providers):
            captured.update(data=data, options=sess_options, providers=providers)

        def get_providers(self):
            return ["CPUExecutionProvider"]

        def get_inputs(self):
            return [SimpleNamespace(shape=[1, 3, 320, 320], type="tensor(float)")]

        def get_outputs(self):
            return [SimpleNamespace(shape=[1, spec["classes"], 320, 320], type="tensor(float)")]

        def get_modelmeta(self):
            return SimpleNamespace(
                custom_metadata_map={"mask_poc_contract": spec["contract"]} if profile == "torchvision" else {}
            )

    monkeypatch.setitem(
        sys.modules,
        "onnxruntime",
        SimpleNamespace(
            SessionOptions=Options, ExecutionMode=SimpleNamespace(ORT_SEQUENTIAL="sequential"), InferenceSession=Session
        ),
    )
    poc.create_session(b"fake", 1, profile)
    assert captured["providers"] == ["CPUExecutionProvider"]
    assert captured["options"].intra_op_num_threads == captured["options"].inter_op_num_threads == 1
    assert captured["session.intra_op.allow_spinning"] == captured["session.inter_op.allow_spinning"] == "0"
    monkeypatch.setattr(Session, "get_providers", lambda self: ["CUDAExecutionProvider"])
    with pytest.raises(ValueError, match="exclusively"):
        poc.create_session(b"fake", 1, profile)


@pytest.mark.parametrize("profile", ["torchvision", "fastseg"])
def test_outputs_report_and_source_preservation(tmp_path, monkeypatch, profile):
    spec = poc.PROFILES[profile]
    monkeypatch.setattr(poc, "ROOT", tmp_path)
    monkeypatch.setitem(sys.modules, "onnxruntime", SimpleNamespace(__version__="fake-test-runtime"))
    source = tmp_path / "source.png"
    Image.new("RGB", (8, 4), "white").save(source)
    original = source.read_bytes()
    model = tmp_path / "model.onnx"
    model.write_bytes(b"fake")
    calls = []

    class Session:
        def get_inputs(self):
            return [SimpleNamespace(name="image")]

        def get_outputs(self):
            return [SimpleNamespace(name="logits")]

        def get_providers(self):
            return ["CPUExecutionProvider"]

        def run(self, outputs, inputs):
            calls.append(inputs["image"].shape)
            logits = np.zeros((1, spec["classes"], 320, 320), dtype=np.float32)
            logits[:, spec["person"]] = 1
            return [logits]

    monkeypatch.setattr(poc, "create_session", lambda data, threads, profile: Session())
    args = SimpleNamespace(
        profile=profile,
        source=source,
        model=model,
        sha256=poc.hashlib.sha256(b"fake").hexdigest(),
        box=[1, 1, 5, 3],
        threads=1,
        out=tmp_path / "tmp/run",
    )
    output = poc.run(args)
    assert calls == [(1, 3, 320, 320)]
    assert source.read_bytes() == original and model.read_bytes() == b"fake"
    assert {p.name for p in output.iterdir()} == {
        "person_mask.png",
        "zone_mask.png",
        "edit_mask.png",
        "overlay.jpg",
        "report.json",
    }
    report = json.loads((output / "report.json").read_text())
    assert report["masks"]["edit_pixels"] == 8
    assert report["model"]["person_class"] == spec["person"]
    assert report["model"]["class_count"] == spec["classes"]
    assert report["runtime"]["providers"] == ["CPUExecutionProvider"]
    assert all(value >= 0 for value in report["timings_ms"].values())
    with pytest.raises(ValueError, match="exist"):
        poc.run(args)
    args.out = tmp_path / "tmp/new"
    args.sha256 = "0" * 64
    with pytest.raises(ValueError, match="mismatch"):
        poc.run(args)
    assert not args.out.exists()
    with pytest.raises(ValueError, match="below"):
        poc.output_path(tmp_path / "elsewhere")
