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


def forbidden_imports(source: str, package: str, *, backends: bool = True) -> tuple[str, ...]:
    """Resolve absolute, relative and function-local imports through AST traversal."""
    bad = []
    banned = ("PySide6", "PyQt", "drawthings_py", "grpclib", "numpy", "PIL", "betterproto", "flatbuffers")
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
            assert not forbidden_imports(path.read_text(), f"dtflow.{directory}"), path
    importlib.import_module("dtflow.core.models")
    importlib.import_module("dtflow.services.generation")


def test_only_the_draw_things_integration_imports_provider_libraries():
    root = ROOT / "src/dtflow"
    for path in root.rglob("*.py"):
        relative = path.relative_to(root)
        if relative.parts[:2] == ("backends", "drawthings"):
            continue
        package = ".".join(("dtflow", *relative.parts[:-1]))
        assert not forbidden_imports(path.read_text(), package, backends=False), path


def test_boundary_check_covers_relative_and_local_imports():
    assert forbidden_imports("def x():\n import drawthings_py", "dtflow.services")
    assert forbidden_imports("from ..backends.fake import FakeBackend", "dtflow.services")
    assert forbidden_imports("from .. import backends", "dtflow.services")
    assert not forbidden_imports("from ..core.models import Backend", "dtflow.services")


@pytest.mark.parametrize("payload", [bytearray(b"image"), memoryview(b"image")])
def test_artifacts_refuse_mutable_buffer_payloads(payload):
    from dtflow.core.models import Designation, ImageArtifact

    with pytest.raises(ValidationError, match="immutable bytes"):
        ImageArtifact("a", Designation.FINAL, "image/png", payload, 128, 128)
