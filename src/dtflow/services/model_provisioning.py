"""Shared detection-model catalogue and verified downloads for Generate and Workflows (#223, D7/D8).

- The read-only catalogue is ``resources/catalogue/detection_models.json``: role, display name, pinned publisher URL,
  SHA-256, approximate size, licence name/URL and source page. DTFlow never redistributes weights (D7).
- Downloads run only on an explicit user request (one preflight confirmation listing each licence), off the Qt thread,
  into ``<state>/models/person/<sha256>.onnx``; each file is SHA-256 checked, contract-checked with
  ``person_selection.classify_models`` and recorded as an ordinary ``ModelRef`` in ``person_models.json``
  (schema 1, no bump). Existing user-selected models are never replaced for a role that is already usable.

Transfers use the standard library HTTP client: it honours ``HTTPS_PROXY``/system proxy settings and the platform
certificate store, follows HTTPS-only redirects (Hugging Face resolve URLs redirect to a CDN) and is bounded by
``person_selection.MAX_MODEL_BYTES``.
"""

from __future__ import annotations

import errno
import hashlib
import json
import logging
import os
import ssl
import threading
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from tempfile import NamedTemporaryFile

from dtflow.core.models import ValidationError
from dtflow.diagnostics import event as diagnostic_event
from dtflow.persistence.person_models import ModelRef, PersonModels, load_person_models, save_person_models
from dtflow.services import person_selection

log = logging.getLogger(__name__)

ROLES = ("segmentation", "clothes", "faces")
CATALOGUE_PATH = Path(__file__).parents[1] / "resources" / "catalogue" / "detection_models.json"
CHUNK = 256 * 1024
TIMEOUT = 30.0
_ROLE_INDEX = {role: index for index, role in enumerate(ROLES)}


class DownloadCancelled(ValidationError):
    """The user stopped the download; the partial file was removed and nothing was recorded for that model."""


@dataclass(frozen=True, slots=True)
class CatalogueEntry:
    role: str  # segmentation | clothes | faces
    name: str  # display name, e.g. "YOLO26n person segmentation"
    url: str  # pinned publisher URL
    sha256: str
    size_mib: float  # approximate, for display; the transfer total comes from the response
    licence: str  # licence name as shown to the user
    licence_url: str
    source_url: str  # publisher page


@dataclass(frozen=True, slots=True)
class ProvisionProgress:
    """One truthful progress report: ``stage`` is a short label such as "Downloading YOLO26n person segmentation",
    "Verifying download" or "Checking model contract"; ``total`` is None when the size is unknown."""

    role: str
    stage: str
    done: int = 0
    total: int | None = None


