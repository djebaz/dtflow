"""DT+ LoRA storage through the verified BYOM endpoint: metadata lookup, negotiation and the signed storage PUT.

Signed URLs are temporary credentials. They are parsed, held and used only here, behind an opaque ticket; they never
reach logs, exceptions, receipts or any value that crosses the backend boundary.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import logging
import math
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from dtflow.backends.drawthings.cloud_auth import CloudAuth
from dtflow.backends.drawthings.errors import api_error, diagnostics
from dtflow.diagnostics import api_error_data, asset_ref_data, event
from dtflow.core.models import (
    AssetContent,
    AssetNegotiation,
    ErrorCategory,
    FileIdentity,
    LoRABackendAvailability,
    RemoteOutcome,
    SubmissionStage,
    ValidationError,
)


class _RedactQueries(logging.Filter):
    """httpx logs full request URLs; a signed URL's query string must never reach any log sink."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if "?" in message:
            record.msg, record.args = re.sub(r"\?\S*", "?<redacted>", message), ()
        return True


for _name in ("httpx", "httpcore"):
    logging.getLogger(_name).setLevel(logging.WARNING)
logging.getLogger("httpx").addFilter(_RedactQueries())


async def lookup_lora_assets(
    auth: CloudAuth, identities: tuple[FileIdentity, ...]
) -> dict[str, LoRABackendAvailability]:
    """Resolve known converted LoRA fingerprints without uploading bytes."""
    usable = tuple(item for item in identities if item.exists and item.sha256 is not None)
    if not usable:
        return {}
    response = await auth.authenticated_json(
        "/get_byom_urls",
        {"files": [{"file": item.identifier, "sha256": item.sha256} for item in usable]},
    )

    already, urls = _response_branches(response)

    results: dict[str, LoRABackendAvailability] = {}
    for item in usable:
        reference = already.get(item.sha256)
        if item.identifier in urls:
            _observe_upload(urls[item.identifier], item.sha256)
        if _valid_reference(reference):
            results[item.identifier] = LoRABackendAvailability(
                "byom_reusable", "dt_cloud.get_byom_urls", sha256=item.sha256, reference=reference
            )
        elif item.identifier in urls:
            upload = urls[item.identifier]
            results[item.identifier] = LoRABackendAvailability(
                "upload_required" if _usable_upload(upload) else "unknown",
                "dt_cloud.get_byom_urls",
                sha256=item.sha256,
            )
        else:
            results[item.identifier] = LoRABackendAvailability("unknown", "dt_cloud.get_byom_urls", sha256=item.sha256)
    event(
        logging.getLogger(__name__),
        "backend.asset_lookup_summary",
        level=logging.DEBUG,
        data={
            "requested": len(usable),
            **{
                name: sum(item.availability == name for item in results.values())
                for name in ("byom_reusable", "upload_required", "unknown")
            },
        },
    )
    return results


@dataclass(frozen=True, slots=True)
class UploadTicket:
    """Adapter-private handle for one signed destination; its text form never shows the URL."""

    url: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class AssetUploadPolicy:
    connect: float = 10.0
    write: float = 60.0
    read: float = 60.0
    total: float = 3600.0
    cleanup: float = 5.0
    chunk: int = 1024 * 1024
    response_bytes: int = 64 * 1024

    def __post_init__(self):
        if any(not math.isfinite(x) or x <= 0 for x in (self.connect, self.write, self.read, self.total, self.cleanup)):
            raise ValidationError("Upload deadlines must be positive and finite.")
        if type(self.chunk) is not int or self.chunk <= 0 or type(self.response_bytes) is not int:
            raise ValidationError("Upload limits must be positive integers.")


