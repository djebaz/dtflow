"""Phase 3A configuration resolution, LoRA preflight and request fidelity (pure functions plus one service path)."""

import json
from dataclasses import replace

import pytest

from dtflow.backends.fake import FakeBackend, now
from dtflow.core.models import (
    BackendError,
    InventoryEntry,
    InventorySnapshot,
    LoRASelection,
    ResolvedGenerationRequest,
    ValidationError,
)
from dtflow.persistence.store import SQLiteAttempts
from dtflow.services.config import ConfigError, export_config, load_config, parse_document, resolve_request
from dtflow.services.generation import GenerationService
from dtflow.services.preflight import apply_trigger_prefixes, preflight_loras


def entry(identifier, kind, architecture=None, **extra):
    return InventoryEntry(identifier, kind, identifier, architecture, "listed", "test", **extra)


def snapshot(*entries):
    return InventorySnapshot("test", "server", now(), True, entries)


def base_request(**changes):
    return replace(ResolvedGenerationRequest("r", "a cat", "base.ckpt", 0), **changes)


# -- 3A-O2: defaults < config < override, effective request, round trips --------------------------------------------


def test_precedence_evidence_and_distinctive_values(tmp_path):
    config = {"prompt": "from config", "seed": 0, "width": 512, "steps": 9, "model": "cfg.ckpt"}
    request = resolve_request(config, {"prompt": "from cli", "steps": 11, "guidance": 2.5}, config_name="c.json")
    assert (request.prompt, request.seed, request.width_px, request.steps, request.model_id) == (
        "from cli",
        0,  # explicit zero survives resolution
        512,
        11,
        "cfg.ckpt",
    )
    evidence = dict(request.resolution_evidence)
    assert evidence["field.prompt"] == "override" and evidence["field.width"] == "config"
    assert evidence["field.height"] == "default" and evidence["config_file"] == "c.json"
    assert request.guidance == 2.5 and request.height_px == 128


def test_export_round_trips_to_an_equal_request_and_carries_no_identity(tmp_path):
    original = base_request(
        seed=0, negative_prompt="blur", width_px=256, loras=(LoRASelection("a_lora_f16.ckpt", 0.55),)
    )
    document = export_config(original)
    assert "request_id" not in json.dumps(document) and document["version"] == 1
    path = tmp_path / "c.json"
    path.write_text(json.dumps(document))
    again = resolve_request(load_config(path), {})
    assert replace(again, request_id="r", resolution_evidence=()) == replace(original, resolution_evidence=())


def test_prior_metadata_is_reusable_but_outputs_and_build_are_not():
    metadata = {
        "schema_version": 1,
        "attempt_id": "x",
        "request": {
            "prompt": "p",
            "model_id": "m",
            "seed": 0,
            "width_px": 192,
            "loras": [{"identifier": "l", "weight": 1}],
        },
        "build": {"source_commit": "abc"},
        "artifacts": [],
    }
    request = resolve_request(parse_document(metadata), {})
    assert (request.prompt, request.model_id, request.width_px, request.loras) == (
        "p",
        "m",
        192,
        (LoRASelection("l", 1),),
    )


@pytest.mark.parametrize(
    "document,message",
    [
        ({"schema": "dtflow.generation-config", "version": 2, "generation": {}}, "Unsupported configuration version"),
        ({"schema": "other", "version": 1, "generation": {}}, "Unsupported configuration schema"),
        ({"schema": "dtflow.generation-config", "version": 1, "generation": {"bogus": 1}}, "Unknown configuration"),
        ({"schema": "dtflow.generation-config", "version": 1, "generation": {"api_key": "s"}}, "credentials"),
        ({"schema": "dtflow.generation-config", "version": 1, "generation": {}, "token": "s"}, "credentials"),
        ({"request": {"prompt": "p", "authorization": "b"}, "attempt_id": "x"}, "credentials"),
        ([], "JSON object"),
    ],
)
def test_unsupported_or_credential_bearing_documents_are_rejected(document, message):
    with pytest.raises(ConfigError, match=message):
        parse_document(document)


def test_missing_required_values_and_bad_files_fail_validation(tmp_path):
    with pytest.raises(ValidationError, match="prompt is required"):
        resolve_request(None, {"model": "m"})
    with pytest.raises(ValidationError, match="model is required"):
        resolve_request(None, {"prompt": "p"})
    (tmp_path / "bad.json").write_text("{nope")
    with pytest.raises(ConfigError):
        load_config(tmp_path / "bad.json")
    with pytest.raises(ConfigError):
        load_config(tmp_path / "absent.json")
    with pytest.raises(ValidationError, match="Unsupported sampler"):
        resolve_request(None, {"prompt": "p", "model": "m", "sampler": "nope"})


