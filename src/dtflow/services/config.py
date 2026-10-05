"""Versioned, credential-free generation configuration: import, override resolution and export.

Precedence is built-in default < imported file < explicit override. The result is one immutable
``ResolvedGenerationRequest`` whose ``resolution_evidence`` records where each field came from, so the effective
request (not just the entered arguments) is what gets persisted.
"""

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from uuid import uuid4

from dtflow.core.models import LoRASelection, ResolvedGenerationRequest, Sampler, ValidationError

SCHEMA = "dtflow.generation-config"
VERSION = 1
MAX_BYTES = 1024 * 1024
DEFAULTS: dict[str, Any] = {
    "negative_prompt": "",
    "seed": 0,
    "width": 128,
    "height": 128,
    "steps": 4,
    "guidance": 1.0,
    "sampler": Sampler.EULER_A.value,
    "loras": (),
}
FIELDS = ("prompt", "model", *DEFAULTS)
# Metadata written by an earlier run maps onto the same generation fields.
METADATA_FIELDS = {
    "prompt": "prompt",
    "negative_prompt": "negative_prompt",
    "model_id": "model",
    "seed": "seed",
    "width_px": "width",
    "height_px": "height",
    "steps": "steps",
    "guidance": "guidance",
    "sampler_id": "sampler",
    "loras": "loras",
}
CREDENTIAL_WORDS = ("key", "token", "secret", "auth", "password", "credential")


class ConfigError(ValidationError):
    """Unreadable, unsupported or credential-bearing configuration; nothing was dispatched."""


def reject_credentials(value: Any, path: str = "") -> None:
    if isinstance(value, dict):
        for name, child in value.items():
            if any(word in str(name).lower() for word in CREDENTIAL_WORDS):
                raise ConfigError(f"Configuration must not contain credentials ({path}{name}).")
            reject_credentials(child, f"{path}{name}.")
    elif isinstance(value, list):
        for child in value:
            reject_credentials(child, path)


def _loras(value: Any) -> tuple[LoRASelection, ...]:
    if not isinstance(value, (list, tuple)):
        raise ConfigError("loras must be a list.")
    selections = []
    for item in value:
        if isinstance(item, LoRASelection):
            selections.append(item)
        elif isinstance(item, dict) and set(item) == {"identifier", "weight"}:
            selections.append(LoRASelection(item["identifier"], item["weight"]))
        else:
            raise ConfigError("Each LoRA needs exactly an identifier and a weight.")
    return tuple(selections)


def parse_document(document: Any) -> dict[str, Any]:
    """Validate one parsed config or prior-run metadata document and return its generation fields."""
    if not isinstance(document, dict):
        raise ConfigError("Configuration must be a JSON object.")
    if "request" in document and "attempt_id" in document:
        # Metadata reuse: only fields from the recorded resolved request; outputs and build identity are not reused.
        request = document["request"]
        if not isinstance(request, dict):
            raise ConfigError("Metadata request is malformed.")
        reject_credentials(request)
        return {METADATA_FIELDS[name]: value for name, value in request.items() if name in METADATA_FIELDS}
    reject_credentials(document)
    if document.get("schema") != SCHEMA:
        raise ConfigError(f"Unsupported configuration schema (expected {SCHEMA}).")
    version = document.get("version")
    if type(version) is not int or version != VERSION:
        raise ConfigError(f"Unsupported configuration version {version!r}; this build reads version {VERSION}.")
    fields = document.get("generation")
    if not isinstance(fields, dict) or set(document) - {"schema", "version", "generation"}:
        raise ConfigError("Configuration needs only schema, version and a generation object.")
    unknown = sorted(set(fields) - set(FIELDS))
    if unknown:
        raise ConfigError("Unknown configuration field(s): " + ", ".join(unknown))
    return dict(fields)


def read_json_file(path: Path, label: str = "Configuration") -> Any:
    """Bounded UTF-8 JSON read shared by configuration and plan files; ``label`` words the errors."""
    try:
        with Path(path).open("rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
    except OSError as exc:
        raise ConfigError(f"{label} file could not be read.") from exc
    if len(raw) > MAX_BYTES:
        raise ConfigError(f"{label} file is too large.")
    try:
        return json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise ConfigError(f"{label} file is not valid UTF-8 JSON.") from exc


def load_config(path: Path) -> dict[str, Any]:
    return parse_document(read_json_file(path))


def resolve_request(
    config: Mapping[str, Any] | None,
    overrides: Mapping[str, Any],
    *,
    default_model: str | None = None,
    config_name: str | None = None,
) -> ResolvedGenerationRequest:
    """Merge default < config < override into an immutable request; ``None`` overrides mean not supplied."""
    config = config or {}
    values: dict[str, Any] = {}
    evidence: list[tuple[str, str]] = []
    for name in FIELDS:
        if overrides.get(name) is not None:
            values[name], origin = overrides[name], "override"
        elif name in config:
            values[name], origin = config[name], "config"
        elif name == "model" and default_model is not None:
            values[name], origin = default_model, "default"
        elif name in DEFAULTS:
            values[name], origin = DEFAULTS[name], "default"
        else:
            raise ConfigError(f"{name} is required.")
        evidence.append((f"field.{name}", origin))
    if config_name and any(origin == "config" for _, origin in evidence):
        evidence.append(("config_file", config_name))
    try:
        sampler = Sampler(values["sampler"])
    except ValueError as exc:
        raise ConfigError("Unsupported sampler.") from exc
    return ResolvedGenerationRequest(
        uuid4().hex,
        values["prompt"],
        values["model"],
        values["seed"],
        negative_prompt=values["negative_prompt"],
        width_px=values["width"],
        height_px=values["height"],
        steps=values["steps"],
        guidance=values["guidance"],
        sampler_id=sampler,
        loras=_loras(values["loras"]),
        resolution_evidence=tuple(evidence),
    )


def export_config(request: ResolvedGenerationRequest) -> dict[str, Any]:
    """The reusable, credential-free generation settings of a resolved request (no ids, evidence or backend)."""
    return {
        "schema": SCHEMA,
        "version": VERSION,
        "generation": {
            "prompt": request.prompt,
            "negative_prompt": request.negative_prompt,
            "model": request.model_id,
            "seed": request.seed,
            "width": request.width_px,
            "height": request.height_px,
            "steps": request.steps,
            "guidance": request.guidance,
            "sampler": request.sampler_id.value,
            "loras": [{"identifier": item.identifier, "weight": item.weight} for item in request.loras],
        },
    }
