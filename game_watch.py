"""Detect whether the CS2 game process is running (Windows, stdlib only).

Used by the "exit with the game" option: the app quits itself shortly after
cs2.exe / csgo.exe disappears — but only if the game was seen running at
least once, so enabling the option while the game is closed never quits
the app immediately.

No third-party dependencies — Toolhelp32 process snapshot via ctypes.
"""

import ctypes
import os
from ctypes import wintypes

GAME_PROCESSES = ("cs2.exe", "csgo.exe")

_TH32CS_SNAPPROCESS = 0x00000002
_MAX_PATH = 260


class _ProcessEntry(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),  # ULONG_PTR
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * _MAX_PATH),
    ]


def _snapshot_names() -> list:
    """All running process exe names. Empty list on any failure / non-Windows."""
    if os.name != "nt":
        return []
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    except Exception:  # noqa: BLE001
        return []
    try:
        kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel32.Process32FirstW.restype = wintypes.BOOL
        kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry)]
        kernel32.Process32NextW.restype = wintypes.BOOL
        kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry)]
        snap = kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
        if not snap or snap == ctypes.c_void_p(-1).value:
            return []
        try:
            entry = _ProcessEntry()
            entry.dwSize = ctypes.sizeof(_ProcessEntry)
            names = []
            ok = kernel32.Process32FirstW(snap, ctypes.byref(entry))
            while ok:
                if entry.szExeFile:
                    names.append(entry.szExeFile)
                ok = kernel32.Process32NextW(snap, ctypes.byref(entry))
            return names
        finally:
            kernel32.CloseHandle(snap)
    except Exception:  # noqa: BLE001
        return []


def is_game_running(names=None) -> bool:
    """True if cs2.exe / csgo.exe is present (case-insensitive, any path).

    Pass an iterable of exe names/paths to test matching without OS access.
    """
    if names is None:
        try:
            names = _snapshot_names()
        except Exception:  # noqa: BLE001
            return False
    wanted = set(GAME_PROCESSES)
    for raw in names:
        base = (raw or "").replace("/", "\\").split("\\")[-1].lower()
        if base in wanted:
            return True
    return False