def _response_branches(response) -> tuple[dict, dict]:
    """Observe before interpretation; consume known branches independently, never dump dynamic keys."""
    data = response.get("data")
    fields = {"data_type": type(data).__name__}
    if isinstance(data, dict):
        fields["data_member_count"] = len(data)
    for name in ("alreadyExists", "signedUrls"):
        branch = data.get(name) if isinstance(data, dict) else None
        fields[name + "_present"] = isinstance(data, dict) and name in data
        fields[name + "_type"] = type(branch).__name__
        if isinstance(branch, (dict, list)):
            fields[name + "_count"] = len(branch)
    signed = data.get("signedUrls") if isinstance(data, dict) else None
    urls = signed.get("urls") if isinstance(signed, dict) else None
    fields["upload_entries_type"] = type(urls).__name__
    if isinstance(urls, (dict, list)):
        fields["upload_entries_count"] = len(urls)
    event(
        logging.getLogger(__name__),
        "backend.byom_response",
        level=logging.INFO,
        data={
            **getattr(response, "error_data", {}),
            **fields,
            "http_status": 200,
            "operation": "get_byom_urls",
            "transport": "http",
        },
    )
    already = data.get("alreadyExists") if isinstance(data, dict) else None
    return already if isinstance(already, dict) else {}, urls if isinstance(urls, dict) else {}


_EXTENSIONS = frozenset({".ckpt", ".safetensors"})


def _type_counts(values, limit: int = 4) -> str:
    """Bounded ``type:count`` summary of branch values; dynamic keys and values never appear."""
    counts: dict[str, int] = {}
    for value in values:
        name = type(value).__name__
        counts[name] = counts.get(name, 0) + 1
    return ",".join(f"{name}:{counts[name]}" for name in sorted(counts)[:limit])


def _match_shape(response, already: dict, urls: dict, content: AssetContent) -> dict:
    """Allowlisted matching evidence for one request: counts, formats and booleans, never keys, hashes or URLs."""
    data = response.get("data")
    branches = data if isinstance(data, dict) else {}
    raw_already = branches.get("alreadyExists")
    signed = branches.get("signedUrls")
    raw_urls = signed.get("urls") if isinstance(signed, dict) else None
    digest = content.sha256.lower()
    identifier = content.identifier
    suffix = Path(identifier).suffix.lower()
    already_keys = [key for key in already if isinstance(key, str)]
    url_keys = [key for key in urls if isinstance(key, str)]
    shape = {
        "request_identifier_length": len(identifier),
        "request_identifier_extension": suffix if suffix in _EXTENSIONS else "other",
        "already_branch_is_dict": isinstance(raw_already, dict),
        "already_branch_is_list": isinstance(raw_already, list),
        "already_key_hex64_count": sum(bool(re.fullmatch(r"[0-9a-fA-F]{64}", key)) for key in already_keys),
        "already_key_exact_match": content.sha256 in already,
        "already_key_casefold_match": any(key.lower() == digest for key in already_keys),
        "already_value_types": _type_counts(already.values()),
        "url_branch_is_dict": isinstance(raw_urls, dict),
        "url_branch_is_list": isinstance(raw_urls, list),
        "url_key_exact_match": identifier in urls,
        "url_key_casefold_match": any(key.casefold() == identifier.casefold() for key in url_keys),
        "url_key_same_extension_count": sum(Path(key).suffix.lower() == suffix for key in url_keys),
        "url_key_same_length_count": sum(len(key) == len(identifier) for key in url_keys),
        "url_value_types": _type_counts(urls.values()),
    }
    if isinstance(raw_already, list):
        shape["already_list_item_types"] = _type_counts(raw_already)
    if isinstance(raw_urls, list):
        shape["url_list_item_types"] = _type_counts(raw_urls)
    return shape


def _valid_reference(reference) -> bool:
    try:
        AssetNegotiation("exists", reference)
    except ValidationError:
        return False
    return True


