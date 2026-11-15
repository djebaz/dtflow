"""Civitai client: bounded metadata resolution and a safe LoRA download (#462, epic #327).

Qt-free and blocking: call from a worker thread. Design decisions live in
``devdocs/plans/2026-11-15-civitai-downloads-design.md``.

- Metadata and download go to ``civitai.com`` only. The token is sent only as ``Authorization: Bearer`` (never
  ``?token=``), only to ``civitai.com``, and is dropped for the rest of a request at the first host change. It is never
  written to disk, to diagnostics or to an error message, and a metadata response that contains it is discarded.
- ``httpx`` runs with ``follow_redirects=False``; redirects are followed here, https only, with a hop cap. Metadata
  calls may only be redirected within the same host. (``model_provisioning`` is deliberately not reused for this: its
  ``urllib`` handler would forward credentials to any https host.)
- Only bounded normalised fields are retained; raw responses and preview media never are.
- A download streams into ``<state>/lora_sources/.staging``, is size-checked, header-checked and SHA-256 measured, and
  is published atomically as ``<state>/lora_sources/<sha256>.safetensors`` (the staged file is adopted in place, so the
  bytes are written once). The server-supplied filename is sanitised and kept only as a display value.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import ssl
import stat
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlsplit

import httpx

from dtflow.core.models import ValidationError
from dtflow.diagnostics import event as diagnostic_event

log = logging.getLogger(__name__)

HOST = "civitai.com"
API_ROOT = f"https://{HOST}/api/v1"
SOURCES_DIR = "lora_sources"
STAGING_DIR = ".staging"
CHUNK = 256 * 1024
MAX_METADATA_BYTES = 32 * 1024 * 1024  # accommodates large multi-version listing responses
MAX_ERROR_BYTES = 8 * 1024
MAX_DOWNLOAD_BYTES = 4 * 1024**3  # a LoRA bound; the 256 MiB detection-model cap is far too small
FREE_SPACE_MARGIN = 64 * 1024**2
MAX_HOPS = 5
MAX_CONCURRENT = 3
STALE_STAGING_SECONDS = 3600.0
SIZE_TOLERANCE = 0.01  # Civitai publishes sizeKB as a rounded float; used only as a sanity bound
MAX_HEADER_BYTES = 100 * 1024**2  # safetensors JSON header sanity bound
TIMEOUT = httpx.Timeout(30.0, connect=15.0)
TRANSIENT = (httpx.ConnectError, httpx.TimeoutException)
LORA_TYPE = "LORA"

_SHA256 = re.compile(r"[0-9a-fA-F]{64}\Z")
_TOKEN = re.compile(r"[\x21-\x7e]{8,512}\Z")
_UNSAFE = re.compile(r"[^A-Za-z0-9._ \-()\[\]+]")
_ACTIVE_STAGING: set[Path] = set()
_ACTIVE_LOCK = threading.Lock()


class CivitaiError(ValidationError):
    """Civitai failure with safe diagnostic fields separate from potentially private provider wording."""

    def __init__(
        self,
        message: str,
        *,
        reason: str | None = None,
        http_status: int | None = None,
        bytes_read: int | None = None,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.http_status = http_status
        self.bytes_read = bytes_read


class CivitaiMissing(CivitaiError):
    """404/410: the model, version or file is gone or was never public."""


class CivitaiCancelled(CivitaiError):
    """Stopped by the user or by shutdown; the staging file was removed."""


class CivitaiUnsupportedFamily(CivitaiError):
    """The version's base-model label maps to no family in ``lora_convert/data/families.json``."""


class CivitaiChoice(CivitaiError):
    """Several versions or files match; nothing is chosen silently. ``options`` lists them for the caller."""

    def __init__(self, message: str, options: tuple[dict, ...]):
        super().__init__(message)
        self.options = options


@dataclass(frozen=True, slots=True)
class CivitaiRef:
    model_id: int | None = None
    version_id: int | None = None