# -- 3A-O5: preflight --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model_arch,lora_arch,match,code",
    [
        ("sdxl_base_v0.9", "sdxl_base_v0.9", "match", "ok"),
        ("sd_v1.5", "sdxl_base_v0.9", "mismatch", "architecture_mismatch"),
        ("sd_v1.5", None, "unknown", "architecture_unknown"),
        (None, "sd_v1.5", "unknown", "architecture_unknown"),
    ],
)
def test_architecture_match_mismatch_and_unknown_stay_distinct(model_arch, lora_arch, match, code):
    inventory = snapshot(entry("base.ckpt", "model", model_arch), entry("l.ckpt", "lora", lora_arch))
    (diagnostic,) = preflight_loras(base_request(loras=(LoRASelection("l.ckpt", 1.0),)), inventory)
    assert (diagnostic.architecture_match, diagnostic.code, diagnostic.availability) == (match, code, "present")
    assert diagnostic.generation_evidence == "application_unconfirmed"


def test_absent_or_misclassified_selection_is_not_available_and_selection_order_is_kept():
    inventory = snapshot(
        entry("base.ckpt", "model", "a"), entry("notlora.ckpt", "control", "a"), entry("ok", "lora", "a")
    )
    selected = tuple(LoRASelection(name, 1.0) for name in ("missing", "notlora.ckpt", "ok"))
    diagnostics = preflight_loras(base_request(loras=selected), inventory)
    assert [(d.identifier, d.code) for d in diagnostics] == [
        ("missing", "not_available"),
        ("notlora.ckpt", "not_available"),
        ("ok", "ok"),
    ]


def test_trigger_prefix_is_explicit_ordered_deduplicated_and_recorded():
    inventory = snapshot(
        entry("a", "lora", "x", trigger_prefix="first,"),
        entry("b", "lora", "x"),
        entry("c", "lora", "x", trigger_prefix="third"),
    )
    request = base_request(prompt="a cat", loras=tuple(LoRASelection(n, 1.0) for n in "abc"))
    changed = apply_trigger_prefixes(request, inventory)
    assert (
        changed.prompt == "first, third a cat" and ("trigger_prefixes", "first,|third") in changed.resolution_evidence
    )
    assert apply_trigger_prefixes(changed, inventory) == changed  # already present: not prepended twice
    assert apply_trigger_prefixes(request, snapshot(entry("a", "lora", "x"))) is request


# -- 3A-O5/O6 composition: the service gate, with a real attempt store -----------------------------------------------


class LoraFake(FakeBackend):
    """Offline fake that advertises LoRA selection and counts what actually reaches dispatch."""

    def __init__(self, entries):
        super().__init__()
        self.entries, self.dispatched = entries, []
        self._capabilities = replace(self._capabilities, lora_support="selection")

    async def get_inventory(self, refresh=False):
        return snapshot(entry("fake-fixture", "model", "arch-a"), *self.entries)

    async def generate(self, request, *, on_event):
        self.dispatched.append(request)
        return await super().generate(replace(request, loras=()), on_event=on_event)


async def run_service(tmp_path, backend, request):
    store = SQLiteAttempts(tmp_path / "state")
    await store.open()
    await backend.open()
    try:
        service = GenerationService(backend, store, tmp_path / "out")
        try:
            return await service.generate(request), None, await store.inspect()
        except BackendError as exc:
            return None, exc, await store.inspect()
    finally:
        await store.aclose()


@pytest.mark.asyncio
async def test_confirmed_incompatibility_creates_no_attempt_and_never_dispatches(tmp_path):
    backend = LoraFake((entry("l.ckpt", "lora", "arch-b"),))
    request = base_request(model_id="fake-fixture", loras=(LoRASelection("l.ckpt", 0.8),))
    saved, error, attempts = await run_service(tmp_path, backend, request)
    assert saved is None and error.failure.code == "lora_preflight_failed"
    assert dict(error.failure.diagnostics) == {"l.ckpt": "architecture_mismatch"}
    assert backend.dispatched == [] and attempts == () and not (tmp_path / "out").exists()


@pytest.mark.asyncio
async def test_unknown_architecture_proceeds_unchanged_and_metadata_keeps_exact_selection(tmp_path):
    backend = LoraFake((entry("l.ckpt", "lora", None),))
    selection = (LoRASelection("l.ckpt", 0.35),)
    request = base_request(model_id="fake-fixture", loras=selection)
    saved, error, attempts = await run_service(tmp_path, backend, request)
    assert error is None and backend.dispatched == [request]  # never silently dropped or replaced
    recorded = json.loads(open(saved.metadata_path).read())["request"]
    assert recorded["loras"] == [{"identifier": "l.ckpt", "weight": 0.35}]
    assert attempts[0]["state"] == "succeeded" and attempts[0]["request"]["loras"] == recorded["loras"]
