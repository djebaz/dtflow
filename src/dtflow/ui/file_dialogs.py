"""Drop-in ``QFileDialog`` that remembers the last folder of every file and folder selector (#312).

Import ``QFileDialog`` from here instead of ``PySide6.QtWidgets``; the API is unchanged and nothing else is needed, so
a new selector is remembered from its first use. A selector is identified by its call site (module, function and
dialog kind), so unrelated selectors never share a folder. Pass ``remember_as="name"`` to share one folder on purpose
(import/export pairs, script input pickers) and ``remember=False`` to opt out (a folder that is itself a setting).

The folders live in ``<state>/settings.json`` (``dialog_paths``, per runtime). A refactor that moves or renames a call
site simply starts from the caller's folder once. A dialog never fails because of this memory: an unreadable, newer or
unwritable settings file means "nothing remembered", reported once as a warning.
"""

import json
import logging
import os
import sys
from collections.abc import Callable
from pathlib import Path

from PySide6.QtWidgets import QFileDialog as _QtFileDialog

from dtflow.diagnostics import event as diagnostic_event
from dtflow.persistence.runtime_environment import environment_key
from dtflow.persistence.settings import (
    MAX_DIALOG_KEY,
    forget_dialog_paths,
    load_dialog_paths,
    save_dialog_path,
)

log = logging.getLogger(__name__)

# Selectors whose folder was remembered by an older mechanism: the Prepare picker kept ``prepare_preferences.json``.
_LEGACY_PREPARE_KEY = "ui.lora_panel:_choose_asset_file:open_many"

_state: Path | None = None
_entries: dict[str, dict] = {}
_on_problem: Callable[[str], None] | None = None
_on_change: Callable[[], None] | None = None


def configure(
    state: Path | None, *, on_problem: Callable[[str], None] | None = None, on_change: Callable[[], None] | None = None
) -> None:
    """Point the memory at a state directory (None disables it); load what is remembered for this runtime."""
    global _state, _entries, _on_problem, _on_change
    _on_change = on_change
    _state, _on_problem = (Path(state) if state is not None else None), on_problem
    _entries = {}
    if _state is None:
        return
    try:
        _entries = load_dialog_paths(_state)
    except (OSError, ValueError) as exc:  # SettingsError is a ValidationError, a ValueError
        _problem("read_failed", exc)
    if _LEGACY_PREPARE_KEY not in _entries:
        legacy = _legacy_prepare_folder(_state)
        if legacy:
            _entries[_LEGACY_PREPARE_KEY] = {"path": legacy, "title": "Prepare converted LoRA files"}
    _trace("ui.dialog_folders_loaded", count=len(_entries), seeded=_LEGACY_PREPARE_KEY in _entries)


def _legacy_prepare_folder(state: Path) -> str:
    """The folder the retired ``prepare_preferences.json`` remembered for this runtime (read only, never rewritten)."""
    try:
        with (state / "prepare_preferences.json").open("rb") as handle:
            document = json.loads(handle.read(65537))
        folder = document["folders"][environment_key()]
    except (OSError, ValueError, KeyError, TypeError):
        return ""
    return folder if isinstance(folder, str) and 0 < len(folder) <= 4096 and "\x00" not in folder else ""


def remembered() -> list[tuple[str, str, str]]:
    """Every remembered selector as ``(key, title, path)``, most recently used first."""
    return [(key, entry["title"], entry["path"]) for key, entry in reversed(list(_entries.items()))]


def forget(keys: list[str] | None = None) -> None:
    """Forget the given selectors, or all of them; raises when the settings file cannot be rewritten."""
    if _state is not None:
        forget_dialog_paths(_state, keys)
    for key in list(_entries) if keys is None else keys:
        _entries.pop(key, None)


def _trace(name: str, **data) -> None:
    """Privacy-safe observation: selector names and booleans only, never a path (the shared schema redacts those)."""
    diagnostic_event(log, name, level=logging.INFO, data=data)


def _style(path: str) -> str:
    """The shape of a path for diagnostics (``empty``, ``unc``, ``windows``, ``posix``, ``relative``)."""
    if not path:
        return "empty"
    if _is_unc(path):
        return "unc"
    if len(path) > 2 and path[1] == ":" and path[2] in "\\/":
        return "windows"
    return "posix" if path.startswith("/") else "relative"


def _problem(reason: str, exc: BaseException) -> None:
    """Every failure is logged and shown, so one early failure cannot hide the ones after it."""
    diagnostic_event(
        log, "ui.dialog_folder_unavailable", level=logging.WARNING, data={"reason": reason, "error": type(exc).__name__}
    )
    if _on_problem is not None:
        _on_problem(reason)


def _is_unc(path: str) -> bool:
    return path.startswith(("\\\\", "//"))


def _usable_directory(path: str) -> str | None:
    """The nearest existing, readable ancestor; a UNC path is trusted as is, so a dead share cannot stall the UI."""
    if _is_unc(path):
        return path
    current = Path(path)
    for _ in range(64):
        try:
            if current.is_dir() and os.access(current, os.R_OK | os.X_OK):
                return str(current)
        except OSError:
            pass
        if current.parent == current:
            return None
        current = current.parent
    return None