@dataclass(frozen=True, slots=True)
class CivitaiFile:
    id: int | None
    name: str
    size_bytes: int | None  # approximate: Civitai publishes sizeKB
    sha256: str | None
    primary: bool
    download_url: str


@dataclass(frozen=True, slots=True)
class CivitaiVersion:
    """Bounded normalised metadata for one version and its file (no raw response, no previews)."""

    provider: str
    model_id: int
    version_id: int
    model_name: str
    version_name: str
    creator: str | None
    base_model: str | None  # the original Civitai label, unmapped
    model_type: str | None
    permissions: dict
    tags: tuple[str, ...]
    trained_words: tuple[str, ...]
    trained_words_state: str  # "missing" | "empty" | "populated"
    availability: str | None
    file: CivitaiFile


@dataclass(frozen=True, slots=True)
class DownloadedFile:
    path: Path
    sha256: str
    size: int
    display_name: str  # sanitised server filename, a display value only
    reused: bool  # an existing stored file with the same published hash was used; nothing was transferred
    hash_verified: bool  # False only when Civitai published no SHA-256


@dataclass(frozen=True, slots=True)
class DownloadProgress:
    done: int
    total: int | None
    stage: str = "Downloading"


# --------------------------------------------------------------------------------------------------------- references


def parse_reference(text: str) -> CivitaiRef:
    """A bare version id, a model page link (``?modelVersionId=`` selects a version) or a download link."""
    value = (text or "").strip()
    if value.isdigit() and len(value) <= 12:
        return CivitaiRef(version_id=int(value))
    parts = urlsplit(value)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or host not in (HOST, f"www.{HOST}") or parts.username or parts.password:
        raise CivitaiError("Enter a version id or an https://civitai.com model or download link.")
    segments = [segment for segment in parts.path.split("/") if segment]
    query = parse_qs(parts.query)

    def number(raw: str | None) -> int | None:
        return int(raw) if raw and raw.isdigit() and len(raw) <= 12 else None

    if segments[:3] == ["api", "download", "models"] and len(segments) >= 4 and number(segments[3]):
        return CivitaiRef(version_id=number(segments[3]))
    if len(segments) >= 2 and segments[0] == "model-versions" and number(segments[1]):
        return CivitaiRef(version_id=number(segments[1]))
    if len(segments) >= 2 and segments[0] == "models" and number(segments[1]):
        return CivitaiRef(model_id=number(segments[1]), version_id=number((query.get("modelVersionId") or [None])[0]))
    raise CivitaiError("That link is not a Civitai model, version or download link.")


def _valid_token(token: str | None) -> str | None:
    if token is None or token == "":
        return None
    token = token.strip()
    if not _TOKEN.fullmatch(token):
        raise CivitaiError("The Civitai token has an unexpected format; check that it was copied whole.")
    return token


# ---------------------------------------------------------------------------------------------------------- transport


def new_client() -> httpx.Client:
    """No automatic redirects; platform trust store; environment proxies honoured."""
    return httpx.Client(
        follow_redirects=False,
        timeout=TIMEOUT,
        verify=ssl.create_default_context(),
        headers={"User-Agent": "DTFlow"},
    )


