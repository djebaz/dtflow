"""Narrow Windows file/lock boundary for source-run WSL; no ledger or UI logic.

The child accepts only named DTFlow leases or a fingerprinted managed original.
All orchestration and durable intent remain in the caller's existing service.
"""

from __future__ import annotations

import ctypes
import json
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


def windows_path(path: Path, mounts) -> str:
    """Invert one verified whole-drive mapping, rejecting redirected components."""
    path = path.absolute()
    candidates = [(drive, Path(root)) for drive, root in mounts if path.is_relative_to(Path(root))]
    if len(candidates) != 1:
        raise OSError("Shared Windows path is unavailable or ambiguous.")
    drive, root = candidates[0]
    for component in (path, *path.parents):
        if component.is_symlink():
            raise OSError("Redirected shared paths are not supported.")
    if path.resolve() != path:
        raise OSError("Shared path changed.")
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
            with Path("/proc/self/mountinfo").open("rb") as stream:
                data = stream.read(1024 * 1024 + 1)
            if len(data) > 1024 * 1024:
                raise OSError("Mount information is unavailable.")
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
                raise OSError("Shared filesystem has no verified Windows lock boundary.")
        return None
    native_state = windows_path(state, paths.mounts)
    root = Path(__file__).resolve().parents[3]
    executable = root / ".venv" / "Scripts" / "python.exe"
    if not executable.is_file():
        raise OSError("WSL shared originals require this checkout's Windows .venv.")
    script = windows_path(Path(__file__).resolve(), paths.mounts)
    return [str(executable), script], native_state


def _start(command, request):
    process = subprocess.Popen(
        command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, encoding="utf-8", bufsize=1,
    )
    try:
        process.stdin.write(json.dumps(request) + "\n")
        process.stdin.flush()
    except OSError:
        _close(process)
        raise
    return process


def _reply(process, timeout):
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
        if not isinstance(response, dict) or response.get("ok") is not True:
            raise ValueError
        return response
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


@contextmanager
def shared_lock(command, state, filename, timeout):
    process = _start(command, dict(action="lock", state=state, filename=filename, timeout=timeout))
    try:
        _reply(process, timeout + 10)
        yield
    finally:
        _close(process)


def delete_managed(state: Path, target) -> None:
    """Delete a verified Windows file by handle; uncertain child results never replay."""
    if os.name == "nt":
        _delete(dict(state=str(state), identity=target.identity, size=target.size,
                     mtime_ns=target.mtime_ns, inode=target.inode))
        return
    connection = bridge(state)
    if connection is None:
        raise OSError("No Windows mutation boundary for this state.")
    command, native_state = connection
    process = _start(command, dict(action="delete", state=native_state, identity=target.identity,
                                   size=target.size, mtime_ns=target.mtime_ns, inode=target.inode))
    try:
        _reply(process, 30)
    finally:
        _close(process)


def _delete(request):
    """Windows only: never delete a caller-supplied arbitrary pathname."""
    import msvcrt
    from ctypes import wintypes

    identity = re.fullmatch(r"([0-9a-f]{32}):(0|[1-9][0-9]{0,5})", request["identity"])
    state = Path(request["state"])
    if identity is None or not state.is_absolute() or str(state).startswith("\\\\"):
        raise ValueError("Invalid managed original")
    path = state / "outputs" / identity[1] / f"final-{int(identity[2]) + 1}.png"
    for component in (path, *path.parents):
        if component.is_symlink() or os.path.isjunction(component):
            raise ValueError("Redirected original")
    if not (path.parent / "metadata.json").is_file():
        raise ValueError("Missing metadata")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    # DELETE | FILE_READ_ATTRIBUTES; share reads only, open the reparse point itself.
    handle = create(str(path), 0x10000 | 0x80, 1, None, 3, 0x00200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY)
    except BaseException:
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle(handle)
        raise
    try:
        info = os.fstat(descriptor)
        if (
            (info.st_size, info.st_mtime_ns, info.st_ino) !=
            (request["size"], request["mtime_ns"], request["inode"])
            or not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1 or getattr(info, "st_file_attributes", 0) & 0x400
        ):
            raise ValueError("Original identity changed")
        disposition = wintypes.BOOL(True)
        remove = kernel.SetFileInformationByHandle
        remove.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        remove.restype = wintypes.BOOL
        if not remove(handle, 4, ctypes.byref(disposition), ctypes.sizeof(disposition)):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        os.close(descriptor)


def _main():
    if os.name != "nt":
        return 1
    try:
        request = json.loads(sys.stdin.readline(65537))
        if request["action"] == "lock":
            import msvcrt

            if request["filename"] not in ("original_images.lock", "image_recycle_operation.lock"):
                raise ValueError
            state = Path(request["state"])
            if not state.is_absolute():
                raise ValueError
            state.mkdir(parents=True, exist_ok=True)
            with (state / request["filename"]).open("a+b") as handle:
                if handle.seek(0, os.SEEK_END) == 0:
                    handle.write(b"0")
                    handle.flush()
                deadline = time.monotonic() + min(max(float(request["timeout"]), 0), 30)
                while True:
                    try:
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise
                        time.sleep(0.05)
                print('{"ok":true}', flush=True)
                sys.stdin.readline()  # EOF on parent death also releases the OS lock.
        elif request["action"] == "delete":
            _delete(request)
            print('{"ok":true}', flush=True)
        else:
            raise ValueError
    except Exception:
        print('{"ok":false}', flush=True)  # Never print OS errors or private paths.
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
