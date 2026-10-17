"""Narrow Windows file/lock boundary for source-run WSL; no ledger or UI logic.

The child accepts only named DTFlow leases or a fingerprinted managed original.
All orchestration and durable intent remain in the caller's existing service.
"""

from __future__ import annotations

import ctypes
import json
import logging
import os
import platform
import queue
import re
import stat
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path, PureWindowsPath

log = logging.getLogger(__name__)


class RefusedBeforeMutation(OSError):
    """Windows deletion was confirmed not to have reached the disposition call."""


class WindowsBridgeError(OSError):
    """Stable, sanitized prerequisite or drive-mapping failure."""

    def __init__(self, reason: str):
        self.reason = reason
        messages = {
            "mapping": "Windows drive mapping is unavailable or ambiguous.",
            "redirect": "Redirected shared Windows paths are unsupported.",
            "mount": "Shared filesystem has no verified Windows lock boundary.",
            "interpreter": "This checkout requires a Windows .venv/Scripts/python.exe.",
        }
        super().__init__(messages[reason])


def windows_path(path: Path, mounts) -> str:
    """Invert one verified whole-drive mapping, rejecting redirected components."""
    path = path.absolute()
    candidates = [(drive, Path(root)) for drive, root in mounts if path.is_relative_to(Path(root))]
    if len(candidates) != 1:
        raise WindowsBridgeError("mapping")
    drive, root = candidates[0]
    if len({mounted for letter, mounted in mounts if letter == drive}) != 1:
        raise WindowsBridgeError("mapping")
    for component in (path, *path.parents):
        if component.is_symlink():
            raise WindowsBridgeError("redirect")
    if path.resolve() != path:
        raise WindowsBridgeError("redirect")
    return str(PureWindowsPath(drive + ":/", *path.relative_to(root).parts))


def bridge(state: Path):
    """Return a source-only Windows command/state mapping, or None on native FS."""
    if os.name == "nt":
        return None
    from dtflow.services.artifact_paths import ArtifactReadPaths

    paths = ArtifactReadPaths.discover()
    state = Path(state).absolute()
    matches = [root for _drive, root in paths.mounts if state.is_relative_to(Path(root))]
    if not matches:
        if "microsoft" in platform.release().lower():
            # Do not silently use Linux-only locks on an unrecognized Windows
            # mount (including failed discovery or unsupported WSL 2 mounts).
            try:
                with Path("/proc/self/mountinfo").open("rb") as stream:
                    data = stream.read(1024 * 1024 + 1)
                if len(data) > 1024 * 1024:
                    raise WindowsBridgeError("mount")
                from dtflow.services.artifact_paths import _unescape

                candidates = []
                for line in data.decode("utf-8").splitlines():
                    before, separator, after = line.partition(" - ")
                    fields, filesystem = before.split(), after.split()
                    if separator and len(fields) >= 6 and filesystem:
                        mount = Path(_unescape(fields[4]))
                        if state.is_relative_to(mount):
                            candidates.append((len(mount.parts), filesystem[0]))
                if not candidates or max(candidates)[1] not in ("rootfs", "wslfs", "ext4"):
                    log.debug("windows bridge filesystem=%s", max(candidates)[1] if candidates else "unknown")
                    raise WindowsBridgeError("mount")
            except WindowsBridgeError:
                raise
            except (OSError, UnicodeError, ValueError) as exc:
                raise WindowsBridgeError("mount") from exc
        return None
    native_state = windows_path(state, paths.mounts)
    root = Path(__file__).resolve().parents[3]
    executable = root / ".venv" / "Scripts" / "python.exe"
    if not executable.is_file():
        raise WindowsBridgeError("interpreter")
    windows_path(executable, paths.mounts)
    script = windows_path(Path(__file__).resolve(), paths.mounts)
    return [str(executable), script], native_state


def _start(command, request):
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        bufsize=1,
    )
    try:
        process.stdin.write(json.dumps(request) + "\n")
        process.stdin.flush()
    except OSError:
        _close(process)
        raise
    return process


def _reply(process, timeout, *, allow_refusal: bool = False):
    messages = queue.Queue(maxsize=1)

    def receive():
        try:
            messages.put(process.stdout.readline(65537))
        except (OSError, ValueError):
            messages.put("")

    threading.Thread(target=receive, daemon=True).start()
    try:
        line = messages.get(timeout=timeout)
        if not line or len(line) > 65536:
            raise ValueError
        response = json.loads(line)
        if not isinstance(response, dict):
            raise ValueError
        if response.get("ok") is True:
            return response
        if allow_refusal and response.get("ok") is False and response.get("phase") == "pre":
            raise RefusedBeforeMutation("Windows deletion was refused before mutation.")
        raise ValueError
    except (queue.Empty, ValueError, TypeError) as exc:
        raise OSError("Windows original-file helper did not confirm its result.") from exc