def _checked_url(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        raise CivitaiError("Civitai redirected to an address that is not a plain https address; nothing was fetched.")
    return url


def _open(
    stack: ExitStack, client: httpx.Client, url: str, token: str | None, *, same_host_only: bool
) -> httpx.Response:
    """GET with manual https-only redirects. ``Authorization`` goes only to civitai.com, and once any hop has left
    that host it is never sent again for this request."""
    current = _checked_url(url)
    if urlsplit(current).hostname != HOST:
        raise CivitaiError("Only civitai.com addresses are fetched.")
    send_token = token is not None
    for _ in range(MAX_HOPS + 1):
        host = urlsplit(current).hostname
        if host != HOST:
            send_token = False
        headers = {"Authorization": f"Bearer {token}"} if send_token else {}
        response = stack.enter_context(client.stream("GET", current, headers=headers))
        if response.status_code not in (301, 302, 303, 307, 308):
            return response
        location = response.headers.get("location")
        if not location:
            raise CivitaiError("Civitai sent a redirect without a destination; nothing was downloaded.")
        target = _checked_url(urljoin(current, location))
        if same_host_only and urlsplit(target).hostname != HOST:
            raise CivitaiError("Civitai redirected a metadata request to another host; nothing was used.")
        current = target
    raise CivitaiError("Civitai redirected too many times; nothing was downloaded.")


def _clean(text: str, token: str | None) -> str:
    if token:
        text = text.replace(token, "[token]")
    return " ".join("".join(c if c.isprintable() else " " for c in text).split())[:240]


def _failure(response: httpx.Response, token: str | None) -> CivitaiError:
    """Plain message keeping Civitai's own wording (read bounded; never the URL)."""
    raw = b""
    try:
        for chunk in response.iter_bytes():
            raw += chunk
            if len(raw) >= MAX_ERROR_BYTES:
                break
    except httpx.HTTPError:
        pass
    wording = ""
    try:
        body = json.loads(raw[:MAX_ERROR_BYTES])
        if isinstance(body, dict):
            for key in ("error", "message", "detail"):
                if isinstance(body.get(key), str):
                    wording = _clean(body[key], token)
                    break
    except ValueError:
        pass
    code = response.status_code
    suffix = f' Civitai says: "{wording}".' if wording else ""
    if code in (404, 410):
        return CivitaiMissing(
            f"Civitai answered {code}: the item was not found or is no longer available.{suffix}",
            reason="http_error",
            http_status=code,
        )
    if code == 401:
        return CivitaiError(
            f"Civitai answered 401: a missing or invalid token. Check the Civitai token.{suffix}",
            reason="http_error",
            http_status=code,
        )
    if code == 403:
        return CivitaiError(
            f"Civitai answered 403: this item needs access the token does not have (gated, early access or terms "
            f"to accept on Civitai).{suffix}",
            reason="http_error",
            http_status=code,
        )
    if code == 429:
        return CivitaiError(
            f"Civitai answered 429: rate limited. Wait a while and retry.{suffix}",
            reason="http_error",
            http_status=code,
        )
    return CivitaiError(f"Civitai answered HTTP {code}.{suffix}", reason="http_error", http_status=code)


def _transient(exc: BaseException) -> CivitaiError:
    return CivitaiError(
        f"Could not reach Civitai ({type(exc).__name__}); check the network or proxy and retry.",
        reason="network_failure",
    )


def _fetch_json(client: httpx.Client, url: str, token: str | None) -> dict:
    """Bounded metadata GET with one retry for definite connectivity/timeout failures; returns the JSON object."""
    for attempt in (0, 1):
        try:
            with ExitStack() as stack:
                response = _open(stack, client, url, token, same_host_only=True)
                if response.status_code != 200:
                    raise _failure(response, token)
                raw = b""
                for chunk in response.iter_bytes():
                    raw += chunk
                    if len(raw) > MAX_METADATA_BYTES:
                        raise CivitaiError(
                            "Civitai's answer was larger than expected; it was not used.",
                            reason="response_too_large",
                            bytes_read=len(raw),
                        )
            break
        except TRANSIENT as exc:
            if attempt:
                raise _transient(exc) from exc
        except httpx.HTTPError as exc:
            raise _transient(exc) from exc
    if token and token.encode() in raw:
        raise CivitaiError("Civitai's answer contained the token and was discarded.")
    try:
        body = json.loads(raw)
    except ValueError as exc:
        raise CivitaiError("Civitai's answer was not valid JSON; it was not used.", reason="invalid_json") from exc
    if not isinstance(body, dict):
        raise CivitaiError("Civitai's answer had an unexpected shape; it was not used.", reason="unexpected_json_shape")
    return body


def _get_json(client: httpx.Client, path: str, token: str | None, expected_id: int) -> dict:
    body = _fetch_json(client, f"{API_ROOT}{path}", token)
    if body.get("id") != expected_id:
        raise CivitaiError("Civitai's answer did not match the requested id; it was not used.")
    return body


def get_listing(client: httpx.Client, query: str, token: str | None = None) -> dict:
    """``GET /api/v1/models?<query>`` (the model listing used by the gallery): the bounded JSON object."""
    return _fetch_json(client, f"{API_ROOT}/models?{query}", _valid_token(token))


# -------------------------------------------------------------------------------------------------------- normalising


def _text(value, limit: int = 200) -> str | None:
    return value[:limit] if isinstance(value, str) and value else None


def _int(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _sha256(hashes) -> str | None:
    if isinstance(hashes, dict):
        for key, value in hashes.items():
            if isinstance(key, str) and key.upper() == "SHA256" and isinstance(value, str) and _SHA256.fullmatch(value):
                return value.lower()
    return None


def _file(item: dict, version_id: int) -> CivitaiFile | None:
    name = _text(item.get("name"), 255)
    if name is None:
        return None
    size_kb = item.get("sizeKB")
    size = (
        int(size_kb * 1024)
        if isinstance(size_kb, (int, float)) and not isinstance(size_kb, bool) and size_kb > 0
        else None
    )
    url = item.get("downloadUrl")
    canonical = f"https://{HOST}/api/download/models/{version_id}"
    if isinstance(url, str) and url:
        parts = urlsplit(url)
        if (
            parts.scheme != "https"
            or parts.hostname != HOST
            or not parts.path.startswith("/api/download/models/")
            or "token" in parse_qs(parts.query)
        ):
            raise CivitaiError("Civitai listed a download address outside civitai.com; it was not used.")
    else:
        url = canonical
    return CivitaiFile(_int(item.get("id")), name, size, _sha256(item.get("hashes")), item.get("primary") is True, url)


def _permissions(model: dict) -> dict:
    out: dict = {}
    for key in ("allowNoCredit", "allowDerivatives", "allowDifferentLicense", "allowCommercialUse"):
        value = model.get(key)
        if isinstance(value, bool):
            out[key] = value
        elif isinstance(value, str):
            out[key] = value[:60]
        elif isinstance(value, list):
            out[key] = [v[:60] for v in value[:8] if isinstance(v, str)]
    return out


def normalise(model: dict, version: dict, model_id: int, version_id: int, file_id: int | None) -> CivitaiVersion:
    model_type = _text(model.get("type"), 40)
    if model_type != LORA_TYPE:
        raise CivitaiError(f"This Civitai item is a {model_type or 'unknown type'}, not a LoRA; it was not used.")
    files = [f for f in (_file(i, version_id) for i in version.get("files") or [] if isinstance(i, dict)) if f]
    candidates = [f for f in files if f.name.lower().endswith(".safetensors")]
    if file_id is not None:
        candidates = [f for f in candidates if f.id == file_id]
        if not candidates:
            raise CivitaiMissing("That file id is not a .safetensors file of this version.")
    if not candidates:
        raise CivitaiError("This version has no .safetensors file; other formats are not downloaded.")
    if len(candidates) > 1:
        primary = [f for f in candidates if f.primary]
        if file_id is None or len(primary) != 1:
            options = tuple(
                {"file_id": f.id, "name": f.name, "size_bytes": f.size_bytes, "primary": f.primary} for f in candidates
            )
            raise CivitaiChoice(
                "This version has several .safetensors files: "
                + ", ".join(f"{f.id} {f.name}" for f in candidates)
                + ". Choose one with --file-id.",
                options,
            )
        candidates = primary
    words = version.get("trainedWords")
    cleaned = tuple(w[:200] for w in (words if isinstance(words, list) else []) if isinstance(w, str) and w.strip())[
        :64
    ]
    state = "missing" if not isinstance(words, list) else ("populated" if cleaned else "empty")
    creator = model.get("creator")
    tags = tuple(t[:60] for t in (model.get("tags") or []) if isinstance(t, str))[:32]
    return CivitaiVersion(
        provider="civitai",
        model_id=model_id,
        version_id=version_id,
        model_name=_text(model.get("name")) or "",
        version_name=_text(version.get("name")) or "",
        creator=_text(creator.get("username"), 80) if isinstance(creator, dict) else None,
        base_model=_text(version.get("baseModel"), 80),
        model_type=model_type,
        permissions=_permissions(model),
        tags=tags,
        trained_words=cleaned,
        trained_words_state=state,
        availability=_text(version.get("availability"), 40),
        file=candidates[0],
    )


def resolve(
    ref: CivitaiRef, token: str | None = None, *, file_id: int | None = None, client: httpx.Client | None = None
) -> CivitaiVersion:
    """Resolve a link or id to one concrete version and primary file. Several versions or files raise
    ``CivitaiChoice`` instead of picking "latest"."""
    token = _valid_token(token)
    if ref.model_id is None and ref.version_id is None:
        raise CivitaiError("Enter a version id or a Civitai link.")
    owned = client is None
    client = client or new_client()
    try:
        if ref.version_id is not None:
            version = _get_json(client, f"/model-versions/{ref.version_id}", token, ref.version_id)
            model_id = _int(version.get("modelId"))
            if model_id is None or (ref.model_id is not None and ref.model_id != model_id):
                raise CivitaiError("Civitai's version does not belong to the requested model; it was not used.")
            model = _get_json(client, f"/models/{model_id}", token, model_id)
            listed = [v.get("id") for v in model.get("modelVersions") or [] if isinstance(v, dict)]
            if ref.version_id not in listed:
                raise CivitaiError("Civitai's model does not list the requested version; it was not used.")
            return normalise(model, version, model_id, ref.version_id, file_id)
        model = _get_json(client, f"/models/{ref.model_id}", token, ref.model_id)
        versions = [
            v for v in model.get("modelVersions") or [] if isinstance(v, dict) and _int(v.get("id")) is not None
        ]
        if not versions:
            raise CivitaiMissing("Civitai lists no versions for this model.")
        if len(versions) > 1:
            options = tuple(
                {"version_id": v["id"], "name": _text(v.get("name")), "base_model": _text(v.get("baseModel"), 80)}
                for v in versions
            )
            raise CivitaiChoice(
                "This model has several versions: "
                + ", ".join(f"{o['version_id']} {o['name'] or ''}".strip() for o in options)
                + ". Open the version you want (a link with ?modelVersionId= or a version id).",
                options,
            )
        return normalise(model, versions[0], ref.model_id, versions[0]["id"], file_id)
    finally:
        if owned:
            client.close()


def check_connection(token: str | None, *, client: httpx.Client | None = None) -> tuple[str, str]:
    """One cheap authenticated request: is Civitai reachable and does it accept the token?

    Returns (state, plain text) with state ``ok``, ``error`` (token rejected), ``warning`` (network or other failure) or
    ``unknown`` (no token). Uses ``GET /api/v1/models?favorites=true&limit=1``: the developer pages document that
    ``favorites`` needs a token, and without a valid token Civitai answers 401 (observed 2026-11-15); the answer to a
    valid token is untested. The token is never part of the returned text."""
    if not token:
        return "unknown", "No token saved"
    try:
        token = _valid_token(token)
    except CivitaiError:
        return "error", "Token rejected: it does not look like a Civitai token"
    owned = client is None
    client = client or new_client()
    try:
        for attempt in (0, 1):
            try:
                url = f"{API_ROOT}/models?favorites=true&limit=1"
                with ExitStack() as stack:
                    code = _open(stack, client, url, token, same_host_only=True).status_code
                break
            except TRANSIENT:
                if attempt:
                    return "warning", "Cannot reach Civitai (network error)"
            except (httpx.HTTPError, CivitaiError):
                return "warning", "Cannot reach Civitai (network error)"
    finally:
        if owned:
            client.close()
    if code == 200:
        return "ok", "Connected, token accepted"
    if code in (401, 403):
        return "error", f"Token rejected by Civitai ({code})"
    return "warning", f"Civitai answered HTTP {code}"


# ---------------------------------------------------------------------------------------------------------- storage


def sources_dir(state: Path) -> Path:
    return Path(state) / SOURCES_DIR


def _ensure_plain_dir(path: Path) -> None:
    for part in (path, *path.parents):
        if part.exists() or part.is_symlink():
            info = part.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise CivitaiError("The downloads folder is redirected; access refused.")
    path.mkdir(parents=True, exist_ok=True)


def display_filename(name: str) -> str:
    """Sanitise a server-supplied filename for display; the stored name is always the SHA-256."""
    base = re.split(r"[\\/]", name or "")[-1]
    base = _UNSAFE.sub("_", base).strip(" .")[:120]
    return base if base.lower().endswith(".safetensors") and len(base) > len(".safetensors") else "lora.safetensors"


def cleanup_staging(state: Path) -> int:
    """Best-effort startup sweep of orphan ``.part`` files not written to for an hour; never follows links and never
    touches a file an in-process download owns. Returns the number removed."""
    folder = sources_dir(state) / STAGING_DIR
    removed = 0
    try:
        if not folder.is_dir() or folder.is_symlink():
            return 0
        with os.scandir(folder) as entries:
            for entry in entries:
                path = Path(entry.path)
                with _ACTIVE_LOCK:
                    if path in _ACTIVE_STAGING:
                        continue
                try:
                    info = entry.stat(follow_symlinks=False)
                    if (
                        entry.name.endswith(".part")
                        and stat.S_ISREG(info.st_mode)
                        and time.time() - info.st_mtime > STALE_STAGING_SECONDS
                    ):
                        path.unlink()
                        removed += 1
                except OSError:
                    continue
    except OSError:
        pass
    diagnostic_event(log, "civitai.staging_cleanup", data={"removed": removed})
    return removed


def _stored(directory: Path, sha256: str, expected: int | None) -> Path | None:
    target = directory / f"{sha256}.safetensors"
    try:
        info = target.lstat()
    except OSError:
        return None
    if not stat.S_ISREG(info.st_mode):
        raise CivitaiError("A stored LoRA name is taken by something that is not a file; nothing was replaced.")
    if expected is not None and abs(info.st_size - expected) > expected * SIZE_TOLERANCE + 4096:
        raise CivitaiError("A stored file already uses this checksum name with a different size; nothing was replaced.")
    return target


# --------------------------------------------------------------------------------------------------------- download


def _stream(
    client: httpx.Client,
    file: CivitaiFile,
    token: str | None,
    staging: Path,
    limit: int,
    on_progress: Callable[[DownloadProgress], None] | None,
    cancel: threading.Event | None,
) -> tuple[str, int]:
    """One transfer attempt into ``staging``; returns (measured sha256, size)."""
    with ExitStack() as stack:
        response = _open(stack, client, file.download_url, token, same_host_only=False)
        if response.status_code != 200:
            raise _failure(response, token)
        if "html" in response.headers.get("content-type", "").lower():
            raise CivitaiError("Civitai answered with a web page instead of a file; nothing was downloaded.")
        length = response.headers.get("content-length")
        total = int(length) if length and length.isdigit() else None
        if total is not None and total > limit:
            raise CivitaiError("The file is larger than the allowed size; it was not downloaded.")
        needed = (total or file.size_bytes or 0) + FREE_SPACE_MARGIN
        if shutil.disk_usage(staging.parent).free < needed:
            raise CivitaiError("Not enough free disk space for this download; free some space and retry.")
        digest = hashlib.sha256()
        done = 0
        head = b""
        if on_progress:
            on_progress(DownloadProgress(0, total))
        with staging.open("xb") as handle:
            for chunk in response.iter_bytes(CHUNK):
                if cancel is not None and cancel.is_set():
                    raise CivitaiCancelled("Download cancelled; the partial file was removed.")
                done += len(chunk)
                if done > limit:
                    raise CivitaiError("The file is larger than the allowed size; the download was stopped.")
                if len(head) < 8 + 1:
                    head += chunk[: 9 - len(head)]
                    if len(head) >= 9:
                        _check_header(head)
                digest.update(chunk)
                handle.write(chunk)
                if on_progress:
                    on_progress(DownloadProgress(done, total))
            handle.flush()
            os.fsync(handle.fileno())
    if len(head) < 9:
        raise CivitaiError("The downloaded file is too small to be a LoRA; it was rejected.")
    if total is not None and done != total:
        raise CivitaiError("The download ended early; the partial file was removed. Retry.")
    return digest.hexdigest(), done


def _check_header(head: bytes) -> None:
    """A safetensors file starts with a little-endian JSON header length then ``{``; anything else (an HTML or JSON
    error body, a pickle) is rejected."""
    length = int.from_bytes(head[:8], "little")
    if head[8:9] != b"{" or length < 2 or length > MAX_HEADER_BYTES:
        raise CivitaiError("The downloaded data is not a .safetensors file (it may be an error page); it was rejected.")


def supported_family(version: CivitaiVersion):
    """The converter family of the version's base-model label, or ``CivitaiUnsupportedFamily``.

    The one gate for every caller (CLI, Downloads view, queue): it needs no network and runs before any bytes are
    fetched. The label comes from Civitai's own ``baseModel``; nothing is guessed from filenames or tags."""
    from dtflow.services.lora_convert.registry import family_for_label

    spec = family_for_label(version.provider, version.base_model)
    if spec is None:
        label = f'"{version.base_model}"' if version.base_model else "no base model"
        raise CivitaiUnsupportedFamily(
            f"Family not supported yet: Civitai lists this LoRA with {label}, which DTFlow cannot convert. "
            "Nothing was downloaded."
        )
    return spec


def download(
    version: CivitaiVersion,
    state: Path,
    token: str | None = None,
    *,
    on_progress: Callable[[DownloadProgress], None] | None = None,
    cancel: threading.Event | None = None,
    max_bytes: int = MAX_DOWNLOAD_BYTES,
    client: httpx.Client | None = None,
) -> DownloadedFile:
    """Download the resolved file. Publishes only after complete streaming, size and header checks and a measured
    SHA-256 that matches the published one; on any failure the staging file is removed. One automatic retry, for
    connectivity/timeout failures only. A file already stored under the published hash is reused untouched."""
    supported_family(version)  # before any file or network access
    token = _valid_token(token)
    file = version.file
    directory = sources_dir(state)
    _ensure_plain_dir(directory)
    name = display_filename(file.name)
    if file.sha256:
        existing = _stored(directory, file.sha256, file.size_bytes)
        if existing is not None:
            diagnostic_event(log, "civitai.download.reused", data={"version_id": version.version_id})
            return DownloadedFile(existing, file.sha256, existing.stat().st_size, name, True, True)
    staging_dir = directory / STAGING_DIR
    _ensure_plain_dir(staging_dir)
    staging = staging_dir / f"dl-{uuid.uuid4().hex}.part"
    with _ACTIVE_LOCK:
        _ACTIVE_STAGING.add(staging)
    owned = client is None
    client = client or new_client()
    diagnostic_event(log, "civitai.download.started", data={"version_id": version.version_id})
    try:
        for attempt in (0, 1):
            try:
                digest, size = _stream(client, file, token, staging, max_bytes, on_progress, cancel)
                break
            except TRANSIENT as exc:
                staging.unlink(missing_ok=True)
                if attempt:
                    raise _transient(exc) from exc
            except httpx.HTTPError as exc:
                raise _transient(exc) from exc
        if file.size_bytes and abs(size - file.size_bytes) > file.size_bytes * SIZE_TOLERANCE + 4096:
            raise CivitaiError("The downloaded size does not match Civitai's listed size; the file was rejected.")
        if file.sha256 and digest != file.sha256:
            raise CivitaiError("The downloaded file does not match Civitai's published SHA-256; it was rejected.")
        target = directory / f"{digest}.safetensors"
        if _stored(directory, digest, size) is not None:
            staging.unlink(missing_ok=True)  # same bytes already stored: deduplicate
            return DownloadedFile(target, digest, size, name, True, bool(file.sha256))
        os.replace(staging, target)  # adopt the staged file in place: atomic, written once
        diagnostic_event(log, "civitai.download.finished", data={"version_id": version.version_id, "bytes": size})
        return DownloadedFile(target, digest, size, name, False, bool(file.sha256))
    except OSError as exc:
        raise CivitaiError(
            "The download could not be written to DTFlow's data folder; check space and permissions."
        ) from exc
    except BaseException as exc:
        diagnostic_event(
            log,
            "civitai.download.failed",
            level=logging.WARNING,
            data={"version_id": version.version_id, "reason": type(exc).__name__},
        )
        raise
    finally:
        staging.unlink(missing_ok=True)
        with _ACTIVE_LOCK:
            _ACTIVE_STAGING.discard(staging)
        if owned:
            client.close()


# ------------------------------------------------------------------------------------------------------ concurrency


@dataclass
class DownloadHandle:
    key: tuple
    future: Future
    cancel: threading.Event = field(default_factory=threading.Event)


class DownloadManager:
    """At most ``MAX_CONCURRENT`` transfers; requests for one source share one transfer; ``shutdown`` cancels all."""

    def __init__(self, state: Path, *, max_workers: int = MAX_CONCURRENT):
        self._state = Path(state)
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="civitai-download")
        self._lock = threading.Lock()
        self._handles: dict[tuple, DownloadHandle] = {}
        self._running = 0

    def submit(
        self,
        version: CivitaiVersion,
        token: str | None,
        on_progress: Callable[[DownloadProgress], None] | None = None,
    ) -> DownloadHandle:
        supported_family(version)
        key = (version.version_id, version.file.id)
        with self._lock:
            current = self._handles.get(key)
            if current is not None and not current.future.done():
                return current
            handle = DownloadHandle(key, Future())
            self._handles[key] = handle
        self._pool.submit(self._run, handle, version, token, on_progress)
        return handle

    def _run(self, handle: DownloadHandle, version, token, on_progress) -> None:
        if not handle.future.set_running_or_notify_cancel():
            return
        with self._lock:
            self._running += 1
        try:
            if handle.cancel.is_set():
                raise CivitaiCancelled("Download cancelled before it started.")
            handle.future.set_result(
                download(version, self._state, token, on_progress=on_progress, cancel=handle.cancel)
            )
        except BaseException as exc:
            handle.future.set_exception(exc)
        finally:
            with self._lock:
                self._running -= 1
                if self._handles.get(handle.key) is handle:
                    del self._handles[handle.key]

    def counts(self) -> tuple[int, int]:
        """(running, waiting), for one aggregate line such as "Downloading 2 of 3"."""
        with self._lock:
            return self._running, max(len(self._handles) - self._running, 0)

    def shutdown(self) -> None:
        with self._lock:
            handles = list(self._handles.values())
        for handle in handles:
            handle.cancel.set()
            handle.future.cancel()
        self._pool.shutdown(wait=True, cancel_futures=True)
