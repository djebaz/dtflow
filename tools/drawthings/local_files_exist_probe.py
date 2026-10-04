"""Probe a local Draw Things gRPC server for checkpoint existence and SHA-256."""

from __future__ import annotations

import argparse
import asyncio
import os
import ssl
from importlib.resources import files

from drawthings_py.generated.dt_grpc import image_service
from grpclib.client import Channel

DEFAULT_GRPC_PORT = 7859
DEFAULT_FILENAME = (
    "the_body_version_m_zit_512_r16_adamw8bit_weighted_bf16_"
    "womanonly_000008000_lora_f16.ckpt"
)
DEFAULT_KNOWN_SHA256 = "a069707aef7a69ec9dc867b9b5bcace2f560030030ad44cf1f15979622a6aec8"
SHARED_SECRET_ENV = "DRAWTHINGS_SHARED_SECRET"


class ProbeError(RuntimeError):
    """Raised when the local Draw Things file probe cannot complete safely."""


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser."""
    parser = argparse.ArgumentParser(
        description=(
            "Ask a local Draw Things gRPC server whether a converted checkpoint exists "
            "and request its SHA-256 via FilesExist(filesWithHash=...). "
            "No file bytes are downloaded and no generation is submitted."
        )
    )
    parser.add_argument(
        "-a",
        "--host",
        required=True,
        help="Draw Things gRPC server hostname or LAN IP, for example 192.168.1.42.",
    )
    parser.add_argument(
        "-p",
        "--port",
        type=int,
        default=DEFAULT_GRPC_PORT,
        help=f"Draw Things gRPC port (default: {DEFAULT_GRPC_PORT}).",
    )
    parser.add_argument(
        "--filename",
        default=DEFAULT_FILENAME,
        help="Converted checkpoint basename to query.",
    )
    parser.add_argument(
        "--known-sha256",
        default=DEFAULT_KNOWN_SHA256,
        help="Optional known SHA-256 used only for comparison with the server result.",
    )
    return parser


def _validate_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    if not args.host.strip():
        parser.error("--host must not be empty")
    if not 1 <= args.port <= 65535:
        parser.error("--port must be in 1..65535")
    if not args.filename or args.filename in {".", ".."} or "/" in args.filename or "\\" in args.filename:
        parser.error("--filename must be a basename, not a path")
    digest = args.known_sha256.lower()
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        parser.error("--known-sha256 must be exactly 64 hexadecimal characters")
    args.known_sha256 = digest


def _drawthings_tls_context() -> ssl.SSLContext:
    """Use the same bundled Draw Things CA policy as drawthings-py local gRPC."""
    cert_path = files("drawthings_py.resources").joinpath("root_ca.crt")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(cafile=str(cert_path))
    context.check_hostname = False
    context.set_alpn_protocols(["h2"])
    return context


async def _probe(args: argparse.Namespace) -> tuple[bool, str | None]:
    shared_secret = os.environ.get(SHARED_SECRET_ENV, "").strip() or None
    channel = Channel(args.host, args.port, ssl=_drawthings_tls_context())

    try:
        stub = image_service.ImageGenerationServiceStub(channel)
        request = image_service.FileListRequest(
            files=[args.filename],
            files_with_hash=[args.filename],
        )
        if shared_secret is not None:
            request.shared_secret = shared_secret

        response = await stub.files_exist(request, timeout=30)
    except Exception as exc:
        raise ProbeError(f"FilesExist failed: {type(exc).__name__}: {exc}") from exc
    finally:
        channel.close()

    try:
        index = response.files.index(args.filename)
    except ValueError as exc:
        raise ProbeError("FilesExist response did not include the requested filename") from exc

    if index >= len(response.existences):
        raise ProbeError("FilesExist response did not include an existence value")

    exists = bool(response.existences[index])
    if index >= len(response.hashes) or not response.hashes[index]:
        return exists, None

    digest = response.hashes[index]
    if len(digest) != 32:
        raise ProbeError(f"FilesExist returned an unexpected hash length: {len(digest)} bytes")

    return exists, digest.hex()


def main() -> int:
    """Run the local non-generative checkpoint hash probe."""
    parser = build_parser()
    args = parser.parse_args()
    _validate_args(args, parser)

    try:
        exists, server_sha256 = asyncio.run(_probe(args))
    except ProbeError as exc:
        raise SystemExit(str(exc)) from exc

    print(f"Target: {args.host}:{args.port}")
    print(f"Filename: {args.filename}")
    print(f"Exists: {exists}")
    print(f"Server SHA-256: {server_sha256 or '<not returned>'}")

    if server_sha256 is not None:
        print(f"Matches known diagnostic SHA-256: {server_sha256 == args.known_sha256}")

    if exists and server_sha256 is None:
        print(
            "Note: the server reported the file as present but returned no SHA-256. "
            "That would differ from the inspected local Draw Things server implementation."
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
