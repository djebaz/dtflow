"""Family registry: the supported families (data in ``data/families.json``) and their conversion runners (code).

Registration is explicit (``families/__init__.py`` imports each family module once); there is
no directory scanning, so PyInstaller sees static imports.
"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from dtflow.services.lora_convert.errors import FamilyDataError, UnsupportedFamilyError

if TYPE_CHECKING:
    import numpy as np

    from dtflow.services.lora_convert.context import ConversionContext
    from dtflow.services.lora_convert.models import ConversionRequest
    from dtflow.services.lora_convert.safetensors_io import SafeTensors

Status = Literal["verified", "unverified_on_dt_plus", "experimental"]
# (open source file, request, context) -> converted tensors by checkpoint name
FamilyRunner = Callable[["SafeTensors", "ConversionRequest", "ConversionContext"], "dict[str, np.ndarray]"]


@dataclass(frozen=True, slots=True)
class FamilyCode:
    """What stays in code for one family: its runner, kind and notes. Everything else is in ``data/families.json``."""

    key: str  # "krea2"
    kind: Literal["resolver", "tables"]
    run: FamilyRunner
    notes: str = ""


@dataclass(frozen=True, slots=True)
class FamilySpec:
    key: str  # "krea2"
    label: str  # "Krea 2"
    exact_architecture: str  # Draw Things version string, "krea_2"
    status: Status
    experimental: bool  # follows the status from families.json; conversion asks for consent only if experimental
    kind: Literal["resolver", "tables"]
    run: FamilyRunner
    notes: str = ""
    aliases: Mapping[str, tuple[str, ...]] = field(default_factory=dict)  # provider -> labels, e.g. "civitai"


FAMILIES_FILE = Path(__file__).with_name("data") / "families.json"
_STATUSES = ("verified", "unverified_on_dt_plus", "experimental")
_ENTRY_FIELDS = {"key", "label", "exact_architecture", "status", "experimental", "aliases"}
_PROVIDER = re.compile(r"[a-z][a-z0-9_]{0,31}\Z")

_CODE: dict[str, FamilyCode] = {}
_SPECS: dict[str, FamilySpec] | None = None
_ALIASES: dict[str, dict[str, str]] = {}  # provider -> casefolded label -> family key
_LOCK = threading.Lock()


def register(code: FamilyCode) -> FamilyCode:
    if code.key in _CODE:
        raise ValueError(f"LoRA family {code.key!r} is registered twice.")
    _CODE[code.key] = code
    return code


def _refuse(message: str) -> FamilyDataError:
    return FamilyDataError(f"families.json: {message}")


def _text(value: object, what: str, limit: int = 80) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip() or len(value) > limit:
        raise _refuse(f"{what} must be a non-empty text of at most {limit} characters without edge spaces.")
    return value


def _parse(document: object, runners: Mapping[str, FamilyCode]) -> tuple[dict[str, FamilySpec], dict]:
    """Validate the whole file against the registered runners; any problem refuses all of it."""
    from dtflow.services.lora_naming import ARCHITECTURE_TAGS

    if not isinstance(document, dict) or set(document) != {"schema", "families"} or document["schema"] != 1:
        raise _refuse('the file must be an object with "schema": 1 and "families".')
    entries = document["families"]
    if not isinstance(entries, list) or not entries:
        raise _refuse('"families" must be a non-empty list.')
    specs: dict[str, FamilySpec] = {}
    aliases: dict[str, dict[str, str]] = {}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != _ENTRY_FIELDS:
            raise _refuse("every family needs exactly " + ", ".join(sorted(_ENTRY_FIELDS)) + " and nothing else.")
        key = _text(entry["key"], "key", 32)
        if key in specs:
            raise _refuse(f"family {key!r} is listed twice.")
        code = runners.get(key)
        if code is None:
            raise _refuse(f"family {key!r} has no converter runner registered in code.")
        label = _text(entry["label"], f"{key}: label")
        architecture = _text(entry["exact_architecture"], f"{key}: exact_architecture")
        if architecture not in ARCHITECTURE_TAGS:
            raise _refuse(f"{key}: exact_architecture {architecture!r} has no managed-name tag.")
        status = entry["status"]
        experimental = entry["experimental"]
        if status not in _STATUSES:
            raise _refuse(f"{key}: status must be one of {', '.join(_STATUSES)}.")
        if type(experimental) is not bool or experimental != (status == "experimental"):
            raise _refuse(f"{key}: experimental must be true exactly when the status is experimental.")
        provider_labels = entry["aliases"]
        if not isinstance(provider_labels, dict):
            raise _refuse(f"{key}: aliases must map a provider to a list of labels.")
        mapped: dict[str, tuple[str, ...]] = {}
        for provider, labels in provider_labels.items():
            if not isinstance(provider, str) or not _PROVIDER.match(provider):
                raise _refuse(f"{key}: provider names are lowercase words such as 'civitai'.")
            if not isinstance(labels, list) or not labels:
                raise _refuse(f"{key}: {provider} aliases must be a non-empty list.")
            kept = []
            for raw in labels:
                alias = _text(raw, f"{key}: {provider} alias")
                owner = aliases.setdefault(provider, {}).get(alias.casefold())
                if owner is not None:
                    raise _refuse(f"the {provider} label {alias!r} is claimed by both {owner!r} and {key!r}.")
                aliases[provider][alias.casefold()] = key
                kept.append(alias)
            mapped[provider] = tuple(kept)
        specs[key] = FamilySpec(
            key=key,
            label=label,
            exact_architecture=architecture,
            status=status,
            experimental=experimental,
            kind=code.kind,
            run=code.run,
            notes=code.notes,
            aliases=mapped,
        )
    for key in runners:
        if key not in specs:
            raise _refuse(f"the converter runner {key!r} has no entry.")
    return specs, aliases


def _load() -> dict[str, FamilySpec]:
    global _SPECS, _ALIASES
    if _SPECS is not None:
        return _SPECS
    with _LOCK:
        if _SPECS is None:
            from dtflow.services.lora_convert import families  # noqa: F401  (registers every runner)

            try:
                document = json.loads(FAMILIES_FILE.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise _refuse("the file is missing or is not valid JSON.") from exc
            _SPECS, _ALIASES = _parse(document, dict(_CODE))
    return _SPECS


def lookup(key: str) -> FamilySpec:
    spec = _load().get(key)
    if spec is None:
        from dtflow.services.lora_convert.errors import COLAB_HINT

        raise UnsupportedFamilyError(f"Family {key!r} is not supported by the Python converter.", hint=COLAB_HINT)
    return spec


def supported_families() -> tuple[FamilySpec, ...]:
    """Specs in the stable display order (the order of ``families.json``)."""
    return tuple(_load().values())


def family_for_label(provider: str, label: str | None) -> FamilySpec | None:
    """The converter family a provider's base-model label maps to, or None. Exact label match, no guessing."""
    specs = _load()
    if not isinstance(label, str):
        return None
    key = _ALIASES.get(provider, {}).get(label.strip().casefold())
    return specs.get(key) if key else None


def provider_labels(provider: str) -> tuple[str, ...]:
    """Every label of ``provider`` that maps to a supported family, in file order (for a gallery's exact filter)."""
    return tuple(label for spec in supported_families() for label in spec.aliases.get(provider, ()))
