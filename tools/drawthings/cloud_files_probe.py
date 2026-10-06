"""Read-only probe: ask DT+ over gRPC whether it holds a LoRA file (FilesExist), optionally comparing SHA-256.

No file bytes are uploaded and no generation is submitted. Credentials resolve like the application (environment
variable, otherwise the saved credential). The output never contains credentials.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import sys
from pathlib import Path

from dtflow.services.credentials import SavedCredentials
from dtflow.services.session import open_session, selection_for


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-f", "--filename", required=True, help="LoRA file name as used in generation.")
    parser.add_argument("-l", "--file", type=Path, help="Local file to hash for comparison (read only).")
    parser.add_argument("-d", "--state", type=Path, default=Path(".dtflow"))
    return parser


async def probe(args: argparse.Namespace) -> int:
    local = None
    if args.file is not None:
        digest = hashlib.sha256()
        with args.file.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        local = digest.hexdigest()
    selection = selection_for("cloud", None, args.state)
    async with open_session(selection, args.state, durable=False, saved=SavedCredentials()) as service:
        identities = await service.backend.get_file_identities((args.filename,), with_sha256=(args.filename,))
    for item in identities:
        print(f"file={item.identifier} exists={item.exists} cloud_sha256={item.sha256} local_sha256={local}")
        if item.sha256 and local:
            print("hash_match=" + str(item.sha256 == local))
    return 0


def main() -> int:
    try:
        return asyncio.run(probe(build_parser().parse_args()))
    except Exception as exc:  # report the code only; never a credential-bearing message
        print(
            f"probe failed: {type(exc).__name__}: {getattr(getattr(exc, 'failure', None), 'code', '')}", file=sys.stderr
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
