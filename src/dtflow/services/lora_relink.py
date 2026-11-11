"""Match LoRA names that imported presets, configs and workflows carry to LoRAs this machine has prepared.

A file written elsewhere names a LoRA by whatever filename its author used. Prepare now stores each LoRA under a managed
name, so an imported name rarely equals the prepared one even when the same file was prepared here. This module only reads
local evidence (receipt pool, canonical records, the original file names the retained store saw); it never uploads,
renames or guesses between several candidates. A name that matches exactly one prepared content is relinked to that
content's identifier. Anything else is reported as ambiguous or unprepared so the caller can say so plainly.

A name DT+ itself lists is never relinked or reported as a problem. When the checkpoint's architecture is known, a
candidate prepared for another architecture is not offered; the format the name carries (``_lora_f16``/``_lora_q8p``)
likewise only removes candidates, it never picks one by guessing.
"""

import asyncio
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import PureWindowsPath

from dtflow.core.models import LoRASelection, PreparedAsset, ResolvedGenerationRequest
from dtflow.persistence.assets import AssetRegistry
from dtflow.services import session
from dtflow.services.lora_store import LoraStore

BOUND, MATCHED, AMBIGUOUS, UNPREPARED, LISTED = "bound", "matched", "ambiguous", "unprepared", "listed"
#: Request evidence key; the value is a JSON list of ``[imported name, prepared name]`` pairs.
RELINK_EVIDENCE = "lora_relinked"
_EXTENSIONS = (".ckpt", ".safetensors", ".pt", ".bin")
_SUFFIXES = ("_lora_f16", "_lora_q8p", "_lora")


@dataclass(frozen=True, slots=True)
class LoraMatch:
    requested: str
    status: str  # bound | matched | ambiguous | unprepared | listed (DT+ lists this exact name)
    identifier: str  # the name to use: the prepared one when bound or matched, else the requested one
    candidates: tuple[str, ...] = ()  # the prepared identifiers an ambiguous name could mean


@dataclass(frozen=True, slots=True)
class RelinkEvidence:
    pool: tuple[PreparedAsset, ...] = ()
    records: Mapping[tuple[str, int], Mapping] | None = None
    origins: Mapping[tuple[str, int], tuple[str, ...]] | None = None
    listed: frozenset[str] = frozenset()  # LoRA names the cached DT+ inventory lists itself


def name_key(name: str) -> str:
    """Comparable form of a LoRA file name: no folder, case or extension, no trailing ``_lora``/format tag."""
    text = PureWindowsPath(name.replace("/", "\\")).name.casefold().strip()
    for extension in _EXTENSIONS:
        if text.endswith(extension):
            text = text[: -len(extension)]
            break
    for suffix in _SUFFIXES:
        if text.endswith(suffix) and len(text) > len(suffix):
            return text[: -len(suffix)]
    return text


def name_format(name: str) -> str | None:
    """The precision a converted file name states (``F16``/``Q8P``), else None."""
    text = PureWindowsPath(name.replace("/", "\\")).name.casefold()
    for extension in _EXTENSIONS:
        if text.endswith(extension):
            text = text[: -len(extension)]
            break
    if text.endswith("_lora_q8p"):
        return "Q8P"
    return "F16" if text.endswith("_lora_f16") else None


def match_names(names: Iterable[str], evidence: RelinkEvidence, *, family: str | None = None) -> dict[str, LoraMatch]:
    """One match per distinct name; pure, local evidence only.

    ``family`` is the exact architecture of the checkpoint the name will be used with, when known."""
    pool = evidence.pool
    records = evidence.records or {}
    origins = evidence.origins or {}
    exact = {item.identifier for item in pool}
    contents: dict[tuple[str, int], list[PreparedAsset]] = {}
    for item in pool:
        contents.setdefault((item.sha256, item.size), []).append(item)
    aliases: dict[tuple[str, int], set[str]] = {}
    shorts: dict[tuple[str, int], str] = {}
    label: dict[tuple[str, int], str] = {}
    families: dict[tuple[str, int], str | None] = {}
    formats: dict[tuple[str, int], str | None] = {}
    for content, items in contents.items():
        record = records.get(content) or {}
        managed = record.get("managed_identifier")
        names_here = {item.identifier for item in items}
        label[content] = managed if managed in names_here else items[-1].identifier
        found = {name_key(name) for name in names_here} | {name_key(name) for name in origins.get(content, ())}
        if managed:
            found.add(name_key(managed))
        aliases[content] = found
        if record.get("short_name"):
            shorts[content] = str(record["short_name"]).casefold()
        families[content] = record.get("family") or next((i.family for i in reversed(items) if i.family), None)
        formats[content] = (str(record["format_code"]).upper() if record.get("format_code") else None) or name_format(
            label[content]
        )
    result: dict[str, LoraMatch] = {}
    for name in dict.fromkeys(names):
        if name in exact:
            result[name] = LoraMatch(name, BOUND, name)
            continue
        if name in evidence.listed:  # DT+ applies its own listed file; never swap it for a local one
            result[name] = LoraMatch(name, LISTED, name)
            continue
        key = name_key(name)
        wanted_format = name_format(name)
        hits = [
            content
            for content in contents
            if (key in aliases[content] or name.casefold() == shorts.get(content))
            # Tie-breakers only remove candidates: another architecture, or a format the name says it is not.
            and not (family and families[content] and families[content] != family)
            and not (wanted_format and formats[content] and formats[content] != wanted_format)
        ]
        if len(hits) == 1:
            result[name] = LoraMatch(name, MATCHED, label[hits[0]])
        elif hits:
            result[name] = LoraMatch(name, AMBIGUOUS, name, tuple(sorted(label[hit] for hit in hits)))
        else:
            result[name] = LoraMatch(name, UNPREPARED, name)
    return result


