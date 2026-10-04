"""Scripted Draw Things gRPC server for offline adapter tests.

Runs the real generated service base over TLS + HTTP/2 on an ephemeral loopback port that is registered with the
exact-endpoint network guard for the lifetime of the context. It returns scripted protocol fixtures only; it loads no
models and does not imitate Draw Things internals.
"""

from __future__ import annotations

import asyncio
import json
import socket
import ssl
import struct
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, closing
from dataclasses import dataclass, field
from pathlib import Path

import grpclib
import numpy as np
from drawthings_py.generated.dt_grpc import image_service
from drawthings_py.generated.dt_grpc.config_generated import GenerationConfiguration
from grpclib.const import Cardinality, Handler, Status
from grpclib.server import Server

from support.network_guard import endpoint

TLS_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "tls"
HOST = "127.0.0.1"
FPZIP_MAGIC = 1012247


# --- CCV tensor builder (the wire format Draw Things returns) -------------------------------------------------------


def tensor(width: int, height: int, channels: int, values: np.ndarray, *, compressed: bool = False) -> bytes:
    """Encode float values in [-1, 1] as a 68-byte-header CCV tensor (raw fp16 or fpzip-compressed)."""
    header = [0] * 17
    header[6], header[7], header[8] = height, width, channels
    if compressed:
        import fpzip

        header[0] = FPZIP_MAGIC
        body = fpzip.compress(values.astype(np.float32).reshape(1, height, width, channels), order="C")
    else:
        body = values.astype(np.float16).tobytes()
    return struct.pack("<17I", *header) + body


def pixel_tensor(width: int, height: int, channels: int = 3, *, fill: int = 128, compressed: bool = False) -> bytes:
    """A solid-colour image tensor whose decoded pixels are all ``fill``."""
    values = np.full(width * height * channels, fill / 127.5 - 1, dtype=np.float32)
    return tensor(width, height, channels, values, compressed=compressed)


# --- Script steps ---------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Signpost:
    kind: str = "sampling"  # text_encoded | sampling | image_decoded
    step: int = 1


@dataclass(frozen=True)
class Preview:
    data: bytes


@dataclass(frozen=True)
class Image:
    data: bytes
    more_chunks: bool = False


@dataclass(frozen=True)
class Fail:
    """Finish the RPC with trailers carrying this gRPC status."""

    status: Status


@dataclass(frozen=True)
class Hang:
    """Stay silent until the client cancels or the server is closed."""


@dataclass(frozen=True)
class Drop:
    """Abort the TCP connection without trailers."""


Step = Signpost | Preview | Image | Fail | Hang | Drop


@dataclass
class EchoSpec:
    files: tuple[str, ...] = ()
    models: tuple[dict, ...] = ()
    loras: tuple[dict, ...] = ()
    control_nets: tuple[dict, ...] = ()
    secret: str | None = None  # when set, a missing or different shared secret yields shared_secret_missing
    server_identifier: int = 7


@dataclass
class Observed:
    echo_requests: list = field(default_factory=list)
    generate_requests: list = field(default_factory=list)
    metadata: list = field(default_factory=list)
    deadlines: list = field(default_factory=list)
    cancelled: asyncio.Event = field(default_factory=asyncio.Event)
    hanging: asyncio.Event = field(default_factory=asyncio.Event)
    active: int = 0
    peak_active: int = 0


