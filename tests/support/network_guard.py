"""Accidental-network guard with exact per-test endpoints; not an OS sandbox."""

import asyncio
import ipaddress
import platform
import socket
import subprocess
import threading
from contextlib import ExitStack, contextmanager

_ORIGINALS = {}
_endpoints: set[tuple[str, int]] = set()
_commands: set[tuple[str, ...]] = set()
_local = threading.local()
_lock = threading.Lock()


def _address(address) -> tuple[str, int]:
    if not isinstance(address, tuple) or len(address) < 2:
        raise AssertionError("Only registered IP test endpoints are allowed.")
    host = address[0].decode("ascii") if isinstance(address[0], bytes) else str(address[0])
    port = int(address[1])
    return host, port


def check(address) -> None:
    host, port = _address(address)
    if getattr(_local, "socketpair", False):
        # Only the captured stdlib socketpair implementation runs in this scope.
        if ipaddress.ip_address(host).is_loopback:
            return
    with _lock:
        if (host, port) not in _endpoints:
            raise AssertionError(f"Unregistered network endpoint: {host}:{port}")


@contextmanager
def endpoint(host: str, port: int):
    """Register an already-bound fixture address; no blanket loopback allowance."""
    ipaddress.ip_address(host)
    value = (host, port)
    with _lock:
        if value in _endpoints:
            raise AssertionError("Endpoint already registered.")
        _endpoints.add(value)
    try:
        yield
    finally:
        with _lock:
            _endpoints.discard(value)


@contextmanager
def live_endpoints(*targets: tuple[str, int]):
    """Allow only named live targets and their resolved numeric endpoints for this explicit check."""
    with ExitStack() as stack:
        names = set(targets)
        addresses = set()
        for host, port in names:
            addresses.update(
                (record[4][0], port) for record in _ORIGINALS["getaddrinfo"](host, port, type=socket.SOCK_STREAM)
            )
        for host, port in addresses:
            stack.enter_context(endpoint(host, port))
        with _lock:
            if names & _endpoints:
                raise AssertionError("Live target already registered.")
            _endpoints.update(names)
        try:
            yield
        finally:
            with _lock:
                _endpoints.difference_update(names)


@contextmanager
def child_command(command: list[str]):
    """Allow one exact Python command that installs this guard in its child."""
    value = tuple(command)
    _commands.add(value)
    try:
        yield
    finally:
        _commands.discard(value)


def install() -> None:
    """Install before collection/client imports; allow private stdlib socketpairs."""
    if _ORIGINALS:
        return
    # On Windows platform.uname() may fall back to a one-time `ver` subprocess when its WMI query fails, and imports
    # such as grpclib call it lazily; resolve and cache it before subprocesses are guarded. Its lazy `processor` field
    # runs `uname -p` on POSIX; platform.platform() (read by pytest-xdist workers) resolves both.
    platform.platform()
    for name in ("connect", "connect_ex", "sendto"):
        _ORIGINALS[name] = getattr(socket.socket, name)
    for name in ("gethostbyname", "gethostbyname_ex", "gethostbyaddr"):
        _ORIGINALS[name] = getattr(socket, name)

        def denied_resolution(*args, **kwargs):
            raise AssertionError("Unregistered DNS resolution is prohibited.")

        setattr(socket, name, denied_resolution)
    _ORIGINALS["getaddrinfo"] = socket.getaddrinfo
    _ORIGINALS["socketpair"] = socket.socketpair
    _ORIGINALS["popen"] = subprocess.Popen

    def connect(self, address):
        check(address)
        return _ORIGINALS["connect"](self, address)

    def connect_ex(self, address):
        check(address)
        return _ORIGINALS["connect_ex"](self, address)

    def sendto(self, data, *args):
        check(args[-1])
        return _ORIGINALS["sendto"](self, data, *args)

    def getaddrinfo(host, port, *args, **kwargs):
        check((host, port))
        return _ORIGINALS["getaddrinfo"](host, port, *args, **kwargs)

    def socketpair(*args, **kwargs):
        previous = getattr(_local, "socketpair", False)
        _local.socketpair = True
        try:
            return _ORIGINALS["socketpair"](*args, **kwargs)
        finally:
            _local.socketpair = previous

    def popen(command, *args, **kwargs):
        if not isinstance(command, (list, tuple)) or tuple(command) not in _commands or kwargs.get("shell"):
            raise AssertionError("Subprocesses need explicit guarded-test registration.")
        return _ORIGINALS["popen"](command, *args, **kwargs)

    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    socket.socket.sendto = sendto
    socket.getaddrinfo = getaddrinfo
    socket.socketpair = socketpair
    subprocess.Popen = popen
    original = asyncio.BaseEventLoop.create_connection
    _ORIGINALS["create_connection"] = original

    async def create_connection(self, factory, host=None, port=None, **kwargs):
        address = kwargs.get("sock")
        if address is not None:
            check(address.getpeername())
        else:
            check((host, port))
        return await original(self, factory, host, port, **kwargs)

    asyncio.BaseEventLoop.create_connection = create_connection
    # Windows' IOCP sock_connect bypasses socket.connect, so guard its public entry too.
    classes = {asyncio.SelectorEventLoop}
    if hasattr(asyncio, "ProactorEventLoop"):
        classes.add(asyncio.ProactorEventLoop)
    for cls in classes:
        method = cls.sock_connect
        _ORIGINALS[(cls, "sock_connect")] = method

        async def sock_connect(self, sock, address, _method=method):
            check(address)
            return await _method(self, sock, address)

        cls.sock_connect = sock_connect

    original_datagram = asyncio.BaseEventLoop.create_datagram_endpoint
    _ORIGINALS["create_datagram_endpoint"] = original_datagram

    async def create_datagram_endpoint(self, factory, local_addr=None, remote_addr=None, **kwargs):
        if remote_addr is not None:
            check(remote_addr)
        if kwargs.get("sock") is not None:
            try:
                check(kwargs["sock"].getpeername())
            except OSError:
                pass
        return await original_datagram(self, factory, local_addr=local_addr, remote_addr=remote_addr, **kwargs)

    asyncio.BaseEventLoop.create_datagram_endpoint = create_datagram_endpoint
    # Guard transport-level sends too: Windows IOCP can bypass socket.sendto.
    from asyncio import proactor_events, selector_events

    for cls in (selector_events._SelectorDatagramTransport, proactor_events._ProactorDatagramTransport):
        method = cls.sendto
        _ORIGINALS[(cls, "sendto")] = method

        def transport_sendto(self, data, addr=None, _method=method):
            check(addr or self._address)
            return _method(self, data, addr)

        cls.sendto = transport_sendto


def reset_registry() -> None:
    if _endpoints or _commands:
        raise AssertionError("Test endpoint/child registration leaked beyond teardown.")


def live_allowed(selection: str, environ: dict, generation: bool) -> bool:
    selected = selection in ("live", "live_generation", "live and live_generation", "live_generation and live")
    if not selected or environ.get("DTFLOW_LIVE_TESTS") != "1":
        return False
    if generation:
        return (
            selection != "live"
            and environ.get("DTFLOW_LIVE_GENERATION") == "1"
            and bool(environ.get("DTFLOW_LIVE_AUTHORIZED_WORKLOAD", "").strip())
        )
    return True
