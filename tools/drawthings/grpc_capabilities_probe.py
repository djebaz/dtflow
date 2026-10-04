"""Probe the non-generative information surface of a local Draw Things gRPC server.

The probe calls only Echo, FilesExist, Pubkey, and Hours. It never calls
GenerateImage or UploadFile. FilesExist hashes are requested only for small
metadata-registry candidates and explicit --hash-file values.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import ssl
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path
from typing import Any

from drawthings_py.generated.dt_grpc import image_service
from grpclib.client import Channel

DEFAULT_GRPC_PORT = 7859
DEFAULT_TIMEOUT_SECONDS = 30.0
SHARED_SECRET_ENV = "DRAWTHINGS_SHARED_SECRET"
CLIENT_NAME = "dtflow-grpc-capabilities-probe"

SAFE_RPCS = ("Echo", "FilesExist", "Pubkey", "Hours")
DELIBERATELY_SKIPPED_RPCS = ("GenerateImage", "UploadFile")

# These basenames are taken from the inspected Draw Things source. FilesExist
# resolves them through the model-download path; it cannot return their bytes.
METADATA_FILE_CANDIDATES: tuple[tuple[str, str], ...] = (
    ("custom_lora.json", "local_lora_registry"),
    ("custom.json", "local_model_registry"),
    ("custom_controlnet.json", "local_control_registry"),
    ("custom_textual_inversions.json", "local_textual_inversion_registry"),
    ("custom_upscaler.json", "local_upscaler_registry"),
    ("custom_configs.json", "local_configuration_registry"),
    ("loras.json", "community_cache_basename"),
    ("loras_sha256.json", "community_checksum_cache_basename"),
)


class ProbeError(RuntimeError):
    """Raised when the capability probe cannot establish its required connection."""


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(
        description=(
            "Inspect the safe/read-only information exposed by a local Draw Things gRPC server. "
            "Calls Echo, FilesExist, Pubkey, and Hours only; never generation or upload."
        )
    )
    parser.add_argument(
        "-a",
        "--host",
        required=True,
        help="Draw Things gRPC server hostname or LAN IP, for example 192.168.1.142.",
    )
    parser.add_argument(
        "-p",
        "--port",
        type=int,
        default=DEFAULT_GRPC_PORT,
        help=f"Draw Things gRPC port (default: {DEFAULT_GRPC_PORT}).",
    )
    parser.add_argument(
        "-f",
        "--hash-file",
        action="append",
        default=[],
        metavar="FILE",
        help=(
            "Additionally request SHA-256 for this exact server-side basename. "
            "Repeat the option to hash more than one file."
        ),
    )
    parser.add_argument(
        "-o",
        "--out",
        type=Path,
        help="Optional path for the complete JSON report. Without this option, the report is printed to stdout.",
    )
    parser.add_argument(
        "-t",
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"Per-RPC timeout in seconds (default: {DEFAULT_TIMEOUT_SECONDS:g}).",
    )
    return parser


def _validate_basename(value: str, parser: argparse.ArgumentParser, *, flag: str) -> str:
    value = value.strip()
    if not value or value in {".", ".."} or "/" in value or "\\" in value:
        parser.error(f"{flag} values must be basenames, not paths")
    return value


def _validate_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    if not args.host.strip():
        parser.error("--host must not be empty")
    if not 1 <= args.port <= 65535:
        parser.error("--port must be in 1..65535")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    args.hash_file = [_validate_basename(value, parser, flag="--hash-file") for value in args.hash_file]


def _drawthings_tls_context() -> ssl.SSLContext:
    """Use the same bundled Draw Things CA policy as the existing local probe."""
    cert_path = files("drawthings_py.resources").joinpath("root_ca.crt")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(cafile=str(cert_path))
    context.check_hostname = False
    context.set_alpn_protocols(["h2"])
    return context


def _thresholds(value: image_service.ComputeUnitThreshold | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        "community": value.community,
        "plus": value.plus,
        "expire_at": value.expire_at,
    }


def _decode_override_blob(raw: bytes, *, category: str) -> dict[str, Any]:
    if not raw:
        return {
            "present": False,
            "byte_count": 0,
            "count": 0,
            "field_names": [],
            "items": [],
        }

    result: dict[str, Any] = {
        "present": True,
        "byte_count": len(raw),
    }
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        result.update(
            {
                "decode_error": f"{type(exc).__name__}: {exc}",
                "count": None,
                "field_names": [],
                "items": None,
            }
        )
        return result

    if not isinstance(value, list):
        result.update(
            {
                "decode_error": f"{category} override decoded to {type(value).__name__}, expected list",
                "count": None,
                "field_names": [],
                "items": value,
            }
        )
        return result

    field_names = sorted({str(key) for item in value if isinstance(item, dict) for key in item})
    result.update(
        {
            "count": len(value),
            "field_names": field_names,
            "items": value,
        }
    )
    return result


def decode_metadata_override(
    override: image_service.MetadataOverride | None,
) -> dict[str, dict[str, Any]]:
    """Decode every Echo metadata category without reducing its fields."""
    if override is None:
        return {
            name: _decode_override_blob(b"", category=name)
            for name in ("models", "loras", "control_nets", "textual_inversions", "upscalers")
        }

    return {
        "models": _decode_override_blob(override.models, category="models"),
        "loras": _decode_override_blob(override.loras, category="loras"),
        "control_nets": _decode_override_blob(override.control_nets, category="control_nets"),
        "textual_inversions": _decode_override_blob(override.textual_inversions, category="textual_inversions"),
        "upscalers": _decode_override_blob(override.upscalers, category="upscalers"),
    }


def _structured_files(metadata: dict[str, dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for category in metadata.values():
        items = category.get("items")
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict) and isinstance(item.get("file"), str):
                names.append(item["file"])
    return _dedupe(names)


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def files_exist_results(
    response: image_service.FileExistenceResponse,
    *,
    hash_requested: set[str],
) -> list[dict[str, Any]]:
    """Normalize parallel FilesExist arrays without assuming perfect server alignment."""
    results: list[dict[str, Any]] = []
    for index, filename in enumerate(response.files):
        exists = response.existences[index] if index < len(response.existences) else None
        digest = response.hashes[index] if index < len(response.hashes) else b""
        item: dict[str, Any] = {
            "file": filename,
            "exists": exists,
            "hash_requested": filename in hash_requested,
            "sha256": digest.hex() if len(digest) == 32 else None,
            "hash_byte_count": len(digest),
        }
        if digest and len(digest) != 32:
            item["unexpected_hash_hex"] = digest.hex()
        results.append(item)
    return results


async def _probe(args: argparse.Namespace) -> dict[str, Any]:
    shared_secret = os.environ.get(SHARED_SECRET_ENV, "").strip() or None
    endpoint = f"grpcs://{args.host}:{args.port}"
    channel = Channel(args.host, args.port, ssl=_drawthings_tls_context())
    stub = image_service.ImageGenerationServiceStub(channel)

    report: dict[str, Any] = {
        "probe_version": 1,
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "endpoint": endpoint,
        "client_contract": {
            "safe_rpcs_called": list(SAFE_RPCS),
            "deliberately_not_called": list(DELIBERATELY_SKIPPED_RPCS),
            "download_rpc_exposed_by_current_client_contract": False,
        },
        "authentication": {
            "shared_secret_environment_set": shared_secret is not None,
            "shared_secret_value_persisted": False,
        },
        "rpc": {},
    }

    try:
        echo_request = image_service.EchoRequest(name=CLIENT_NAME)
        if shared_secret is not None:
            echo_request.shared_secret = shared_secret

        try:
            echo = await stub.echo(echo_request, timeout=args.timeout)
        except Exception as exc:
            raise ProbeError(f"Echo failed: {type(exc).__name__}: {exc}") from exc

        metadata = decode_metadata_override(echo.override)
        echo_files = [str(name) for name in echo.files]
        structured_files = _structured_files(metadata)

        report["rpc"]["echo"] = {
            "status": "ok",
            "message": echo.message,
            "server_identifier": str(echo.server_identifier) if echo.server_identifier else "unknown",
            "shared_secret_missing": echo.shared_secret_missing,
            "thresholds": _thresholds(echo.thresholds),
            "file_count": len(echo_files),
            "files": echo_files,
            "metadata_override": metadata,
        }

        metadata_candidates = [name for name, _ in METADATA_FILE_CANDIDATES]
        requested_files = _dedupe(echo_files + structured_files + metadata_candidates + args.hash_file)
        hash_files = _dedupe(metadata_candidates + args.hash_file)
        hash_requested = set(hash_files)

        files_request = image_service.FileListRequest(
            files=requested_files,
            files_with_hash=hash_files,
        )
        if shared_secret is not None:
            files_request.shared_secret = shared_secret

        try:
            files_response = await stub.files_exist(files_request, timeout=args.timeout)
            normalized_files = files_exist_results(files_response, hash_requested=hash_requested)
            by_file = {item["file"]: item for item in normalized_files}
            report["rpc"]["files_exist"] = {
                "status": "ok",
                "requested_file_count": len(requested_files),
                "requested_hash_count": len(hash_files),
                "results": normalized_files,
                "metadata_file_candidates": [
                    {
                        "file": name,
                        "candidate_role": role,
                        **(
                            by_file.get(name)
                            or {
                                "exists": None,
                                "hash_requested": True,
                                "sha256": None,
                                "hash_byte_count": None,
                            }
                        ),
                    }
                    for name, role in METADATA_FILE_CANDIDATES
                ],
                "consistency": {
                    "echo_files_missing_from_response": [name for name in echo_files if name not in by_file],
                    "echo_files_reported_absent": [
                        name for name in echo_files if name in by_file and by_file[name]["exists"] is False
                    ],
                    "structured_files_reported_absent": [
                        name for name in structured_files if name in by_file and by_file[name]["exists"] is False
                    ],
                },
            }
        except Exception as exc:
            report["rpc"]["files_exist"] = {
                "status": "error",
                "error": f"{type(exc).__name__}: {exc}",
            }

        try:
            pubkey = await stub.pubkey(
                image_service.PubkeyRequest(name=CLIENT_NAME),
                timeout=args.timeout,
            )
            report["rpc"]["pubkey"] = {
                "status": "ok",
                "message": pubkey.message,
                "pubkey": pubkey.pubkey,
            }
        except Exception as exc:
            report["rpc"]["pubkey"] = {
                "status": "error",
                "error": f"{type(exc).__name__}: {exc}",
            }

        try:
            hours = await stub.hours(image_service.HoursRequest(), timeout=args.timeout)
            report["rpc"]["hours"] = {
                "status": "ok",
                "thresholds": _thresholds(hours.thresholds),
            }
        except Exception as exc:
            report["rpc"]["hours"] = {
                "status": "error",
                "error": f"{type(exc).__name__}: {exc}",
            }

        report["observations"] = {
            "grpc_exposes_lora_origin_field": False,
            "echo_lora_provenance_limit": (
                "Echo serializes merged LoRA specifications; official/community/custom origin "
                "is not carried as a field in the current LoRA specification."
            ),
            "files_exist_can_return_file_bytes": False,
            "files_exist_can_return_sha256": True,
            "metadata_registry_contents_retrievable_via_current_grpc_contract": False,
        }
        return report
    finally:
        channel.close()


def _print_summary(report: dict[str, Any]) -> None:
    echo = report["rpc"].get("echo", {})
    files_exist = report["rpc"].get("files_exist", {})
    print(f"Endpoint: {report['endpoint']}")
    print(f"Server identity: {echo.get('server_identifier', '<unavailable>')}")
    print(f"Echo files: {echo.get('file_count', '<unavailable>')}")
    metadata = echo.get("metadata_override", {})
    if isinstance(metadata, dict):
        print(
            "Structured metadata: "
            + ", ".join(f"{name}={value.get('count')}" for name, value in metadata.items() if isinstance(value, dict))
        )

    if files_exist.get("status") == "ok":
        print("Metadata-file visibility:")
        for item in files_exist.get("metadata_file_candidates", []):
            digest = item.get("sha256")
            hash_text = digest if digest else "<not returned>"
            print(f"  {item['file']}: exists={item.get('exists')} sha256={hash_text}")
    else:
        print(f"FilesExist: {files_exist.get('error', '<unavailable>')}")

    for rpc_name in ("pubkey", "hours"):
        rpc = report["rpc"].get(rpc_name, {})
        print(f"{rpc_name}: {rpc.get('status', 'not_run')}")


def main() -> int:
    """Run the non-generative gRPC capability probe."""
    parser = build_parser()
    args = parser.parse_args()
    _validate_args(args, parser)

    try:
        report = asyncio.run(_probe(args))
    except ProbeError as exc:
        raise SystemExit(str(exc)) from exc

    if args.out is None:
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _print_summary(report)
    print(f"Full JSON report: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