def _close(process):
    try:
        process.stdin.close()
        process.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        process.kill()
        process.wait(timeout=5)
    finally:
        process.stdout.close()


_LEASE_NAMES = ("original_images.lock", "image_recycle_operation.lock")


class _LeaseHelper:
    """One long-lived Windows helper per state; leases are cheap pipe round trips.

    Starting Windows Python through WSL1 interop costs about a second, so the
    helper is started once and asked to try, then release, each named lock.
    Threads of this process are serialized by an in-process lock per name;
    the helper's Windows lock excludes other processes. If the helper dies,
    Windows releases its locks, so a broken helper is discarded and restarted.
    """

    def __init__(self, command, state):
        self.command, self.state = command, state
        self.io = threading.Lock()
        self.local = {name: threading.Lock() for name in _LEASE_NAMES}
        self.process = None
        self.messages: queue.Queue = queue.Queue()

    def _spawn(self):
        process = _start(self.command, dict(action="serve", state=self.state))
        messages: queue.Queue = queue.Queue()

        def receive():
            try:
                for line in process.stdout:
                    messages.put(line)
            except (OSError, ValueError):
                pass
            messages.put("")

        threading.Thread(target=receive, daemon=True, name="dtflow-windows-lease").start()
        self.process, self.messages = process, messages
        if not self._answer(30)["ok"]:
            self._discard()
            raise OSError("Windows original-file helper could not open the shared state.")

    def _answer(self, timeout):
        try:
            line = self.messages.get(timeout=timeout)
            if not line or len(line) > 65536:
                raise ValueError
            response = json.loads(line)
            if not isinstance(response, dict) or not isinstance(response.get("ok"), bool):
                raise ValueError
            return response
        except (queue.Empty, ValueError, TypeError) as exc:
            self._discard()
            raise OSError("Windows original-file helper did not confirm its result.") from exc

    def _discard(self):
        process, self.process = self.process, None
        if process is not None:
            _close(process)

    def _call(self, request, timeout=15):
        with self.io:
            if self.process is None or self.process.poll() is not None:
                self._discard()
                self._spawn()
            try:
                self.process.stdin.write(json.dumps(request) + "\n")
                self.process.stdin.flush()
            except OSError:
                self._discard()
                raise
            return self._answer(timeout)

    @contextmanager
    def lease(self, filename, timeout):
        if filename not in self.local:
            raise ValueError("Unknown DTFlow lease")
        deadline = time.monotonic() + timeout
        if not self.local[filename].acquire(timeout=max(timeout, 0)):
            raise OSError("Original-file lease is held by this process.")
        try:
            while True:
                response = self._call(dict(op="try", filename=filename))
                if response["ok"]:
                    break
                if response.get("error") or time.monotonic() >= deadline:
                    raise OSError("Original-file lease is held by another process.")
                time.sleep(0.05)
            try:
                yield
            finally:
                try:
                    released = self._call(dict(op="release", filename=filename))["ok"]
                except OSError:
                    released = True  # The failed helper was discarded, which released its locks.
                if not released:
                    # Unknown lock state: dropping the helper releases its Windows locks.
                    with self.io:
                        self._discard()
        finally:
            self.local[filename].release()


_helpers: dict[tuple, _LeaseHelper] = {}
_helpers_lock = threading.Lock()


@contextmanager
def shared_lock(command, state, filename, timeout):
    with _helpers_lock:
        helper = _helpers.setdefault((tuple(command), state), _LeaseHelper(command, state))
    with helper.lease(filename, timeout):
        yield


def _serve(state: Path) -> None:
    """Windows only: hold named locks for the WSL parent until it releases them or exits."""
    import msvcrt

    held = {}
    print('{"ok":true}', flush=True)
    for line in sys.stdin:  # EOF on parent death ends the loop; Windows releases the locks.
        try:
            request = json.loads(line)
            filename = request["filename"]
            if filename not in _LEASE_NAMES:
                raise ValueError
            if request["op"] == "try":
                if filename in held:
                    raise ValueError
                handle = (state / filename).open("a+b")
                try:
                    if handle.seek(0, os.SEEK_END) == 0:
                        handle.write(b"0")
                        handle.flush()
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError:
                    handle.close()
                    print('{"ok":false}', flush=True)
                    continue
                held[filename] = handle
            elif request["op"] == "release":
                handle = held.pop(filename, None)  # A restarted helper never held it.
                if handle is not None:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    handle.close()
            else:
                raise ValueError
            print('{"ok":true}', flush=True)
        except Exception:
            print('{"ok":false,"error":true}', flush=True)


