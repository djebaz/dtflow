"""Probe DT+ cloud storage metadata without uploading checkpoint bytes."""

from __future__ import annotations

import argparse
import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_API_BASE = "https://api.drawthings.ai"
DEFAULT_FILENAME = (
    "the_body_version_m_zit_512_r16_adamw8bit_weighted_bf16_"
    "womanonly_000008000_lora_f16.ckpt"
)
DEFAULT_SIZE_BYTES = 86257664
DEFAULT_SHA256 = "a069707aef7a69ec9dc867b9b5bcace2f560030030ad44cf1f15979622a6aec8"
API_KEY_ENV = "DRAWTHINGS_API_KEY"
MAX_RESPONSE_BYTES = 1024 * 1024


class ProbeError(RuntimeError):
    """Raised when the DT+ metadata probe cannot complete safely."""


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser."""
    parser = argparse.ArgumentParser(
        description=(
            "Probe DT+ /get_byom_urls with complete and deliberately incomplete "
            "metadata. The probe never uploads file bytes."
        )
    )
    parser.add_argument("--filename", default=DEFAULT_FILENAME, help="Checkpoint filename.")
    parser.add_argument(
        "--size",
        type=int,
        default=DEFAULT_SIZE_BYTES,
        help=f"Exact checkpoint size in bytes (default: {DEFAULT_SIZE_BYTES}).",
    )
    parser.add_argument(
        "--sha256",
        default=DEFAULT_SHA256,
        help="Lowercase SHA-256 hex digest for the checkpoint.",
    )
    parser.add_argument(
        "--api-base",
        default=DEFAULT_API_BASE,
        help=f"DT+ API base URL (default: {DEFAULT_API_BASE}).",
    )
    return parser


def _validate_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    if not args.filename or args.filename in {".", ".."} or "/" in args.filename or "\\" in args.filename:
        parser.error("--filename must be a basename, not a path")
    if args.size <= 0:
        parser.error("--size must be a positive integer")
    digest = args.sha256.lower()
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        parser.error("--sha256 must be exactly 64 hexadecimal characters")
    args.sha256 = digest
    if not args.api_base.startswith("https://"):
        parser.error("--api-base must use https://")
    args.api_base = args.api_base.rstrip("/")


def _decode_json(raw: bytes, *, operation: str) -> dict[str, Any]:
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProbeError(f"{operation} returned invalid JSON") from exc
    if not isinstance(decoded, dict):
        raise ProbeError(f"{operation} returned an unexpected JSON value")
    return decoded


def _read_bounded(response, *, operation: str) -> bytes:
    raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ProbeError(f"{operation} response exceeded {MAX_RESPONSE_BYTES} bytes")
    return raw


def _post_json(
    url: str,
    *,
    payload: dict[str, Any],
    headers: dict[str, str] | None = None,
    operation: str,
    allow_http_error: bool = False,
) -> tuple[int, dict[str, Any]]:
    request_headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "dtflow-get-byom-probe/0.0",
    }
    if headers:
        request_headers.update(headers)

    request = Request(
        url,
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers=request_headers,
        method="POST",
    )

    try:
        with urlopen(request, timeout=30) as response:
            status = response.status
            raw = _read_bounded(response, operation=operation)
    except HTTPError as exc:
        if not allow_http_error:
            raise ProbeError(f"{operation} failed with HTTP {exc.code}") from exc
        status = exc.code
        raw = _read_bounded(exc, operation=operation)
    except URLError as exc:
        raise ProbeError(f"{operation} failed: {exc.reason}") from exc

    return status, _decode_json(raw, operation=operation)


def _short_term_token(api_base: str, api_key: str) -> str:
    _, response = _post_json(
        f"{api_base}/sdk/token",
        payload={
            "apiKey": api_key,
            "appCheckType": "none",
            "appCheckToken": None,
        },
        operation="DT+ short-term token request",
    )
    token = response.get("shortTermToken")
    if not isinstance(token, str) or not token.strip():
        raise ProbeError("DT+ short-term token response did not contain a token")
    return token


def _redact_signed_urls(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if key == "url" and isinstance(item, str):
                redacted[key] = "<redacted-signed-url>"
            else:
                redacted[key] = _redact_signed_urls(item)
        return redacted
    if isinstance(value, list):
        return [_redact_signed_urls(item) for item in value]
    return value


def _classify(response: dict[str, Any]) -> tuple[str, str | None]:
    data = response.get("data")
    if not isinstance(data, dict):
        return "unknown_response", None

    already_exists = data.get("alreadyExists")
    if isinstance(already_exists, dict):
        references = [value for value in already_exists.values() if isinstance(value, str)]
        return "already_exists", references[0] if len(references) == 1 else None

    if isinstance(data.get("signedUrls"), dict):
        return "upload_required", None

    if isinstance(data.get("storageLimitExceeded"), dict):
        return "storage_limit_exceeded", None

    return "unknown_response", None


def _cases(args: argparse.Namespace) -> list[tuple[str, dict[str, Any]]]:
    return [
        (
            "full_metadata",
            {"file": args.filename, "sha256": args.sha256, "size": args.size},
        ),
        (
            "without_size",
            {"file": args.filename, "sha256": args.sha256},
        ),
        (
            "without_sha256",
            {"file": args.filename, "size": args.size},
        ),
        (
            "filename_only",
            {"file": args.filename},
        ),
    ]


def main() -> int:
    """Run the non-uploading DT+ metadata omission matrix."""
    parser = build_parser()
    args = parser.parse_args()
    _validate_args(args, parser)

    api_key = os.environ.get(API_KEY_ENV, "").strip()
    if not api_key:
        raise SystemExit(f"Missing {API_KEY_ENV} environment variable.")

    try:
        token = _short_term_token(args.api_base, api_key)
    except ProbeError as exc:
        raise SystemExit(str(exc)) from exc

    print(f"Filename: {args.filename}")
    print(f"Known size (bytes): {args.size}")
    print(f"Known SHA-256: {args.sha256}")
    print()

    for case_name, metadata in _cases(args):
        print(f"=== {case_name} ===")
        print("Sent metadata:")
        print(json.dumps(metadata, indent=2, sort_keys=True))
        try:
            http_status, response = _post_json(
                f"{args.api_base}/get_byom_urls",
                payload={"files": [metadata]},
                headers={"Authorization": token},
                operation=f"DT+ get_byom_urls request ({case_name})",
                allow_http_error=True,
            )
        except ProbeError as exc:
            print(f"Probe error: {exc}")
            print()
            continue

        result, server_reference = _classify(response)
        print(f"HTTP status: {http_status}")
        print(f"Result: {result}")
        if server_reference is not None:
            print(f"Server reference: {server_reference}")
        print("Raw sanitized response:")
        print(json.dumps(_redact_signed_urls(response), indent=2, sort_keys=True))
        if result == "upload_required":
            print("Note: the server returned an upload slot, but this probe does not upload anything.")
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
