"""Qt-free mapping between Generate-page values and the shared request/configuration services.

Widgets hand over plain ``FormValues``; the same ``resolve_request`` the CLI uses validates them, and a stable table maps
the resulting validation errors back to the widget that owns the field. No resolver or compatibility rules live here.
"""

import asyncio
import json
import re
import secrets
from dataclasses import dataclass, replace
from pathlib import Path

from dtflow.core.models import SEED_LIMIT, LoRASelection, ResolvedGenerationRequest, Sampler, ValidationError
from dtflow.services.artifacts import atomic_write
from dtflow.services.lora_naming import display_label
from dtflow.services.config import export_config, load_config, resolve_request

FAKE_MODEL = "fake-fixture"  # the same default the CLI applies for the fake backend only
SAMPLERS = tuple(item.value for item in Sampler)
# Leading word of a ValidationError message -> Designer object name of the owning widget. Anything unlisted is shown as
# a general banner instead of being guessed onto a field.
ERROR_FIELDS = {
    "prompt": "promptEdit",
    "prompts": "promptEdit",
    "model": "modelCombo",
    "model_id": "modelCombo",
    "seed": "seedEdit",
    "width_px": "widthSpinBox",
    "height_px": "heightSpinBox",
    "steps": "stepsSpinBox",
    "guidance": "guidanceSpinBox",
    "sampler": "samplerCombo",
    "unsupported": "samplerCombo",  # "Unsupported sampler."
    "loras": "loraList",
    "lora": "loraList",
}


@dataclass(frozen=True, slots=True)
class FormValues:
    """Raw widget values; ``seed`` stays text so a malformed entry is reported against its own field."""

    prompt: str = ""
    negative_prompt: str = ""
    model: str = ""
    seed: str = "0"
    width: int = 512
    height: int = 512
    steps: int = 20
    guidance: float = 7.0
    sampler: str = Sampler.EULER_A.value
    loras: tuple[LoRASelection, ...] = ()


