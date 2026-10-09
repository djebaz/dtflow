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
    """Windows could not confirm a requested Recycle Bin operation."""


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


def _check(value: int, label: str) -> None:
    if value < 0:
        raise RecycleUnavailable(f"Windows Shell {label} failed (HRESULT 0x{value & 0xffffffff:08X}).")


def recycle_file(path: Path) -> None:
    """Move one regular local original to the Recycle Bin or fail closed.

    This call is only invoked after the shared service checks the source
    record, local volume and original ownership; it does not show DTFlow UI.
    """
    if os.name != "nt":
        raise RecycleUnavailable("Windows Recycle Bin is only supported on Windows.")
    source = Path(path)
    if not source.is_absolute() or not source.is_file() or source.is_symlink():
        raise RecycleUnavailable("The recorded original is not a regular local file.")
    root = source.anchor
    if not root or ctypes.windll.kernel32.GetDriveTypeW(ctypes.c_wchar_p(root)) != 3:
        raise RecycleUnavailable("The original is not on a local fixed drive.")
    ole32, shell32 = ctypes.OleDLL("ole32"), ctypes.OleDLL("shell32")
    # CoInitializeEx(NULL, COINIT_APARTMENTTHREADED): COM objects cannot be
    # shared with or operated from the GUI thread.
    ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    ole32.CoInitializeEx.restype = ctypes.c_long
    _check(ole32.CoInitializeEx(None, 2), "COM initialization")
    operation = ctypes.c_void_p()
    item = ctypes.c_void_p()
    try:
        clsid = _guid("3AD05575-8857-4850-9277-11B85BDB8E09")  # CLSID_FileOperation
        iid = _guid("947AAB5F-0A5C-4C13-B4D6-4EBF7836FC9F")  # IID_IFileOperation
        ole32.CoCreateInstance.argtypes = [
            ctypes.POINTER(_GUID), ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(_GUID),
            ctypes.POINTER(ctypes.c_void_p),
        ]
        ole32.CoCreateInstance.restype = ctypes.c_long
        _check(ole32.CoCreateInstance(ctypes.byref(clsid), None, 1, ctypes.byref(iid), ctypes.byref(operation)),
               "file operation creation")
        shell32.SHCreateItemFromParsingName.argtypes = [
            ctypes.c_wchar_p, ctypes.c_void_p, ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p),
        ]
        shell32.SHCreateItemFromParsingName.restype = ctypes.c_long
        shell_item = _guid("43826D1E-E718-42EE-BC55-A1E261C37BFE")  # IID_IShellItem
        _check(shell32.SHCreateItemFromParsingName(
            str(source), None, ctypes.byref(shell_item), ctypes.byref(item)
        ), "source selection")
        # FOFX_RECYCLEONDELETE: require Windows 8+ Recycle Bin handling.
        # DTFlow has already obtained an explicit user confirmation; suppress
        # duplicate Shell prompts and show operation errors through the app.
        # No FOF_ALLOWUNDO-only or SHFileOperation fallback is permitted.
        FOFX_RECYCLEONDELETE = 0x00080000
        FOF_NOCONFIRMATION = 0x0010
        FOF_SILENT = 0x0004
        FOF_NOERRORUI = 0x0400
        flags = FOFX_RECYCLEONDELETE | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI
        _check(_invoke(operation, 5, [ctypes.c_uint32], flags), "recycle flags")
        _check(_invoke(operation, 18, [ctypes.c_void_p, ctypes.c_void_p], item, None),
               "delete queue")
        _check(_invoke(operation, 21, []), "recycle execution")
        aborted = ctypes.c_int()
        _check(_invoke(operation, 22, [ctypes.POINTER(ctypes.c_int)], ctypes.byref(aborted)),
               "recycle completion")
        if aborted.value:
            raise RecycleUnavailable("Windows aborted the Recycle Bin operation.")
        if source.exists():
            raise RecycleUnavailable("Windows did not remove the original from its source location.")
    finally:
        if item.value:
            _invoke(item, 2, [])
        if operation.value:
            _invoke(operation, 2, [])
        ole32.CoUninitialize()