def delete_managed(state: Path, target) -> None:
    """Delete a verified Windows file by handle; uncertain child results never replay."""
    if os.name == "nt":
        _delete(
            dict(
                state=str(Path(state).absolute()),
                identity=target.identity,
                kind=target.kind,
                size=target.size,
                mtime_ns=target.mtime_ns,
                inode=target.inode,
            )
        )
        return
    connection = bridge(state)
    if connection is None:
        raise OSError("No Windows mutation boundary for this state.")
    command, native_state = connection
    started = time.monotonic()
    process = _start(
        command,
        dict(
            action="delete",
            state=native_state,
            identity=target.identity,
            kind=target.kind,
            size=target.size,
            mtime_ns=target.mtime_ns,
            inode=target.inode,
        ),
    )
    try:
        _reply(process, 30, allow_refusal=True)
    except OSError as exc:
        # Successful deletes stay quiet; the phase decides whether this is retryable or uncertain.
        log.debug(
            "windows delete helper identity=%s kind=%s phase=%s elapsed_ms=%d",
            target.identity,
            target.kind,
            "pre" if isinstance(exc, RefusedBeforeMutation) else "unconfirmed",
            (time.monotonic() - started) * 1000,
        )
        raise
    finally:
        _close(process)


def _delete(request):
    """Windows only: classify failures by disposition phase, never error code."""
    import msvcrt
    from ctypes import wintypes

    try:
        identity = re.fullmatch(r"([0-9a-f]{32}):(0|[1-9][0-9]{0,5})", request["identity"])
        state = Path(request["state"])
        if identity is None or not state.is_absolute() or str(state).startswith("\\\\"):
            raise ValueError("Invalid managed original")
        kind = request.get("kind", "final")
        if kind == "final":
            name = f"final-{int(identity[2]) + 1}.png"
        elif kind == "raw" and identity[2] == "0":
            name = "raw-1.png"  # provider evidence exists only for the single output of an image operation
        else:
            raise ValueError("Invalid managed original kind")
        path = state / "outputs" / identity[1] / name
        for component in (path, *path.parents):
            if component.is_symlink() or os.path.isjunction(component):
                raise ValueError("Redirected original")
        if not (path.parent / "metadata.json").is_file():
            raise ValueError("Missing metadata")
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        create = kernel.CreateFileW
        create.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        create.restype = wintypes.HANDLE
        # DELETE | FILE_READ_ATTRIBUTES; share reads only, open the reparse point itself.
        handle = create(str(path), 0x10000 | 0x80, 1, None, 3, 0x00200000, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise RefusedBeforeMutation("Windows original was refused before mutation.") from exc

    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY)
    except (OSError, ValueError, TypeError) as exc:
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        if not kernel.CloseHandle(handle):
            raise OSError("Windows original handle cleanup was not confirmed.") from exc
        raise RefusedBeforeMutation("Windows original was refused before mutation.") from exc

    try:
        info = os.fstat(descriptor)
        if (
            (info.st_size, info.st_mtime_ns, info.st_ino) != (request["size"], request["mtime_ns"], request["inode"])
            or not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or getattr(info, "st_file_attributes", 0) & 0x400
        ):
            raise ValueError("Original identity changed")
    except (OSError, ValueError, TypeError, KeyError) as exc:
        # Even a pre-disposition refusal is unconfirmed if handle release fails.
        os.close(descriptor)
        raise RefusedBeforeMutation("Windows original was refused before mutation.") from exc

    try:
        disposition = wintypes.BOOL(True)
        remove = kernel.SetFileInformationByHandle
        remove.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        remove.restype = wintypes.BOOL
        # From this call onward, neither an error nor a lost reply proves no mutation.
        if not remove(handle, 4, ctypes.byref(disposition), ctypes.sizeof(disposition)):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        # The filesystem may execute the disposition as this handle closes.
        os.close(descriptor)


def _main():
    if os.name != "nt":
        return 1
    try:
        request = json.loads(sys.stdin.readline(65537))
        if request["action"] == "serve":
            state = Path(request["state"])
            if not state.is_absolute():
                raise ValueError
            state.mkdir(parents=True, exist_ok=True)
            _serve(state)
        elif request["action"] == "delete":
            _delete(request)
            print('{"ok":true}', flush=True)
        else:
            raise ValueError
    except RefusedBeforeMutation:
        print('{"ok":false,"phase":"pre"}', flush=True)
        return 1
    except Exception:
        print('{"ok":false}', flush=True)  # No phase means mutation is unconfirmed.
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
