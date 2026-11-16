"""Contract boundaries and immutable input validation."""

import ast
import importlib
import json
from dataclasses import FrozenInstanceError, asdict
from pathlib import Path

import pytest

from dtflow.core.models import LoRASelection, ResolvedGenerationRequest, Sampler, TimeoutPolicy, ValidationError

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "changes",
    [
        {"seed": True},
        {"seed": -1},
        {"seed": 2**32},
        {"width_px": 300},
        {"height_px": 64},
        {"steps": 0},
        {"guidance": float("nan")},
        {"guidance": True},
        {"sampler_id": "ignored"},
        {"loras": []},
        {"batch_size": 4, "batch_count": 4},
        {"resolution_evidence": [("source", "cli")]},
    ],
)
def test_invalid_resolved_requests_fail_before_submission(changes):
    fields = {"request_id": "r", "prompt": "fixture", "model_id": "fake-fixture", "seed": 0}
    fields.update(changes)
    with pytest.raises(ValidationError):
        ResolvedGenerationRequest(**fields)


def test_immutable_request_seed_zero_and_serialization():
    request = ResolvedGenerationRequest("r", "fixture", "fake-fixture", 0, loras=(LoRASelection("exact.ckpt", 0.5),))
    assert json.loads(json.dumps(asdict(request)))["seed"] == 0
    assert request.sampler_id == Sampler.EULER_A
    with pytest.raises(FrozenInstanceError):
        request.seed = 42


@pytest.mark.parametrize("value", [0, -1, float("inf"), True])
def test_timeout_policy_refuses_unbounded_values(value):
    with pytest.raises(ValidationError):
        TimeoutPolicy(total=value)


def forbidden_imports(
    source: str,
    package: str,
    *,
    backends: bool = True,
    allow_pillow: bool = False,
    allow_numpy: bool = False,
    allow_httpx: bool = False,
) -> tuple[str, ...]:
    """Resolve absolute, relative and function-local imports through AST traversal."""
    bad = []
    banned = ("PySide6", "PyQt", "drawthings_py", "grpclib", "numpy", "PIL", "betterproto", "flatbuffers", "httpx")
    if allow_pillow:
        banned = tuple(module for module in banned if module != "PIL")
    if allow_numpy:
        banned = tuple(module for module in banned if module != "numpy")
    if allow_httpx:
        banned = tuple(module for module in banned if module != "httpx")
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules = [x.name for x in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = package.split(".")
            if node.level:
                prefix = ".".join(base[: len(base) - node.level + 1])
                module = prefix + ("." + node.module if node.module else "")
            else:
                module = node.module or ""
            modules = [module, *(module + "." + x.name for x in node.names)]
        else:
            continue
        for module in modules:
            if module.startswith(banned) or (backends and module.startswith("dtflow.backends")):
                bad.append(module)
    return tuple(bad)


def test_core_and_services_have_no_provider_or_presentation_imports():
    for directory in ("core", "services"):
        for path in (ROOT / "src/dtflow" / directory).glob("*.py"):
            allow_pillow = directory == "services" and path.name in (
                "image_export.py",
                "image_quality.py",
                "image_preparation.py",
                "person_selection.py",
                "face_geometry.py",
            )
            allow_numpy = directory == "services" and path.name in (
                "person_selection.py",
                "face_geometry.py",
                "lora_q8p_codec.py",
                "lora_quantization.py",
            )  # numerical image and LoRA conversion services, not provider integrations
            allow_httpx = directory == "services" and path.name in (
                "civitai.py",
                "civitai_gallery.py",
            )  # the Civitai client and gallery (#462, #466)
            assert not forbidden_imports(
                path.read_text(encoding="utf-8"),
                f"dtflow.{directory}",
                allow_pillow=allow_pillow,
                allow_numpy=allow_numpy,
                allow_httpx=allow_httpx,
            ), path
    importlib.import_module("dtflow.core.models")
    importlib.import_module("dtflow.services.generation")


def test_only_the_draw_things_integration_imports_provider_libraries():
    root = ROOT / "src/dtflow"
    for path in root.rglob("*.py"):
        relative = path.relative_to(root)
        if relative.parts[:2] == ("backends", "drawthings") or relative.parts[:1] == ("ui",):
            continue  # the Qt shell may import Qt; every other package may not
        package = ".".join(("dtflow", *relative.parts[:-1]))
        allow_pillow = relative.parts in (
            ("services", "image_export.py"),
            ("services", "image_quality.py"),
            ("services", "image_preparation.py"),
            ("services", "person_selection.py"),
            ("services", "face_geometry.py"),
        )
        allow_numpy = relative.parts in (
            ("services", "person_selection.py"),
            ("services", "face_geometry.py"),
            ("services", "lora_q8p_codec.py"),
            ("services", "lora_quantization.py"),
        ) or relative.parts[:2] == ("services", "lora_convert")  # the pure-Python LoRA converter needs numpy
        allow_httpx = relative.parts in (
            ("services", "civitai.py"),
            ("services", "civitai_gallery.py"),
        )  # Civitai (#462, #466)
        assert not forbidden_imports(
            path.read_text(encoding="utf-8"),
            package,
            backends=False,
            allow_pillow=allow_pillow,
            allow_numpy=allow_numpy,
            allow_httpx=allow_httpx,
        ), path


def test_boundary_check_covers_relative_and_local_imports():
    assert forbidden_imports("def x():\n import drawthings_py", "dtflow.services")
    assert forbidden_imports("from ..backends.fake import FakeBackend", "dtflow.services")
    assert forbidden_imports("from .. import backends", "dtflow.services")
    assert not forbidden_imports("from ..core.models import Backend", "dtflow.services")
    assert forbidden_imports("from PIL import Image", "dtflow.services")
    assert not forbidden_imports("from PIL import Image", "dtflow.services", allow_pillow=True)
    assert forbidden_imports("import numpy", "dtflow.services", allow_pillow=True)
    assert not forbidden_imports("import numpy", "dtflow.services", allow_numpy=True)
    assert forbidden_imports("import PySide6", "dtflow.services", allow_pillow=True)
    assert forbidden_imports("import drawthings_py", "dtflow.services", allow_pillow=True)


@pytest.mark.parametrize("payload", [bytearray(b"image"), memoryview(b"image")])
def test_artifacts_refuse_mutable_buffer_payloads(payload):
    from dtflow.core.models import Designation, ImageArtifact

    with pytest.raises(ValidationError, match="immutable bytes"):
        ImageArtifact("a", Designation.FINAL, "image/png", payload, 128, 128)