def snap_dimension(value: int) -> int:
    """The nearest multiple of 64 (halves round up) within the request limits 128..2048."""
    return min(2048, max(128, (value + 32) // 64 * 64))


class FormError(ValidationError):
    """A request problem with the widget (Designer object name) that owns it, or ``None`` for a general problem."""

    def __init__(self, message: str, widget: str | None):
        super().__init__(message)
        self.widget = widget


def field_for(message: str) -> str | None:
    match = re.match(r"[A-Za-z_]+", message)
    return ERROR_FIELDS.get(match.group(0).lower()) if match else None


RANDOM_SEED_HEADROOM = 1024  # room above a random base seed for queue items (seed + index) to stay in range


def build_request(
    values: FormValues,
    *,
    backend: str,
    config: dict | None = None,
    config_name: str | None = None,
    random_seed: bool = False,
) -> ResolvedGenerationRequest:
    """Resolve widget values through the shared resolver; every value is an explicit ``override``.

    ``random_seed`` is for submissions only: a seed of 0 (as in the form's default) is replaced by a random one before
    the request exists, so the recorded request and metadata carry the seed that was really used.
    """
    try:
        seed = int(values.seed.strip()) if values.seed.strip() else None
    except ValueError:
        raise FormError("seed must be an integer in 0..4294967295.", "seedEdit") from None
    overrides = {
        "prompt": values.prompt.strip() or None,
        "negative_prompt": values.negative_prompt,
        "model": values.model.strip() or None,
        "seed": seed,
        "width": snap_dimension(values.width),
        "height": snap_dimension(values.height),
        "steps": values.steps,
        "guidance": values.guidance,
        "sampler": values.sampler,
        "loras": values.loras,
    }
    try:
        request = resolve_request(
            config,
            overrides,
            default_model=FAKE_MODEL if backend == "fake" else None,
            config_name=config_name,
        )
    except ValidationError as exc:
        raise FormError(str(exc), field_for(str(exc))) from exc
    if random_seed and request.seed == 0:
        request = replace(
            request,
            seed=secrets.randbelow(SEED_LIMIT - RANDOM_SEED_HEADROOM) + 1,
            resolution_evidence=(*request.resolution_evidence, ("seed.resolved", "random (form seed was 0)")),
        )
    return request


def values_from_request(request: ResolvedGenerationRequest) -> FormValues:
    return FormValues(
        request.prompt,
        request.negative_prompt,
        request.model_id,
        str(request.seed),
        request.width_px,
        request.height_px,
        request.steps,
        request.guidance,
        request.sampler_id.value,
        request.loras,
    )


def form_to_document(values: FormValues) -> dict:
    """Plain JSON-safe form values for the remembered last session (credential-free, never validated here)."""
    return {
        "prompt": values.prompt,
        "negative_prompt": values.negative_prompt,
        "model": values.model,
        "seed": values.seed,
        "width": values.width,
        "height": values.height,
        "steps": values.steps,
        "guidance": values.guidance,
        "sampler": values.sampler,
        "loras": [{"identifier": item.identifier, "weight": item.weight} for item in values.loras],
    }


def form_from_document(document: object) -> FormValues:
    """Tolerant reverse of ``form_to_document``: a field of the wrong type or an invalid LoRA falls back to its default."""
    base = FormValues()
    if not isinstance(document, dict):
        return base

    def pick(name: str, kind: type, default):
        value = document.get(name, default)
        if kind is float and type(value) is int:
            value = float(value)
        return value if type(value) is kind else default

    loras = []
    for item in document.get("loras", ()) if isinstance(document.get("loras"), list) else ():
        try:
            loras.append(LoRASelection(item["identifier"], float(item["weight"])))
        except (KeyError, TypeError, ValueError, ValidationError):
            continue
    sampler = pick("sampler", str, base.sampler)
    return FormValues(
        pick("prompt", str, base.prompt),
        pick("negative_prompt", str, base.negative_prompt),
        pick("model", str, base.model),
        pick("seed", str, base.seed),
        pick("width", int, base.width),
        pick("height", int, base.height),
        pick("steps", int, base.steps),
        pick("guidance", float, base.guidance),
        sampler if sampler in SAMPLERS else base.sampler,
        tuple(loras),
    )


def describe_request(request: ResolvedGenerationRequest, backend: str, endpoint: str | None) -> str:
    """Effective values plus where each came from, exactly as resolved; no UI-only defaults are added."""
    shown = endpoint if backend == "local" else None  # only a local server has a host:port; cloud is not an address
    lines = [f"backend: {backend}" + (f" ({shown})" if shown else ""), f"model: {request.model_id}"]
    lines += [f"seed: {request.seed}", f"size: {request.width_px}x{request.height_px}", f"steps: {request.steps}"]
    lines += [f"guidance: {request.guidance}", f"sampler: {request.sampler_id.value}"]
    receipts = {item.identifier: item.receipt_id[:8] for item in request.prepared_assets}
    lines += [
        f"lora: {item.identifier}"
        + (f" [{display_label(item.identifier)}]" if display_label(item.identifier) != item.identifier else "")
        + f" @ {item.weight}"
        + (f" (receipt {receipts[item.identifier]})" if item.identifier in receipts else "")
        for item in request.loras
    ]
    lines += ["", "resolution evidence:"] + [f"  {name}: {origin}" for name, origin in request.resolution_evidence]
    return "\n".join(lines)


def describe_failure(failure: BaseException) -> str:
    """Development-mode failure evidence: the backend's own codes, stage and diagnostics (already sanitized)."""
    detail = getattr(failure, "failure", None)
    lines = [f"error: {type(failure).__name__}"]
    if detail is None:
        return "\n".join(lines + [f"message: {failure}"])
    category = getattr(detail.category, "value", detail.category)
    lines += [f"code: {detail.code}", f"category: {category}", f"message: {detail.message}"]
    for label, value in (
        ("submission stage", detail.submission_stage),
        ("remote outcome", detail.remote_outcome),
        ("terminal status", detail.terminal_status),
        ("timeout phase", detail.timeout_phase),
    ):
        if value:
            lines.append(f"{label}: {getattr(value, 'value', value)}")
    lines += [f"  {key}: {value}" for key, value in detail.diagnostics]
    return "\n".join(lines)


def _write_config(path: Path, request: ResolvedGenerationRequest) -> None:
    atomic_write(Path(path), (json.dumps(export_config(request), indent=2) + "\n").encode("utf-8"))


async def save_configuration(path: Path, request: ResolvedGenerationRequest) -> None:
    """Write the versioned, credential-free configuration; file I/O runs off the caller's loop."""
    await asyncio.to_thread(_write_config, path, request)


async def read_configuration(path: Path, *, backend: str) -> ResolvedGenerationRequest:
    """Load a configuration (or prior metadata) through the shared importer and resolve it without overrides."""
    path = Path(path)
    config = await asyncio.to_thread(load_config, path)
    try:
        return resolve_request(
            config, {}, default_model=FAKE_MODEL if backend == "fake" else None, config_name=path.name
        )
    except ValidationError as exc:
        raise FormError(str(exc), field_for(str(exc))) from exc
