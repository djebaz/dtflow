"""Dev-only memory observations through the existing structured diagnostics sink.

OS counters describe the whole process, not allocations attributable to a node.
Peak counters are process-lifetime high-water marks, never per-operation peaks.
No heap walks, tensor copies, GC, allocator changes or model eviction happen here.
"""

import logging
import sys
from contextlib import contextmanager
from functools import cache
from pathlib import Path
from threading import get_ident
from time import monotonic

from dtflow.diagnostics import measurement
from dtflow.logging_setup import development_mode


def enabled(logger: logging.Logger) -> bool:
    return development_mode() and logger.isEnabledFor(logging.DEBUG)


@cache
def _windows_reader():
    """Bind pointer-sized Win32 counters lazily, only after explicit dev admission."""
    import ctypes
    from ctypes import wintypes

    class ProcessMemory(ctypes.Structure):
        # PROCESS_MEMORY_COUNTERS_EX; PrivateUsage is private commit, not private RSS.
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            *[
                (name, ctypes.c_size_t)
                for name in (
                    "PeakWorkingSetSize",
                    "WorkingSetSize",
                    "QuotaPeakPagedPoolUsage",
                    "QuotaPagedPoolUsage",
                    "QuotaPeakNonPagedPoolUsage",
                    "QuotaNonPagedPoolUsage",
                    "PagefileUsage",
                    "PeakPagefileUsage",
                    "PrivateUsage",
                )
            ],
        ]

    class SystemMemory(ctypes.Structure):
        # MEMORYSTATUSEX; only physical total/available are projected below.
        _fields_ = [
            ("dwLength", wintypes.DWORD),
            ("dwMemoryLoad", wintypes.DWORD),
            *[
                (name, ctypes.c_ulonglong)
                for name in (
                    "ullTotalPhys",
                    "ullAvailPhys",
                    "ullTotalPageFile",
                    "ullAvailPageFile",
                    "ullTotalVirtual",
                    "ullAvailVirtual",
                    "ullAvailExtendedVirtual",
                )
            ],
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    current = kernel.GetCurrentProcess
    current.argtypes = []
    current.restype = wintypes.HANDLE
    process_info = kernel.K32GetProcessMemoryInfo
    process_info.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessMemory), wintypes.DWORD]
    process_info.restype = wintypes.BOOL
    system_info = kernel.GlobalMemoryStatusEx
    system_info.argtypes = [ctypes.POINTER(SystemMemory)]
    system_info.restype = wintypes.BOOL

    def read() -> dict:
        process = ProcessMemory()
        process.cb = ctypes.sizeof(process)
        system = SystemMemory()
        system.dwLength = ctypes.sizeof(system)
        process_ok = bool(process_info(current(), ctypes.byref(process), process.cb))
        system_ok = bool(system_info(ctypes.byref(system)))
        data = {
            "memory_source": "windows_process_counters",
            "memory_status": "available" if process_ok else "unavailable",
            "system_memory_status": "available" if system_ok else "unavailable",
        }
        if process_ok:
            data.update(
                process_rss_bytes=process.WorkingSetSize,
                process_peak_rss_bytes=process.PeakWorkingSetSize,
                process_private_commit_bytes=process.PrivateUsage,
                process_peak_private_commit_bytes=process.PeakPagefileUsage,
            )
        if system_ok:
            data.update(system_total_bytes=system.ullTotalPhys, system_available_bytes=system.ullAvailPhys)
        return data

    return read


def _proc_fields(path: str, names: dict[str, str]) -> dict:
    # Bounded reads of kernel counters only; do not retain unrelated /proc content.
    with Path(path).open("r", encoding="ascii") as handle:
        text = handle.read(16384)
    found = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[0] in names and parts[2] == "kB":
            value = int(parts[1])
            if value >= 0:
                found[names[parts[0]]] = value * 1024
    return found


def _read_memory() -> dict:
    if sys.platform == "win32":
        return _windows_reader()()
    if sys.platform.startswith("linux"):
        process = _proc_fields("/proc/self/status", {"VmRSS:": "process_rss_bytes", "VmHWM:": "process_peak_rss_bytes"})
        data = {
            "memory_source": "linux_proc",
            "memory_status": "available" if "process_rss_bytes" in process else "unavailable",
            **process,
        }
        try:
            system = _proc_fields(
                "/proc/meminfo", {"MemTotal:": "system_total_bytes", "MemAvailable:": "system_available_bytes"}
            )
        except (OSError, ValueError):
            system = {}
        data.update(system, system_memory_status="available" if "system_available_bytes" in system else "unavailable")
        return data
    return {"memory_source": "unsupported", "memory_status": "unavailable", "system_memory_status": "unavailable"}


def observe(logger: logging.Logger, event: str, **data) -> None:
    """Sample on a worker/runner thread; unavailable counters are absent, never zero."""
    if not enabled(logger):
        return
    try:
        try:
            counters = _read_memory()
        except Exception:
            counters = {"memory_status": "unavailable", "system_memory_status": "unavailable"}
        measurement(logger, event, thread_id=get_ident(), **data, **counters)
    except Exception:
        # Diagnostics must not change an operation's result, exception or retry path.
        pass


@contextmanager
def scope(logger: logging.Logger, event: str, **data):
    """Paired checkpoints; optional output counters must be scalar safe projections."""
    if not enabled(logger):
        yield None
        return
    started = monotonic()
    observe(logger, event, phase="before", **data)
    metrics = {}
    succeeded = False
    try:
        yield metrics
        succeeded = True
    finally:
        observe(
            logger,
            event,
            phase="after",
            success=succeeded,
            elapsed_ms=(monotonic() - started) * 1000,
            **data,
            **metrics,
        )