async def load_evidence(
    state, backend: str, pool: Sequence[PreparedAsset] | None = None, listed: Iterable[str] = ()
) -> RelinkEvidence:
    """Read the local receipt pool, canonical records and original file names once.

    ``listed`` is the LoRA names the DT+ inventory already in memory lists; nothing is fetched here."""
    if backend != "cloud":
        return RelinkEvidence()
    if pool is None:
        pool = await session.script_asset_choices(state, backend)
    registry = AssetRegistry(state)
    await registry.open()
    try:
        records = {(item["sha256"], item["size"]): item for item in await registry.lora_records()}
    finally:
        await registry.aclose()
    origins = await asyncio.to_thread(LoraStore(state).origin_names)
    return RelinkEvidence(tuple(pool), records, origins, frozenset(listed))


async def resolve_names(
    state, backend: str, names: Iterable[str], *, listed: Iterable[str] = (), family: str | None = None
) -> dict[str, LoraMatch]:
    names = tuple(dict.fromkeys(names))
    if not names or backend != "cloud":
        return {}  # nothing to say: only cloud has prepared receipts, and callers check that first
    return match_names(names, await load_evidence(state, backend, listed=listed), family=family)


def relink_selection(
    loras: Sequence[LoRASelection], matches: Mapping[str, LoraMatch]
) -> tuple[tuple[LoRASelection, ...], dict[str, str], tuple[str, ...]]:
    """Swap each uniquely matched name for the prepared one; weights and order stay.

    Returns the selection, the swaps made, and the imported names dropped because another name already meant the same
    prepared LoRA (the first one and its weight are kept)."""
    swaps = {
        item.identifier: matches[item.identifier].identifier
        for item in loras
        if item.identifier in matches and matches[item.identifier].status == MATCHED
    }
    if not swaps:
        return tuple(loras), {}, ()
    seen: set[str] = set()
    dropped: list[str] = []
    result = []
    for item in loras:
        target = swaps.get(item.identifier, item.identifier)
        if target in seen:  # two imported names meaning one prepared LoRA: keep the first, never stack it twice
            dropped.append(item.identifier)
            continue
        seen.add(target)
        result.append(replace(item, identifier=target))
    return tuple(result), swaps, tuple(dropped)


def evidence_pair(swaps: Mapping[str, str]) -> tuple[str, str]:
    """The request-evidence entry recording which imported names were swapped for which prepared LoRAs."""
    return RELINK_EVIDENCE, json.dumps([[old, new] for old, new in swaps.items()], separators=(",", ":"))


def relink_request(request: ResolvedGenerationRequest, matches: Mapping[str, LoraMatch]) -> ResolvedGenerationRequest:
    loras, swaps, _ = relink_selection(request.loras, matches)
    if not swaps:
        return request
    return replace(request, loras=loras, resolution_evidence=(*request.resolution_evidence, evidence_pair(swaps)))


def problems(matches: Mapping[str, LoraMatch]) -> tuple[LoraMatch, ...]:
    """The names that still have no single prepared LoRA behind them."""
    return tuple(item for item in matches.values() if item.status in (AMBIGUOUS, UNPREPARED))


def _plain(name: str) -> str:
    """The name without folder and extension; no label rules (a DT+ listed name is the provider's own)."""
    stem = PureWindowsPath(name.replace("/", "\\")).name
    for extension in _EXTENSIONS:
        if stem.casefold().endswith(extension):
            return stem[: -len(extension)]
    return stem


def imported_label(name: str) -> str:
    """How an imported name that matches no prepared LoRA is shown: no folder or extension, marked as imported."""
    from dtflow.services.lora_naming import display_label

    label = display_label(name)
    return label if label != name else f"{_plain(name)} (imported)"


def summary(matches: Mapping[str, LoraMatch], dropped: Sequence[str] = ()) -> str:
    """One plain sentence for a status line; empty when every name is bound or relinked."""

    unprepared = [imported_label(item.requested) for item in matches.values() if item.status == UNPREPARED]
    ambiguous = [imported_label(item.requested) for item in matches.values() if item.status == AMBIGUOUS]
    listed = [_plain(item.requested) for item in matches.values() if item.status == LISTED]
    relinked = [item for item in matches.values() if item.status == MATCHED]
    parts = []
    if relinked:
        parts.append(f"{len(relinked)} matched to prepared LoRA(s)")
    if listed:
        parts.append("listed on DT+: " + ", ".join(listed))
    if unprepared:
        parts.append("no prepared match, use Prepare: " + ", ".join(unprepared))
    if ambiguous:
        parts.append("matches several prepared LoRAs, pick one: " + ", ".join(ambiguous))
    if dropped:
        parts.append(
            "left out, same prepared LoRA as an earlier name: " + ", ".join(imported_label(n) for n in dropped)
        )
    return "; ".join(parts)