def _key(kind: str, remember_as: str | None, frame) -> str:
    if remember_as:
        return remember_as[:MAX_DIALOG_KEY]
    module = frame.f_globals.get("__name__", "?").removeprefix("dtflow.")
    return f"{module}:{frame.f_code.co_qualname}:{kind}"[:MAX_DIALOG_KEY]


def _start(kind: str, key: str, requested) -> str:
    """The remembered folder (with the caller's suggested file name for saves), else exactly what the caller asked."""
    requested = os.fspath(requested or "")
    entry = _entries.get(key)
    directory = _usable_directory(entry["path"]) if entry is not None else None
    if directory is None:
        return requested
    if kind == "folder" or not requested or os.path.isdir(requested):
        return directory
    return str(Path(directory) / Path(requested).name)


def _remember(key: str, title: str, chosen, *, folder: bool) -> None:
    if _state is None:
        return
    first = chosen[0] if isinstance(chosen, (list, tuple)) else chosen
    if not first:
        return
    path = first if folder else os.path.dirname(os.path.abspath(first))
    if _entries.get(key) == {"path": path, "title": title[:200]}:
        _trace("ui.dialog_folder_saved", selector=key, changed=False, saved=True, path_style=_style(path))
        return
    _entries.pop(key, None)
    _entries[key] = {"path": path, "title": title[:200]}
    saved = True
    try:
        save_dialog_path(_state, key, path, title)
    except (OSError, ValueError) as exc:
        saved = False
        _problem("write_failed", exc)
    _trace("ui.dialog_folder_saved", selector=key, changed=True, saved=saved, path_style=_style(path))
    if _on_change is not None:
        _on_change()


def _trace_open(key: str, kind: str, requested, start: str) -> None:
    entry = _entries.get(key)
    _trace(
        "ui.dialog_folder_open",
        selector=key,
        kind=kind,
        remembered=entry is not None,
        from_memory=entry is not None and start != os.fspath(requested or ""),
        start_style=_style(start),
        start_is_dir=os.path.isdir(start) if start and not _is_unc(start) else None,
    )


def _static(kind, qt_method, parent, caption, requested, tail, kwargs, remember, remember_as):
    """Run a static Qt dialog at the remembered folder and remember where it was accepted (frame 2 is the caller)."""
    if not remember or _state is None:
        return qt_method(parent, caption, requested, *tail, **kwargs)
    key = _key(kind, remember_as, sys._getframe(2))
    start = _start(kind, key, requested)
    _trace_open(key, kind, requested, start)
    result = qt_method(parent, caption, start, *tail, **kwargs)
    chosen = result[0] if isinstance(result, tuple) else result
    if chosen:
        _remember(key, caption, chosen, folder=kind == "folder")
    return result


class QFileDialog(_QtFileDialog):
    """``QFileDialog`` whose selectors remember their own folder; see the module docstring."""

    @staticmethod
    def getOpenFileName(parent=None, caption="", dir="", filter="", *args, remember=True, remember_as=None, **kwargs):  # noqa: A002
        tail = (filter, *args)
        return _static("open", _QtFileDialog.getOpenFileName, parent, caption, dir, tail, kwargs, remember, remember_as)

    @staticmethod
    def getOpenFileNames(parent=None, caption="", dir="", filter="", *args, remember=True, remember_as=None, **kwargs):  # noqa: A002
        tail = (filter, *args)
        return _static(
            "open_many", _QtFileDialog.getOpenFileNames, parent, caption, dir, tail, kwargs, remember, remember_as
        )

    @staticmethod
    def getSaveFileName(parent=None, caption="", dir="", filter="", *args, remember=True, remember_as=None, **kwargs):  # noqa: A002
        tail = (filter, *args)
        return _static("save", _QtFileDialog.getSaveFileName, parent, caption, dir, tail, kwargs, remember, remember_as)

    @staticmethod
    def getExistingDirectory(parent=None, caption="", dir="", *args, remember=True, remember_as=None, **kwargs):  # noqa: A002
        return _static(
            "folder", _QtFileDialog.getExistingDirectory, parent, caption, dir, args, kwargs, remember, remember_as
        )

    def __init__(self, *args, remember=True, remember_as=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._memory_key = None
        if remember and _state is not None:
            self._memory_key = _key("dialog", remember_as, sys._getframe(1))
            entry = _entries.get(self._memory_key)
            directory = _usable_directory(entry["path"]) if entry is not None else None
            _trace(
                "ui.dialog_folder_open",
                selector=self._memory_key,
                kind="dialog",
                remembered=entry is not None,
                from_memory=directory is not None,
                start_style=_style(directory or ""),
                start_is_dir=None,
            )
            if directory is not None:
                self.setDirectory(directory)
            self.accepted.connect(self._remember_selection)

    def _remember_selection(self) -> None:
        chosen = self.selectedFiles()
        if chosen and self._memory_key is not None:
            _remember(self._memory_key, self.windowTitle(), chosen, folder=self.fileMode() == _QtFileDialog.Directory)
