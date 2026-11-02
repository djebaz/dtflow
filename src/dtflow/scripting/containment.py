"""Make sure a script child cannot outlive the app: Linux parent-death signal, Windows kill-on-close Job Object."""

from __future__ import annotations

import signal
import sys

MAX_CHILD_ADDRESS_SPACE = None  # reserved: an OS-level memory cap can be added through the same Job Object


def die_with_parent() -> None:
    """Child side (Linux): receive SIGKILL when the parent goes away. No-op elsewhere."""
    if not sys.platform.startswith("linux"):
        return
    try:
        import ctypes

        ctypes.CDLL(None).prctl(1, int(signal.SIGKILL), 0, 0, 0)  # PR_SET_PDEATHSIG
    except (OSError, AttributeError):
        pass


def child_in_job() -> bool | None:
    """Child side (Windows): whether this process is in any Job Object. ``None`` elsewhere or when unknown.

    Evidence only: a job inherited from a launcher or shell also answers True."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
        result = wintypes.BOOL()
        if not kernel32.IsProcessInJob(kernel32.GetCurrentProcess(), None, ctypes.byref(result)):
            return None
        return bool(result.value)
    except Exception:
        return None


class Containment:
    """Parent side (Windows): a Job Object with KILL_ON_JOB_CLOSE holding the child; closing it kills the child."""

    def __init__(self, handle) -> None:
        self._handle = handle
        self.active = handle is not None

    def close(self) -> None:
        if self._handle is not None:
            import ctypes

            ctypes.WinDLL("kernel32").CloseHandle(self._handle)
            self._handle = None


def contain_child(process) -> Containment:
    """Assign the spawned child to a kill-on-close Job Object. Returns an inactive Containment when unavailable."""
    if sys.platform != "win32":
        return Containment(None)
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in ("r", "w", "o", "rb", "wb", "ob")]

        class BasicLimits(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimits),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return Containment(None)
        limits = ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = kernel32.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits))
        ok = ok and kernel32.AssignProcessToJobObject(job, wintypes.HANDLE(int(process._popen._handle)))
        if not ok:
            kernel32.CloseHandle(job)
            return Containment(None)
        return Containment(job)
    except Exception:  # containment is best effort and reported; it must never block a run
        return Containment(None)
