"""Wildcard lists from a folder (#129): ``<folder>/<name>.txt``, one entry per line, ``#`` lines and blanks skipped.

The only I/O of prompt variation. A plan binds the lists its template reaches (inline lists win), recording each file's
SHA-256 so the item evidence says exactly which list contents produced a prompt. Names are looked up as plain file
names inside the folder (a symlinked file is followed; names follow the file system's case rules).
"""

import hashlib
import re
from dataclasses import replace
from pathlib import Path

from dtflow.services.plan_errors import PlanError
from dtflow.services.planning import Plan, PromptVariation

MAX_FILE_BYTES = 1_000_000
MAX_ENTRIES = 10_000
_NAME = re.compile(r"__([A-Za-z0-9]+(?:[_\-][A-Za-z0-9]+)*)__")


_READ_CACHE: dict[Path, tuple[tuple[int, int], tuple[tuple[str, ...], str]]] = {}  # path -> (mtime_ns, size), result
_NAMES_CACHE: dict[Path, tuple[int, tuple[str, ...]]] = {}  # folder -> mtime_ns, names


def read_list(folder: Path, name: str) -> tuple[tuple[str, ...], str] | None:
    """(entries, sha256 of the file) for ``name`` in ``folder``, or None when there is no such list.

    A list is re-read and re-hashed only when its modification time or size changes, so the composer can ask per edit
    (an edit that keeps the size within a coarse timestamp, as on FAT file systems, is not noticed until it changes)
    """
    path = folder / f"{name}.txt"
    try:
        stat = path.stat()
    except OSError:
        return None
    if not path.is_file():
        return None
    signature = (stat.st_mtime_ns, stat.st_size)
    cached = _READ_CACHE.get(path)
    if cached is not None and cached[0] == signature:
        return cached[1]
    result = _read_file(path, name, stat.st_size)
    _READ_CACHE[path] = (signature, result)
    return result


def _read_file(path: Path, name: str, size: int) -> tuple[tuple[str, ...], str]:
    try:
        if size > MAX_FILE_BYTES:
            raise PlanError(f"Wildcard list '{name}' is larger than {MAX_FILE_BYTES // 1000} KB.")
        data = path.read_bytes()
    except OSError as exc:
        raise PlanError(f"Wildcard list '{name}' could not be read.") from exc
    try:
        text = data.decode("utf-8-sig")  # a Windows editor BOM is not part of the first entry
    except UnicodeDecodeError as exc:
        raise PlanError(f"Wildcard list '{name}' is not UTF-8 text.") from exc
    entries = tuple(line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#"))
    if len(entries) > MAX_ENTRIES:
        raise PlanError(f"Wildcard list '{name}' has more than {MAX_ENTRIES} entries.")
    return entries, hashlib.sha256(data).hexdigest()


def list_names(folder: Path) -> tuple[str, ...]:
    """The wildcard lists available in ``folder`` (file stems that are valid names), sorted."""
    try:
        stamp = folder.stat().st_mtime_ns
    except OSError:
        return ()
    cached = _NAMES_CACHE.get(folder)
    if cached is not None and cached[0] == stamp:
        return cached[1]
    names = tuple(sorted(p.stem for p in folder.glob("*.txt") if _NAME.fullmatch(f"__{p.stem}__")))
    _NAMES_CACHE[folder] = (stamp, names)
    return names


def fingerprint(folder: Path | None) -> tuple:
    """Name, modification time and size of every list in ``folder``: unchanged while no list was edited."""
    if folder is None:
        return ()
    try:
        return tuple(sorted((p.name, st.st_mtime_ns, st.st_size) for p in folder.glob("*.txt") if (st := p.stat())))
    except OSError:
        return ()


def bind_wildcards(plan: Plan, prompt: str, folder: Path | None) -> Plan:
    """The plan with every list its prompt template reaches loaded from ``folder`` (inline lists are kept as given)."""
    index = next((i for i, (name, v) in enumerate(plan.dimensions) if isinstance(v, PromptVariation)), None)
    if index is None or folder is None:
        return plan
    name, variation = plan.dimensions[index]
    template = variation.template if variation.template is not None else prompt
    lists = dict(variation.wildcards)
    sources = dict(variation.sources)
    pending = [*_NAME.findall(template)]
    while pending:
        wanted = pending.pop()
        if wanted in lists:
            pending.extend(n for entry in lists[wanted] for n in _NAME.findall(entry) if n not in lists)
            continue
        found = read_list(folder, wanted)
        if found is None:
            continue  # variants() reports the undefined list by name
        lists[wanted], sources[wanted] = found
        pending.extend(n for entry in found[0] for n in _NAME.findall(entry) if n not in lists)
    bound = replace(variation, wildcards=tuple(lists.items()), sources=tuple(sources.items()))
    dimensions = list(plan.dimensions)
    dimensions[index] = (name, bound)
    return replace(plan, dimensions=tuple(dimensions))
