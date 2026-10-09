"""Recycle a single file with the Windows Shell IFileOperation COM API.

FOFX_RECYCLEONDELETE asks Windows 8+ explicitly to recycle, not permanently
delete. There is no os.remove/unlink or SHFileOperation fallback. This module
runs in a single-threaded COM apartment off the Qt GUI thread.
"""

from __future__ import annotations

import ctypes
import os
import uuid
from pathlib import Path


class RecycleUnavailable(RuntimeError):
    """A sanitized Windows Shell failure, with no source path or private data."""

    def __init__(self, message: str, *, stage: str = "unknown", hresult: int | None = None):
        super().__init__(message)
        self.stage = stage
        self.hresult = hresult

    @property
    def diagnostic(self) -> str:
        """Stable, path-free explanation for the operation result and application log."""
        if self.hresult is not None:
            return f"{self.stage} (HRESULT 0x{self.hresult & 0xFFFFFFFF:08X})"
        return self.stage


class _GUID(ctypes.Structure):
    _fields_ = [
        ("data1", ctypes.c_uint32),
        ("data2", ctypes.c_uint16),
        ("data3", ctypes.c_uint16),
        ("data4", ctypes.c_ubyte * 8),
    ]


def _guid(value: str) -> _GUID:
    return _GUID.from_buffer_copy(uuid.UUID(value).bytes_le)


def _invoke(instance, index, signature, *arguments):
    vtable = ctypes.cast(instance, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    method = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *signature)(vtable[index])
    return method(instance, *arguments)


def _check(value: int, label: str, stage: str) -> None:
    if value < 0:
        raise RecycleUnavailable(
            f"Windows Shell {label} failed (HRESULT 0x{value & 0xFFFFFFFF:08X}).",
            stage=stage,
            hresult=value,
        )


def recycle_file(path: Path) -> None:
    """Move one regular local original to the Recycle Bin or fail closed.

    This call is only invoked after the shared service checks the source
    record, local volume and original ownership; it does not show DTFlow UI.
    """
    if os.name != "nt":
        raise RecycleUnavailable("Windows Recycle Bin is only supported on Windows.")
    source = Path(path)
    if not source.is_absolute() or not source.is_file() or source.is_symlink():
        raise RecycleUnavailable(
            "The recorded original is not a regular local file.",
            stage="source_not_regular",
        )
    root = source.anchor
    if not root:
        raise RecycleUnavailable("The original has no local drive.", stage="source_no_drive")
    drive_type = ctypes.windll.kernel32.GetDriveTypeW(ctypes.c_wchar_p(root))
    if drive_type != 3:
        raise RecycleUnavailable(
            "The original is not on a local fixed drive.",
            stage=f"drive_type_{drive_type}",
        )
    # WinDLL returns the raw HRESULT for explicit stage-aware _check calls.
    # OleDLL automatically converts failed HRESULTs to OSError and loses the
    # precise COM operation stage needed for safe diagnostics.
    ole32, shell32 = ctypes.WinDLL("ole32"), ctypes.WinDLL("shell32")
    # CoInitializeEx(NULL, COINIT_APARTMENTTHREADED): COM objects cannot be
    # shared with or operated from the GUI thread.
    ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    ole32.CoInitializeEx.restype = ctypes.c_long
    _check(ole32.CoInitializeEx(None, 2), "COM initialization", "com_initialize")
    operation = ctypes.c_void_p()
    item = ctypes.c_void_p()
    try:
        clsid = _guid("3AD05575-8857-4850-9277-11B85BDB8E09")  # CLSID_FileOperation
        iid = _guid("947AAB5F-0A5C-4C13-B4D6-4BF7836FC9F8")  # IID_IFileOperation
        ole32.CoCreateInstance.argtypes = [
            ctypes.POINTER(_GUID), ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(_GUID),
            ctypes.POINTER(ctypes.c_void_p),
        ]
        ole32.CoCreateInstance.restype = ctypes.c_long
        _check(ole32.CoCreateInstance(ctypes.byref(clsid), None, 1, ctypes.byref(iid), ctypes.byref(operation)),
               "file operation creation", "operation_create")
        shell32.SHCreateItemFromParsingName.argtypes = [
            ctypes.c_wchar_p, ctypes.c_void_p, ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p),
        ]
        shell32.SHCreateItemFromParsingName.restype = ctypes.c_long
        shell_item = _guid("43826D1E-E718-42EE-BC55-A1E261C37BFE")  # IID_IShellItem
        _check(shell32.SHCreateItemFromParsingName(
            str(source), None, ctypes.byref(shell_item), ctypes.byref(item)
        ), "source selection", "source_parse")
        # FOFX_RECYCLEONDELETE: require Windows 8+ Recycle Bin handling.
        # DTFlow has already obtained an explicit user confirmation; suppress
        # duplicate Shell prompts and show operation errors through the app.
        # No FOF_ALLOWUNDO-only or SHFileOperation fallback is permitted.
        FOFX_RECYCLEONDELETE = 0x00080000
        FOF_NOCONFIRMATION = 0x0010
        FOF_SILENT = 0x0004
        FOF_NOERRORUI = 0x0400
        flags = FOFX_RECYCLEONDELETE | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI
        _check(_invoke(operation, 5, [ctypes.c_uint32], flags), "recycle flags", "set_flags")
        _check(_invoke(operation, 18, [ctypes.c_void_p, ctypes.c_void_p], item, None),
               "delete queue", "queue_delete")
        performed = _invoke(operation, 21, [])
        aborted = ctypes.c_int()
        completed = _invoke(operation, 22, [ctypes.POINTER(ctypes.c_int)], ctypes.byref(aborted))
        # Microsoft recommends reading the aborted flag even after
        # PerformOperations reports a failed HRESULT.
        if completed >= 0 and aborted.value:
            raise RecycleUnavailable("Windows aborted the Recycle Bin operation.", stage="shell_aborted")
        _check(performed, "recycle execution", "perform_operations")
        _check(completed, "recycle completion", "operation_status")
        if source.exists():
            raise RecycleUnavailable(
                "Windows did not remove the original from its source location.",
                stage="source_still_present",
            )
    finally:
        if item.value:
            _invoke(item, 2, [])
        if operation.value:
            _invoke(operation, 2, [])
        ole32.CoUninitialize()