def _observe_upload(upload, digest: str) -> None:
    """Non-identifying evidence only: the server field is not an echoed local checksum contract."""
    returned = upload.get("sha256") if isinstance(upload, dict) else None
    evidence = {
        "operation": "get_byom_urls",
        "upload_entry_type": type(upload).__name__,
        "server_checksum_type": type(returned).__name__,
        "local_checksum_format": "hex_sha256",
        "local_checksum_length": len(digest),
        "comparison_required": False,
    }
    if isinstance(returned, str):
        text = returned.strip()
        encoding = "opaque"
        matches = False
        if re.fullmatch(r"[0-9a-fA-F]{64}", text):
            encoding = "hex_sha256"
            matches = text.lower() == digest
        elif len(text) == 44:
            try:
                decoded = base64.b64decode(text, validate=True)
            except (ValueError, binascii.Error):
                decoded = b""
            if len(decoded) == 32:
                encoding = "base64_sha256"
                matches = decoded.hex() == digest
        evidence.update(
            server_checksum_length=len(returned),
            server_checksum_format=encoding,
            server_checksum_text_matches_local=text.lower() == digest,
            server_reference_usable=_valid_reference(returned),
        )
        if encoding != "opaque":
            evidence["server_checksum_decoded_matches_local"] = matches
    event(
        logging.getLogger(__name__),
        "backend.byom_upload_evidence",
        level=logging.INFO,
        data={**asset_ref_data(sha256=digest), **evidence},
    )


def _usable_upload(upload) -> bool:
    # Official LoRAStore forwards this field as serverSha256; it need not equal the request's local digest.
    return isinstance(upload, dict) and _valid_reference(upload.get("sha256")) and _safe_destination(upload.get("url"))


def _safe_destination(value) -> bool:
    try:
        url = httpx.URL(value) if isinstance(value, str) and value.strip() else None
        return bool(url and url.scheme == "https" and url.host and not url.userinfo and not url.fragment)
    except (httpx.InvalidURL, ValueError):
        return False


def _invalid(
    code: str = "cloud_byom_response_invalid", *, response=None, reason="no_usable_asset_evidence", shape=None
):
    return api_error(
        ErrorCategory.PROTOCOL,
        code,
        f"DTFlow could not establish the asset status from the DT+ response ({reason}).",
        operation="/get_byom_urls",
        transport="http",
        response=getattr(response, "error_data", None),
        diagnostics=diagnostics(http_status=200, validation_reason=reason, **(shape or {})),
    )


def _signed_destination(value: object, *, response=None) -> UploadTicket:
    if not _safe_destination(value):
        raise _invalid(response=response, reason="unsafe_or_missing_upload_destination")
    return UploadTicket(str(httpx.URL(value)))


async def negotiate_lora_assets(
    auth: CloudAuth, contents: tuple[AssetContent, ...]
) -> dict[tuple[str, str, int], AssetNegotiation]:
    """Resolve distinct custom LoRAs in one authenticated BYOM request, without uploading."""
    if not contents:
        return {}
    response = await auth.authenticated_json(
        "/get_byom_urls",
        {"files": [{"file": c.identifier, "sha256": c.sha256, "size": c.size} for c in contents]},
    )
    already, urls = _response_branches(response)
    results = {}
    for content in contents:
        reference = already.get(content.sha256)
        if _valid_reference(reference):
            result = AssetNegotiation("exists", reference)
        else:
            upload = urls.get(content.identifier)
            if not _usable_upload(upload):
                raise _invalid(response=response, reason="missing_or_invalid_batched_asset_evidence")
            result = AssetNegotiation(
                "upload_required", ticket=_signed_destination(upload.get("url"), response=response)
            )
        results[(content.identifier, content.sha256, content.size)] = result
    event(
        logging.getLogger(__name__),
        "backend.assets_negotiated",
        level=logging.DEBUG,
        data={
            "requested": len(contents),
            "exists": sum(x.status == "exists" for x in results.values()),
            "upload_required": sum(x.status == "upload_required" for x in results.values()),
        },
    )
    return results


