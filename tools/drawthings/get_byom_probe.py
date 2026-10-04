"""Probe DT+ cloud storage metadata for one converted checkpoint without uploading it."""

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
            "Query DT+ /get_byom_urls for one known checkpoint fingerprint. "
            "The probe never uploads file bytes."
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


def _request_json(
    url: str,
    *,
    payload: dict[str, Any],
    headers: dict[str, str] | None = None,
    operation: str,
) -> dict[str, Any]:
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
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        raise ProbeError(f"{operation} failed with HTTP {exc.code}") from exc
    except URLError as exc:
        raise ProbeError(f"{operation} failed: {exc.reason}") from exc

    if len(raw) > MAX_RESPONSE_BYTES:
        raise ProbeError(f"{operation} response exceeded {MAX_RESPONSE_BYTES} bytes")

    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProbeError(f"{operation} returned invalid JSON") from exc

    if not isinstance(decoded, dict):
        raise ProbeError(f"{operation} returned an unexpected JSON value")
    return decoded


def _short_term_token(api_base: str, api_key: str) -> str:
    response = _request_json(
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


def _classify(response: dict[str, Any], sha256: str) -> tuple[str, str | None]:
    data = response.get("data")
    if not isinstance(data, dict):
        return "unknown_response", None

    already_exists = data.get("alreadyExists")
    if isinstance(already_exists, dict):
        server_sha256 = already_exists.get(sha256)
        if isinstance(server_sha256, str):
            return "already_exists", server_sha256
        return "already_exists_unmatched", None

    signed_urls = data.get("signedUrls")
    if isinstance(signed_urls, dict):
        return "upload_required", None

    if isinstance(data.get("storageLimitExceeded"), dict):
        return "storage_limit_exceeded", None

    return "unknown_response", None


def main() -> int:
    """Run the non-uploading DT+ checkpoint metadata probe."""
    parser = build_parser()
    args = parser.parse_args()
    _validate_args(args, parser)

    api_key = os.environ.get(API_KEY_ENV, "").strip()
    if not api_key:
        raise SystemExit(f"Missing {API_KEY_ENV} environment variable.")

    try:
        token = _short_term_token(args.api_base, api_key)
        response = _request_json(
            f"{args.api_base}/get_byom_urls",
            payload={
                "files": [
                    {
                        "file": args.filename,
                        "sha256": args.sha256,
                        "size": args.size,
                    }
                ]
            },
            headers={"Authorization": token},
            operation="DT+ get_byom_urls request",
        )
    except ProbeError as exc:
        raise SystemExit(str(exc)) from exc

    result, server_sha256 = _classify(response, args.sha256)

    print(f"Filename: {args.filename}")
    print(f"Size (bytes): {args.size}")
    print(f"SHA-256: {args.sha256}")
    print(f"Result: {result}")
    if server_sha256 is not None:
        print(f"Server SHA-256: {server_sha256}")
    print("Raw sanitized response:")
    print(json.dumps(_redact_signed_urls(response), indent=2, sort_keys=True))

    if result == "upload_required":
        print("Note: the server returned an upload slot, but this probe does not upload anything.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
