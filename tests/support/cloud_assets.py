"""In-memory DT+ API and signed-storage fixture for the cloud asset suites (no sockets, no real service)."""

import asyncio
import hashlib
import json
from pathlib import Path

import httpx

from dtflow.backends.drawthings.cloud import DirectCloudBackend
from dtflow.backends.drawthings.cloud_assets import AssetUploadPolicy
from dtflow.backends.drawthings.cloud_auth import CloudAuth
from dtflow.core.models import Credential

API_KEY = "FAKE-API-KEY-SECRET"
SIGNED = "https://storage.invalid/put/OBJECT?X-Signature=SIGNED-QUERY-SECRET"
CHUNK = 1024


class Credentials:
    async def get(self):
        return Credential("test", API_KEY)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class StorageTransport(httpx.AsyncBaseTransport):
    def __init__(self, handler):
        self._handler = handler

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return await self._handler(request)


class DtPlus:
    """Scripted DT+ BYOM API plus signed storage. ``response`` overrides the negotiation body for fault tests."""

    def __init__(self, origin: str = "https://api.one.invalid"):
        self.origin = origin
        self.stored: dict[str, str] = {}
        self.api_calls: list[dict] = []
        self.puts: list[dict] = []
        self.put_attempts = 0
        self.response = None
        self.storage_status = 200
        self.storage_error: Exception | None = None
        self.connect_error: Exception | None = None
        self.token_status = 200
        self.on_chunk = None  # async callback(first_chunk_bytes) while the PUT streams
        self.store_on_put = True

    async def api(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/sdk/token":
            if self.token_status != 200:
                return httpx.Response(self.token_status, json={"error": "denied"})
            return httpx.Response(200, json={"shortTermToken": "FAKE-SHORT-TOKEN", "expiresIn": 3600})
        body = json.loads(request.content)
        entry = body["files"][0]
        self.api_calls.append(
            {"path": request.url.path, "entry": entry, "authorization": request.headers.get("authorization")}
        )
        if self.response is not None:
            return httpx.Response(200, json=self.response(entry))
        digest, name = entry["sha256"], entry["file"]
        if digest in self.stored:
            data = {"alreadyExists": {digest: self.stored[digest]}}
        else:
            # The wire response is a private server-returned value, independent of local content identity.
            data = {"signedUrls": {"urls": {name: {"sha256": "server_" + digest, "url": SIGNED}}}}
        return httpx.Response(200, json={"code": 208, "data": data})

    async def storage(self, request: httpx.Request) -> httpx.Response:
        # A real transport (unlike MockTransport) leaves the body unbuffered, so chunk sizes are observable.
        self.put_attempts += 1
        if self.connect_error is not None:
            raise self.connect_error  # raised before any body byte is consumed, like a real failed connect
        sizes, body = [], bytearray()
        async for chunk in request.stream:
            sizes.append(len(chunk))
            body.extend(chunk)
            if self.on_chunk is not None and len(sizes) == 1:
                await self.on_chunk()
        self.puts.append(
            {
                "url": str(request.url),
                "headers": dict(request.headers),
                "sizes": sizes,
                "body": bytes(body),
            }
        )
        if self.storage_error is not None:
            raise self.storage_error
        if self.storage_status == 200 and self.store_on_put:
            self.stored[sha256(bytes(body))] = "remote-" + sha256(bytes(body))[:12]
        return httpx.Response(self.storage_status, text="storage says SIGNED-QUERY-SECRET")

    def backend(self) -> DirectCloudBackend:
        auth = CloudAuth(Credentials(), origin=self.origin, transport=httpx.MockTransport(self.api))
        return DirectCloudBackend(
            auth,
            upload_transport=StorageTransport(self.storage),
            upload_policy=AssetUploadPolicy(chunk=CHUNK, total=10.0, cleanup=1.0),
        )


def lora_file(directory: Path, name: str = "style.ckpt", data: bytes | None = None) -> Path:
    path = directory / name
    path.write_bytes(data if data is not None else bytes(range(256)) * 20)  # 5120 bytes: five 1 KiB chunks
    return path


async def settle_loop(times: int = 20) -> None:
    for _ in range(times):
        await asyncio.sleep(0)
