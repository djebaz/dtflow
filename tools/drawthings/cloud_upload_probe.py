"""Explicit experiment: upload one converted LoRA to DT+ through the gRPC UploadFile call.

This WRITES to the account's DT+ storage, so nothing is sent unless ``--yes`` is given. It checks FilesExist first and
never uploads a file DT+ already reports. Credentials resolve like the application and are never printed. The
authentication DT+ requires for this call is unverified; a refusal is reported by gRPC status only.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import sys
from pathlib import Path

from drawthings_py.generated.dt_grpc import image_service

from dtflow.services.credentials import SavedCredentials
from dtflow.services.session import open_session, selection_for

CHUNK = 1024 * 1024


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-l", "--file", type=Path, required=True, help="Converted LoRA file to upload.")
    parser.add_argument("-y", "--yes", action="store_true", help="Actually upload; without it only FilesExist runs.")
    parser.add_argument("-d", "--state", type=Path, default=Path(".dtflow"))
    return parser


def requests(path: Path, digest: bytes, size: int):
    yield image_service.FileUploadRequest(
        init_request=image_service.InitUploadRequest(filename=path.name, sha256=digest, total_size=size)
    )
    offset = 0
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK):
            yield image_service.FileUploadRequest(
                chunk=image_service.FileChunk(content=chunk, filename=path.name, offset=offset)
            )
            offset += len(chunk)


async def probe(args: argparse.Namespace) -> int:
    path = args.file
    data = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK):
            data.update(chunk)
    digest, size = data.digest(), path.stat().st_size
    selection = selection_for("cloud", None, args.state)
    async with open_session(selection, args.state, durable=False, saved=SavedCredentials()) as service:
        backend = service.backend
        (before,) = await backend.get_file_identities((path.name,), with_sha256=(path.name,))
        print(f"before: exists={before.exists} cloud_sha256={before.sha256} local_sha256={digest.hex()} size={size}")
        if before.exists:
            print("already present; nothing uploaded")
            return 0
        if not args.yes:
            print("dry run: pass --yes to upload")
            return 0
        stub = image_service.ImageGenerationServiceStub(backend._require_channel())
        async for response in stub.upload_file(requests(path, digest, size), timeout=600):
            print(
                f"upload response: ok={response.chunk_upload_success} offset={response.received_offset} msg={response.message!r}"
            )
        (after,) = await backend.get_file_identities((path.name,), with_sha256=(path.name,))
        print(f"after: exists={after.exists} cloud_sha256={after.sha256}")
    return 0


def main() -> int:
    try:
        return asyncio.run(probe(build_parser().parse_args()))
    except Exception as exc:  # status and code only; never a credential-bearing message
        code = getattr(getattr(exc, "failure", None), "code", "") or getattr(exc, "status", "")
        print(f"probe failed: {type(exc).__name__} {code}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
