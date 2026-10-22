"""DT+ LoRA storage through the verified BYOM endpoint: metadata lookup, negotiation and the signed storage PUT.

Signed URLs are temporary credentials. They are parsed, held and used only here, behind an opaque ticket; they never
reach logs, exceptions, receipts or any value that crosses the backend boundary.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from dtflow.backends.drawthings.cloud_auth import CloudAuth
from dtflow.backends.drawthings.errors import api_error, diagnostics
from dtflow.diagnostics import api_error_data, event
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

    def invalid():
        return _invalid(response=response)

    data = response.get("data")
    if not isinstance(data, dict):
        raise invalid()

    already = data.get("alreadyExists", {})
    signed = data.get("signedUrls", {})
    # Optional response branches may be absent or null when the other branch supplies the evidence.
    if already is None:
        already = {}
    if signed is None:
        signed = {}
    if not isinstance(already, dict) or not isinstance(signed, dict):
        raise invalid()
    urls = signed.get("urls", {})
    if not isinstance(urls, dict):
        raise invalid()

    results: dict[str, LoRABackendAvailability] = {}
    for item in usable:
        reference = already.get(item.sha256)
        if item.sha256 in already and (not isinstance(reference, str) or not reference.strip()):
            raise invalid()
        if isinstance(reference, str) and reference:
            results[item.identifier] = LoRABackendAvailability(
                "byom_reusable", "dt_cloud.get_byom_urls", sha256=item.sha256, reference=reference
            )
        elif item.identifier in urls:
            upload = urls[item.identifier]
            if (
                not isinstance(upload, dict)
                or not isinstance(upload.get("sha256"), str)
                or upload["sha256"].lower() != item.sha256
                or not isinstance(upload.get("url"), str)
                or not upload["url"].strip()
            ):
                raise invalid()
            results[item.identifier] = LoRABackendAvailability(
                "upload_required", "dt_cloud.get_byom_urls", sha256=item.sha256
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


def _invalid(code: str = "cloud_byom_response_invalid", *, response=None):
    return api_error(
        ErrorCategory.PROTOCOL,
        code,
        "DT+ LoRA storage response is invalid.",
        operation="/get_byom_urls",
        transport="http",
        response=getattr(response, "error_data", None),
        diagnostics=diagnostics(http_status=200),
    )


def _signed_destination(value: object, *, response=None) -> UploadTicket:
    url = httpx.URL(value) if isinstance(value, str) and value.strip() else None
    if url is None or url.scheme != "https" or not url.host or url.userinfo or url.fragment:
        raise _invalid(response=response)
    return UploadTicket(str(url))


async def negotiate_lora_asset(auth: CloudAuth, content: AssetContent) -> AssetNegotiation:
    """Ask DT+ for the current storage state of one content identity; transfers nothing.

    Only the two verified branches are accepted, and only when consistent with the measured checksum. A response that
    supplies both branches, neither, or a mismatched identity is a sanitized protocol failure.
    """
    response = await auth.authenticated_json(
        "/get_byom_urls", {"files": [{"file": content.identifier, "sha256": content.sha256, "size": content.size}]}
    )
    data = response.get("data")
    if not isinstance(data, dict):
        raise _invalid(response=response)
    already = data.get("alreadyExists") or {}
    signed = data.get("signedUrls") or {}
    if not isinstance(already, dict) or not isinstance(signed, dict):
        raise _invalid(response=response)
    urls = signed.get("urls") or {}
    if not isinstance(urls, dict):
        raise _invalid(response=response)
    reference = already.get(content.sha256)
    upload = urls.get(content.identifier)
    if (content.sha256 in already) == (content.identifier in urls):
        raise _invalid(
            "cloud_byom_response_conflict" if content.sha256 in already else "cloud_byom_response_invalid",
            response=response,
        )
    if content.sha256 in already:
        try:
            result = AssetNegotiation("exists", reference if isinstance(reference, str) else None)
        except ValidationError:
            raise _invalid(response=response) from None
        event(logging.getLogger(__name__), "backend.asset_negotiated", level=logging.DEBUG, data={"outcome": "exists"})
        return result
    if (
        not isinstance(upload, dict)
        or not isinstance(upload.get("sha256"), str)
        or upload["sha256"].lower() != content.sha256
    ):
        raise _invalid(response=response)
    result = AssetNegotiation("upload_required", ticket=_signed_destination(upload.get("url"), response=response))
    event(
        logging.getLogger(__name__),
        "backend.asset_negotiated",
        level=logging.DEBUG,
        data={"outcome": "upload_required"},
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
                    private=(ticket.url, content.identifier, content.sha256),
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
