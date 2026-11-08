"""The local Workflows library: ComfyUI v1.0 ``.json`` files under ``<state>/workflows/library/`` (#223). Qt-free.

Durable user authoring state, not cache. Files are always written by ``dump_workflow`` and read back through the
strict ``load_workflow`` (``draft=True``: an unfinished workflow may be saved without a checkpoint; every structural
rule still applies). The file name (without ``.json``) is the workflow's display name. All functions block on small
local files; the desktop calls them through ``asyncio.to_thread``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from dtflow.core.workflows import Graph, WorkflowError, dump_workflow, load_workflow
from dtflow.services.artifacts import atomic_write

LIBRARY_DIR = Path("workflows") / "library"
REFERENCE = Path(__file__).parents[1] / "resources" / "workflows" / "reference_workflow_v1.json"
MAX_FILE_BYTES = 2 * 1024 * 1024  # a 200-node workflow is far below this; larger files are refused unread
MAX_NAME = 80
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{n}" for n in range(1, 10)), *(f"LPT{n}" for n in range(1, 10))}
_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


@dataclass(frozen=True, slots=True)
class LibraryEntry:
    name: str
    path: Path
    modified: float
    problem: str | None  # the single reason a file cannot be opened, or None


def library_dir(state: Path) -> Path:
    return Path(state) / LIBRARY_DIR


def check_name(name: str) -> str:
    """The display name as a file stem; raises WorkflowError with one reason."""
    name = " ".join(str(name).split())
    if not name:
        raise WorkflowError("Enter a workflow name.")
    if len(name) > MAX_NAME:
        raise WorkflowError(f"A workflow name may have at most {MAX_NAME} characters.")
    if _FORBIDDEN.search(name) or name.startswith(".") or name.endswith("."):
        raise WorkflowError('A workflow name cannot contain < > : " / \\ | ? * or start or end with a dot.')
    if name.split(".")[0].strip().upper() in _RESERVED:
        raise WorkflowError(f"{name!r} is reserved by Windows; choose another workflow name.")
    return name


def read_file(path: Path) -> dict:
    """Read one workflow file as JSON, size-bounded; raises WorkflowError with one reason."""
    path = Path(path)
    try:
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            raise WorkflowError(f"{path.name} is larger than {MAX_FILE_BYTES // (1024 * 1024)} MiB.")
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise WorkflowError(f"{path.name} could not be read ({exc.strerror or exc}).") from exc
    except UnicodeDecodeError as exc:
        raise WorkflowError(f"{path.name} is not UTF-8 text.") from exc
    try:
        return json.loads(text)
    except ValueError as exc:
        raise WorkflowError(f"{path.name} is not valid JSON: {exc}.") from exc


def load_file(path: Path) -> Graph:
    """Strict whole-file import (draft rules): one reason on any problem, nothing repaired or dropped."""
    return load_workflow(read_file(path), draft=True)


def list_library(state: Path) -> list[LibraryEntry]:
    folder = library_dir(state)
    if not folder.is_dir():
        return []
    entries = []
    for path in sorted(folder.glob("*.json"), key=lambda p: p.stem.casefold()):
        if not path.is_file():
            continue
        try:
            load_file(path)
            problem = None
        except WorkflowError as exc:
            problem = str(exc)
        try:
            modified = path.stat().st_mtime
        except OSError:
            modified = 0.0
        entries.append(LibraryEntry(path.stem, path, modified, problem))
    return entries


def _target(state: Path, name: str) -> Path:
    return library_dir(state) / f"{check_name(name)}.json"


def _taken(state: Path, name: str, *, besides: Path | None = None) -> bool:
    wanted = check_name(name).casefold()
    folder = library_dir(state)
    if not folder.is_dir():
        return False
    return any(
        path.stem.casefold() == wanted and (besides is None or path.resolve() != Path(besides).resolve())
        for path in folder.glob("*.json")
    )


def unique_name(state: Path, base: str) -> str:
    base = check_name(base)
    if not _taken(state, base):
        return base
    for number in range(2, 1000):
        candidate = check_name(f"{base[: MAX_NAME - 6]} {number}")
        if not _taken(state, candidate):
            return candidate
    raise WorkflowError("Too many workflows share this name.")


def _write(path: Path, graph: Graph) -> None:
    data = json.dumps(dump_workflow(graph), indent=2, ensure_ascii=False).encode("utf-8") + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, data)


def save_workflow(state: Path, name: str, graph: Graph, *, current: Path | None = None) -> Path:
    """Save ``graph`` as ``name``; ``current`` is the file it was opened from (replaced, or renamed away)."""
    target = _target(state, name)
    if _taken(state, name, besides=current):
        raise WorkflowError(f"A workflow named {check_name(name)!r} already exists.")
    load_workflow(dump_workflow(graph), draft=True)  # never write what could not be read back
    _write(target, graph)
    if current is not None and Path(current).resolve() != target.resolve():
        Path(current).unlink(missing_ok=True)
    return target


def create_from_reference(state: Path, name: str = "New workflow") -> tuple[Path, Graph]:
    """A new library workflow that starts as a copy of the bundled reference fixture (fresh workflow id)."""
    graph = load_workflow(json.loads(REFERENCE.read_text(encoding="utf-8")), draft=True)
    # New authoring defaults to Balanced; imported/older V1 files remain precise.
    for node in graph.nodes:
        if node.type == "DTFDetectFaceMask":
            node.face_mask_method = "balanced"
    graph.workflow_id = str(uuid4())
    return save_workflow(state, unique_name(state, name), graph), graph


def rename_workflow(state: Path, path: Path, name: str) -> Path:
    path = Path(path)
    target = _target(state, name)
    if _taken(state, name, besides=path):
        raise WorkflowError(f"A workflow named {check_name(name)!r} already exists.")
    if not path.is_file():
        raise WorkflowError(f"{path.stem} no longer exists.")
    path.replace(target)
    return target


def duplicate_workflow(state: Path, path: Path) -> Path:
    graph = load_file(path)
    graph.workflow_id = str(uuid4())
    return save_workflow(state, unique_name(state, f"{Path(path).stem} copy"), graph)


def delete_workflow(path: Path) -> None:
    Path(path).unlink(missing_ok=True)


def import_workflow(state: Path, source: Path) -> tuple[Path, Graph]:
    """Strictly read an external file and add it to the library under its own (unique) name."""
    graph = load_file(source)
    stem = _FORBIDDEN.sub(" ", Path(source).stem).strip(" .") or "Imported workflow"
    return save_workflow(state, unique_name(state, stem[:MAX_NAME]), graph), graph


def export_workflow(graph: Graph, target: Path) -> Path:
    target = Path(target)
    if target.suffix.lower() != ".json":
        target = target.with_name(target.name + ".json")
    _write(target, graph)
    return target