async def negotiate_lora_asset(auth: CloudAuth, content: AssetContent) -> AssetNegotiation:
    """Ask DT+ for the current storage state of one content identity; transfers nothing.

    Consume usable known evidence independently. Prefer a verified existing reference over an unused upload branch;
    never upload to an unverified destination or invent a missing reference.
    """
    response = await auth.authenticated_json(
        "/get_byom_urls", {"files": [{"file": content.identifier, "sha256": content.sha256, "size": content.size}]}
    )
    already, urls = _response_branches(response)
    reference = already.get(content.sha256)
    upload = urls.get(content.identifier)
    if content.identifier in urls:
        _observe_upload(upload, content.sha256)
    if _valid_reference(reference):
        result = AssetNegotiation("exists", reference)
        event(
            logging.getLogger(__name__),
            "backend.asset_negotiated",
            level=logging.DEBUG,
            data={**asset_ref_data(sha256=content.sha256), "outcome": "exists"},
        )
        return result
    if not _usable_upload(upload):
        reason = "no_matching_asset_entry"
        if not isinstance(response.get("data"), dict):
            reason = "data_not_object"
        elif content.identifier in urls:
            reason = "invalid_upload_entry"
            if isinstance(upload, dict):
                reason = (
                    "unsafe_or_missing_upload_destination"
                    if _valid_reference(upload.get("sha256"))
                    else "invalid_server_reference"
                )
        elif content.sha256 in already:
            reason = "unusable_existing_reference"
        shape = _match_shape(response, already, urls, content)
        event(
            logging.getLogger(__name__),
            "backend.byom_match_failed",
            level=logging.INFO,
            data={**asset_ref_data(sha256=content.sha256), "validation_reason": reason, **shape},
        )
        raise _invalid(response=response, reason=reason, shape=shape)
    result = AssetNegotiation("upload_required", ticket=_signed_destination(upload.get("url"), response=response))
    event(
        logging.getLogger(__name__),
        "backend.asset_negotiated",
        level=logging.DEBUG,
        data={**asset_ref_data(sha256=content.sha256), "outcome": "upload_required"},
    )
    return result


def _transfer_error(
    code: str, outcome: RemoteOutcome, category=ErrorCategory.CONNECTIVITY, *, response=None, elapsed_ms=None, **items
):
    stage = (
        SubmissionStage.NOT_SUBMITTED if outcome == RemoteOutcome.NOT_STARTED else SubmissionStage.POSSIBLY_SUBMITTED
    )
    return api_error(
        category,
        code,
        "DT+ LoRA storage transfer did not complete.",
        stage=stage,
        outcome=outcome,
        diagnostics=diagnostics(**items),
        operation="asset_storage_put",
        transport="http",
        response=response,
        elapsed_ms=elapsed_ms,
    )