@cache
def catalogue() -> tuple[CatalogueEntry, ...]:
    """The pinned catalogue, in ``ROLES`` order."""
    try:
        document = json.loads(CATALOGUE_PATH.read_text(encoding="utf-8"))
        entries = tuple(CatalogueEntry(**item) for item in document["models"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValidationError("The detection-model catalogue shipped with DTFlow could not be read.") from exc
    if document.get("schema_version") != 1 or sorted(entry.role for entry in entries) != sorted(ROLES):
        raise ValidationError("The detection-model catalogue shipped with DTFlow is malformed.")
    for entry in entries:
        if not entry.url.startswith("https://") or len(entry.sha256) != 64:
            raise ValidationError("The detection-model catalogue shipped with DTFlow is malformed.")
    return tuple(sorted(entries, key=lambda entry: _ROLE_INDEX[entry.role]))


def missing_entries(
    models: PersonModels, issues: Mapping[str, str | None], roles: Iterable[str] = ROLES
) -> tuple[CatalogueEntry, ...]:
    """Pure: catalogue entries for ``roles`` that are not configured, or whose configured file has an issue.

    ``issues`` is ``person_selection.model_issues(models)`` (already computed, e.g. by the editor's readiness check)."""
    wanted = set(roles)
    return tuple(
        entry
        for entry in catalogue()
        if entry.role in wanted and (getattr(models, entry.role) is None or issues.get(entry.role))
    )


def missing_models(state: Path, roles: Iterable[str] = ROLES) -> tuple[CatalogueEntry, ...]:
    """Catalogue entries for the requested roles whose configured model is absent or unreadable (``model_issues``).

    Reads local files only; never downloads or contacts the network."""
    models = load_person_models(state)
    return missing_entries(models, person_selection.model_issues(models), roles)


def install_dir(state: Path) -> Path:
    return Path(state) / "models" / "person"


class _HttpsOnlyRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.lower().startswith("https://"):
            raise ValidationError(
                "The publisher redirected the download to a non-HTTPS address; nothing was installed."
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _opener():
    # Environment/system proxies (ProxyHandler default) and the platform trust store.
    return urllib.request.build_opener(
        urllib.request.ProxyHandler(),
        urllib.request.HTTPSHandler(context=ssl.create_default_context()),
        _HttpsOnlyRedirect(),
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _usable(ref: ModelRef | None) -> bool:
    return ref is not None and person_selection.model_issue(PersonModels(ref)) is None


def _disk_error(exc: OSError) -> ValidationError:
    if exc.errno == errno.ENOSPC:
        return ValidationError("Not enough disk space to install the model; free some space and try again.")
    return ValidationError("The model could not be written to DTFlow's models folder; check that it is writable.")


def _fetch(entry: CatalogueEntry, folder: Path, report, cancel) -> Path:
    """Stream one pinned file into a temporary file in ``folder``; returns the verified temporary path."""
    request = urllib.request.Request(entry.url, headers={"User-Agent": "DTFlow model download"})
    temporary: Path | None = None
    stage = f"Downloading {entry.name}"
    try:
        try:
            response = _opener().open(request, timeout=TIMEOUT)
        except urllib.error.HTTPError as exc:
            raise ValidationError(
                f"The publisher answered HTTP {exc.code} for {entry.name}; nothing was installed. Try again later."
            ) from exc
        except (urllib.error.URLError, TimeoutError, ssl.SSLError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise ValidationError(
                f"Could not reach the publisher of {entry.name} ({reason}); check the network or proxy and try again."
            ) from exc
        with response:
            length = response.headers.get("Content-Length")
            total = int(length) if length and length.isdigit() else None
            if total is not None and total > person_selection.MAX_MODEL_BYTES:
                raise ValidationError(f"{entry.name} is larger than 256 MiB; it was not downloaded.")
            digest = hashlib.sha256()
            done = 0
            report(stage, 0, total)
            try:
                with NamedTemporaryFile(dir=folder, prefix=".download-", suffix=".part", delete=False) as handle:
                    temporary = Path(handle.name)
                    while True:
                        if cancel is not None and cancel.is_set():
                            raise DownloadCancelled("Download cancelled; the partial file was removed.")
                        try:
                            chunk = response.read(CHUNK)
                        except (TimeoutError, OSError) as exc:
                            raise ValidationError(
                                f"The download of {entry.name} was interrupted ({exc}); nothing was installed. "
                                "Try again."
                            ) from exc
                        if not chunk:
                            break
                        done += len(chunk)
                        if done > person_selection.MAX_MODEL_BYTES:
                            raise ValidationError(f"{entry.name} is larger than 256 MiB; it was not installed.")
                        digest.update(chunk)
                        handle.write(chunk)
                        report(stage, done, total)
                    handle.flush()
                    os.fsync(handle.fileno())
            except OSError as exc:
                raise _disk_error(exc) from exc
        if total is not None and done != total:
            raise ValidationError(f"The download of {entry.name} ended early; nothing was installed. Try again.")
        report("Verifying download", done, total)
        if digest.hexdigest() != entry.sha256:
            raise ValidationError(f"The publisher's file for {entry.name} changed; not installed.")
        return temporary
    except BaseException:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise


def _check_contract(entry: CatalogueEntry, path: Path) -> None:
    try:
        roles = person_selection.classify_models([path])
    except Exception as exc:  # onnxruntime raises its own errors for a file that is not ONNX at all
        raise ValidationError(
            f"{entry.name} does not match the {entry.role} model contract DTFlow expects; not installed."
        ) from exc
    if roles[_ROLE_INDEX[entry.role]] is None:
        raise ValidationError(
            f"{entry.name} does not match the {entry.role} model contract DTFlow expects; not installed."
        )


def _install(entry: CatalogueEntry, folder: Path, report, cancel) -> ModelRef:
    target = folder / f"{entry.sha256}.onnx"
    if target.is_file():
        report("Verifying existing file", 0, None)
        try:
            reusable = _sha256_file(target) == entry.sha256
        except OSError:
            reusable = False
        if reusable:
            report("Checking model contract", 0, None)
            _check_contract(entry, target)
            return ModelRef(str(target.resolve()), entry.sha256)
    temporary = _fetch(entry, folder, report, cancel)
    try:
        report("Checking model contract", 0, None)
        _check_contract(entry, temporary)
        try:
            os.replace(temporary, target)
        except OSError as exc:
            raise _disk_error(exc) from exc
    finally:
        temporary.unlink(missing_ok=True)
    return ModelRef(str(target.resolve()), entry.sha256)


def download_models(
    state: Path,
    entries: Iterable[CatalogueEntry],
    *,
    on_progress: Callable[[ProvisionProgress], None] | None = None,
    cancel: threading.Event | None = None,
) -> PersonModels:
    """Download, verify (SHA-256 + ONNX contract) and record each entry; returns the updated ``PersonModels``.

    Blocking: call from a worker thread. Raises ``ValidationError`` with an actionable reason on network, hash or
    contract failure; a partial file is removed. ``cancel`` stops between chunks (``DownloadCancelled``). Models
    installed before a failure stay recorded. A role that became usable meanwhile (the user chose a file) is kept."""
    folder = install_dir(state)
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise _disk_error(exc) from exc
    load_person_models(state)  # refuse early when the settings file is unreadable or newer
    for entry in entries:
        if entry.role not in _ROLE_INDEX:
            raise ValidationError(f"Unknown detection model role {entry.role!r}.")
        if cancel is not None and cancel.is_set():
            raise DownloadCancelled("Download cancelled; nothing more was installed.")

        def report(stage, done, total, role=entry.role):
            if on_progress is not None:
                on_progress(ProvisionProgress(role, stage, done, total))

        if _usable(getattr(load_person_models(state), entry.role)):
            continue  # never replace a usable user-selected model
        diagnostic_event(log, "models.download.started", data={"role": entry.role})
        try:
            ref = _install(entry, folder, report, cancel)
        except ValidationError as exc:
            diagnostic_event(
                log,
                "models.download.failed",
                level=logging.WARNING,
                data={"role": entry.role, "reason": type(exc).__name__},
            )
            raise
        current = load_person_models(state)  # re-read: keep every other role exactly as it is now
        if _usable(getattr(current, entry.role)):
            continue
        fields = {role: getattr(current, role) for role in ROLES}
        fields[entry.role] = ref
        try:
            save_person_models(state, PersonModels(**fields))
        except OSError as exc:
            raise _disk_error(exc) from exc
        diagnostic_event(log, "models.download.installed", data={"role": entry.role})
    return load_person_models(state)
