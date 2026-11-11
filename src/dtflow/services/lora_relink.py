"""Match LoRA names that imported presets, configs and workflows carry to LoRAs this machine has prepared.

A file written elsewhere names a LoRA by whatever filename its author used. Prepare now stores each LoRA under a managed
name, so an imported name rarely equals the prepared one even when the same file was prepared here. This module only reads
local evidence (receipt pool, canonical records, the original file names the retained store saw); it never uploads,
renames or guesses between several candidates. A name that matches exactly one prepared content is relinked to that
content's identifier. Anything else is reported as ambiguous or unprepared so the caller can say so plainly.
"""

import asyncio
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import PureWindowsPath

from dtflow.core.models import LoRASelection, PreparedAsset, ResolvedGenerationRequest
from dtflow.persistence.assets import AssetRegistry
from dtflow.services import session
from dtflow.services.lora_store import LoraStore

BOUND, MATCHED, AMBIGUOUS, UNPREPARED = "bound", "matched", "ambiguous", "unprepared"
_EXTENSIONS = (".ckpt", ".safetensors", ".pt", ".bin")
_SUFFIXES = ("_lora_f16", "_lora_q8p", "_lora")


@dataclass(frozen=True, slots=True)
class LoraMatch:
    requested: str
    status: str  # bound | matched | ambiguous | unprepared
    identifier: str  # the name to use: the prepared one when bound or matched, else the requested one
    candidates: tuple[str, ...] = ()  # the prepared identifiers an ambiguous name could mean


@dataclass(frozen=True, slots=True)
class RelinkEvidence:
    pool: tuple[PreparedAsset, ...] = ()
    records: Mapping[tuple[str, int], Mapping] | None = None
    origins: Mapping[tuple[str, int], tuple[str, ...]] | None = None


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


def match_names(names: Iterable[str], evidence: RelinkEvidence) -> dict[str, LoraMatch]:
    """One match per distinct name; pure, local evidence only."""
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
    result: dict[str, LoraMatch] = {}
    for name in dict.fromkeys(names):
        if name in exact:
            result[name] = LoraMatch(name, BOUND, name)
            continue
        key = name_key(name)
        hits = [content for content in contents if key in aliases[content] or (name.casefold() == shorts.get(content))]
        if len(hits) == 1:
            result[name] = LoraMatch(name, MATCHED, label[hits[0]])
        elif hits:
            result[name] = LoraMatch(name, AMBIGUOUS, name, tuple(sorted(label[hit] for hit in hits)))
        else:
            result[name] = LoraMatch(name, UNPREPARED, name)
    return result


async def load_evidence(state, backend: str, pool: Sequence[PreparedAsset] | None = None) -> RelinkEvidence:
    """Read the local receipt pool, canonical records and original file names once."""
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
    return RelinkEvidence(tuple(pool), records, origins)


async def resolve_names(state, backend: str, names: Iterable[str]) -> dict[str, LoraMatch]:
    names = tuple(dict.fromkeys(names))
    if not names or backend != "cloud":
        return {name: LoraMatch(name, UNPREPARED, name) for name in names}
    return match_names(names, await load_evidence(state, backend))


def relink_selection(
    loras: Sequence[LoRASelection], matches: Mapping[str, LoraMatch]
) -> tuple[tuple[LoRASelection, ...], dict[str, str]]:
    """Swap each uniquely matched name for the prepared one; weights and order stay. Returns the swaps made."""
    swaps = {
        item.identifier: matches[item.identifier].identifier
        for item in loras
        if item.identifier in matches and matches[item.identifier].status == MATCHED
    }
    if not swaps:
        return tuple(loras), {}
    seen: set[str] = set()
    result = []
    for item in loras:
        target = swaps.get(item.identifier, item.identifier)
        if target in seen:  # two imported names meaning one prepared LoRA: keep the first, never stack it twice
            continue
        seen.add(target)
        result.append(replace(item, identifier=target))
    return tuple(result), swaps


def relink_request(request: ResolvedGenerationRequest, matches: Mapping[str, LoraMatch]) -> ResolvedGenerationRequest:
    loras, swaps = relink_selection(request.loras, matches)
    if not swaps:
        return request
    note = ",".join(f"{old}>{new}" for old, new in swaps.items())
    return replace(request, loras=loras, resolution_evidence=(*request.resolution_evidence, ("lora_relinked", note)))


def problems(matches: Mapping[str, LoraMatch]) -> tuple[LoraMatch, ...]:
    """The names that still have no single prepared LoRA behind them."""
    return tuple(item for item in matches.values() if item.status in (AMBIGUOUS, UNPREPARED))


def summary(matches: Mapping[str, LoraMatch]) -> str:
    """One plain sentence for a status line; empty when every name is bound or relinked."""
    from dtflow.services.lora_naming import display_label

    unprepared = [display_label(item.requested) for item in matches.values() if item.status == UNPREPARED]
    ambiguous = [display_label(item.requested) for item in matches.values() if item.status == AMBIGUOUS]
    relinked = [item for item in matches.values() if item.status == MATCHED]
    parts = []
    if relinked:
        parts.append(f"{len(relinked)} matched to prepared LoRA(s)")
    if unprepared:
        parts.append("not prepared, so ignored until you Prepare them: " + ", ".join(unprepared))
    if ambiguous:
        parts.append("matches several prepared LoRAs, pick one: " + ", ".join(ambiguous))
    return "; ".join(parts)