async def upload_signed(
    ticket: UploadTicket,
    path: Path,
    content: AssetContent,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    policy: AssetUploadPolicy | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> None:
    """Stream the file once to the signed destination with no DT+ authorization and bounded memory.

    ``remote_outcome`` on failures is the only evidence the service uses: ``NOT_STARTED`` means no bytes were sent,
    ``FAILED`` a definite storage refusal, ``UNKNOWN`` anything that may have stored data. The file is re-hashed while
    it streams; a changed file aborts the transfer and is reported as unknown because bytes were already sent.
    """
    policy = policy or AssetUploadPolicy()
    started = False
    yielded = 0
    began = time.monotonic()

    def elapsed():
        return (time.monotonic() - began) * 1000

    async def body():
        nonlocal started, yielded
        digest, sent = hashlib.sha256(), 0
        handle = await asyncio.to_thread(path.open, "rb")
        try:
            while chunk := await asyncio.to_thread(handle.read, policy.chunk):
                started = True
                digest.update(chunk)
                sent += len(chunk)
                if sent > content.size:
                    raise _transfer_error("asset_changed", RemoteOutcome.UNKNOWN, ErrorCategory.VALIDATION)
                yielded += len(chunk)
                if on_progress is not None:
                    on_progress(yielded, content.size)  # bytes handed to the HTTP client, not server-acknowledged
                yield chunk
            if sent != content.size or digest.hexdigest() != content.sha256:
                raise _transfer_error("asset_changed", RemoteOutcome.UNKNOWN, ErrorCategory.VALIDATION)
        finally:
            await asyncio.to_thread(handle.close)

    headers = {"Content-Type": "application/octet-stream", "Content-Length": str(content.size)}
    client = httpx.AsyncClient(
        transport=transport,
        trust_env=False,
        follow_redirects=False,
        timeout=httpx.Timeout(policy.read, connect=policy.connect, write=policy.write, pool=policy.connect),
    )
    try:
        destination = httpx.URL(ticket.url)
        # A storage error may echo an individual signature instead of the full signed URL.
        query_private = tuple(value for _, value in destination.params.multi_items()) + tuple(
            part.partition(b"=")[2].decode("ascii") for part in destination.query.split(b"&") if b"=" in part
        )
        async with asyncio.timeout(policy.total):
            async with client.stream("PUT", ticket.url, content=body(), headers=headers) as response:
                status = response.status_code
                size = 0
                error_body = bytearray()
                truncated = False
                try:
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if not 200 <= status < 300:
                            remaining = 8192 - len(error_body)
                            error_body.extend(chunk[:remaining])
                            if len(chunk) > remaining:
                                truncated = True
                                break
                        if size > policy.response_bytes:
                            break
                except (httpx.HTTPError, TimeoutError):
                    if 200 <= status < 300:
                        raise
                    truncated = True
                evidence = api_error_data(
                    body=bytes(error_body) if not 200 <= status < 300 else None,
                    headers=response.headers,
                    private=(
                        ticket.url,
                        content.identifier,
                        content.sha256,
                        *query_private,
                    ),
                    truncated=truncated,
                )
        if 200 <= status < 300:
            event(
                logging.getLogger(__name__),
                "backend.api_completed",
                level=logging.DEBUG,
                data={
                    "transport": "http",
                    "operation": "asset_storage_put",
                    **asset_ref_data(sha256=content.sha256),
                    "http_status": status,
                    "bytes_yielded": yielded,
                    "elapsed_ms": elapsed(),
                },
            )
            return
        if 400 <= status < 500:
            raise _transfer_error(
                "asset_storage_refused",
                RemoteOutcome.FAILED,
                ErrorCategory.REMOTE_REJECTION,
                http_status=status,
                response=evidence,
                elapsed_ms=elapsed(),
                bytes_yielded=yielded,
            )
        raise _transfer_error(
            "asset_storage_error",
            RemoteOutcome.UNKNOWN,
            ErrorCategory.REMOTE_REJECTION,
            http_status=status,
            response=evidence,
            elapsed_ms=elapsed(),
            bytes_yielded=yielded,
        )
    except (httpx.ConnectError, httpx.ConnectTimeout):
        outcome = RemoteOutcome.UNKNOWN if started else RemoteOutcome.NOT_STARTED
        raise _transfer_error(
            "asset_storage_unreachable", outcome, elapsed_ms=elapsed(), bytes_yielded=yielded
        ) from None
    except (TimeoutError, httpx.TimeoutException):
        raise _transfer_error(
            "asset_transfer_deadline",
            RemoteOutcome.UNKNOWN,
            ErrorCategory.TIMEOUT,
            elapsed_ms=elapsed(),
            bytes_yielded=yielded,
        ) from None
    except httpx.HTTPError as exc:
        raise _transfer_error(
            "asset_transfer_failed",
            RemoteOutcome.UNKNOWN,
            elapsed_ms=elapsed(),
            kind=type(exc).__name__,
            bytes_yielded=yielded,
        ) from None
    finally:
        try:
            async with asyncio.timeout(policy.cleanup):
                await client.aclose()
        except TimeoutError:
            pass