class ScriptedService(image_service.ImageGenerationServiceBase):
    def __init__(self, steps: tuple[Step, ...] = (), echo: EchoSpec | None = None):
        self.steps = steps
        self.echo_spec = echo or EchoSpec()
        self.seen = Observed()

    def configuration(self, index: int = -1) -> GenerationConfiguration:
        return GenerationConfiguration.GetRootAs(self.seen.generate_requests[index].configuration, 0)

    async def echo(self, echo_request):
        self.seen.echo_requests.append(echo_request)
        spec = self.echo_spec
        if spec.secret is not None and echo_request.shared_secret != spec.secret:
            return image_service.EchoReply(
                message="HELLO", shared_secret_missing=True, server_identifier=spec.server_identifier
            )
        override = image_service.MetadataOverride(
            models=json.dumps(list(spec.models)).encode() if spec.models else b"",
            loras=json.dumps(list(spec.loras)).encode() if spec.loras else b"",
            control_nets=json.dumps(list(spec.control_nets)).encode() if spec.control_nets else b"",
        )
        return image_service.EchoReply(
            message="HELLO",
            files=list(spec.files),
            override=override,
            server_identifier=spec.server_identifier,
        )

    def __mapping__(self):
        mapping = super().__mapping__()
        mapping["/ImageGenerationService/GenerateImage"] = Handler(
            self._generate,
            Cardinality.UNARY_STREAM,
            image_service.ImageGenerationRequest,
            image_service.ImageGenerationResponse,
        )
        return mapping

    async def _generate(self, stream) -> None:
        request = await stream.recv_message()
        self.seen.generate_requests.append(request)
        self.seen.metadata.append(dict(stream.metadata))
        self.seen.deadlines.append(stream.deadline)
        self.seen.active += 1
        self.seen.peak_active = max(self.seen.peak_active, self.seen.active)
        try:
            for step in self.steps:
                if isinstance(step, Signpost):
                    signpost = image_service.ImageGenerationSignpostProto()
                    if step.kind == "text_encoded":
                        signpost.text_encoded = image_service.ImageGenerationSignpostProtoTextEncoded()
                    elif step.kind == "image_decoded":
                        signpost.image_decoded = image_service.ImageGenerationSignpostProtoImageDecoded()
                    else:
                        signpost.sampling = image_service.ImageGenerationSignpostProtoSampling(step=step.step)
                    await stream.send_message(image_service.ImageGenerationResponse(current_signpost=signpost))
                elif isinstance(step, Preview):
                    await stream.send_message(image_service.ImageGenerationResponse(preview_image=step.data))
                elif isinstance(step, Image):
                    await stream.send_message(
                        image_service.ImageGenerationResponse(
                            generated_images=[step.data],
                            chunk_state=image_service.ChunkState.MORE_CHUNKS
                            if step.more_chunks
                            else image_service.ChunkState.LAST_CHUNK,
                        )
                    )
                elif isinstance(step, Fail):
                    raise grpclib.GRPCError(step.status)
                elif isinstance(step, Hang):
                    self.seen.hanging.set()
                    await asyncio.Event().wait()
                elif isinstance(step, Drop):
                    stream._stream._transport.abort()
                    await asyncio.sleep(3600)
        except asyncio.CancelledError:
            self.seen.cancelled.set()
            raise
        finally:
            self.seen.active -= 1


# --- Server lifecycle -----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RunningServer:
    host: str
    port: int
    service: ScriptedService

    @property
    def seen(self) -> Observed:
        return self.service.seen


def server_context(leaf: str) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(TLS_DIR / f"{leaf}.pem", TLS_DIR / f"{leaf}.key")
    context.set_alpn_protocols(["h2"])
    return context


def client_context(*, ca: str = "ca") -> ssl.SSLContext:
    """A strict client context trusting a fixture CA (verification and hostname checking enabled)."""
    context = ssl.create_default_context(cafile=str(TLS_DIR / f"{ca}.pem"))
    context.set_alpn_protocols(["h2"])
    return context


@asynccontextmanager
async def scripted_server(
    steps: tuple[Step, ...] = (), echo: EchoSpec | None = None, *, leaf: str = "server"
) -> AsyncIterator[RunningServer]:
    service = ScriptedService(steps, echo)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind((HOST, 0))
    port = sock.getsockname()[1]
    server = Server([service])
    await server.start(sock=sock, ssl=server_context(leaf))
    try:
        with endpoint(HOST, port):
            yield RunningServer(HOST, port, service)
    finally:
        server.close()
        server._server.close_clients()  # Python >= 3.13 waits for open client connections in wait_closed()
        await asyncio.wait_for(server.wait_closed(), 10)


@asynccontextmanager
async def closed_port() -> AsyncIterator[tuple[str, int]]:
    """A registered loopback port with nothing listening (connection refused)."""
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as probe:
        probe.bind((HOST, 0))
        port = probe.getsockname()[1]
    with endpoint(HOST, port):
        yield HOST, port


@asynccontextmanager
async def black_hole() -> AsyncIterator[tuple[str, int]]:
    """A registered loopback server that accepts TCP and never answers the TLS handshake."""

    async def silent(reader, writer):
        try:
            await reader.read()  # never answers; ends when the client gives up and closes
        finally:
            writer.close()

    server = await asyncio.start_server(silent, HOST, 0)
    port = server.sockets[0].getsockname()[1]
    try:
        with endpoint(HOST, port):
            yield HOST, port
    finally:
        server.close()
        server.close_clients()
        await server.wait_closed()
