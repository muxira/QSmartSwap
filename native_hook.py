"""
Native Windows keyboard hook using ctypes.
This module installs a WH_KEYBOARD_LL hook that correctly preserves
the extended key flag, which is essential for distinguishing numpad
keys from navigation keys (since they share the same base scan code).
"""

import ctypes
from ctypes import wintypes
import threading
import time
from typing import Callable
from dataclasses import dataclass

# --- Windows API Constants and Structures ---

WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105

LLKHF_EXTENDED = 0x01
LLKHF_INJECTED = 0x10

class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))
    ]

LowLevelKeyboardProc = ctypes.WINFUNCTYPE(
    wintypes.LPARAM, ctypes.c_int, wintypes.WPARAM, ctypes.POINTER(KBDLLHOOKSTRUCT)
)

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

HHOOK = wintypes.HANDLE

user32.SetWindowsHookExW.argtypes = (ctypes.c_int, LowLevelKeyboardProc, wintypes.HINSTANCE, wintypes.DWORD)
user32.SetWindowsHookExW.restype = HHOOK

user32.UnhookWindowsHookEx.argtypes = (HHOOK,)
user32.UnhookWindowsHookEx.restype = wintypes.BOOL

user32.CallNextHookEx.argtypes = (HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
user32.CallNextHookEx.restype = wintypes.LPARAM

kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE

user32.PostThreadMessageW.argtypes = (wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
user32.PostThreadMessageW.restype = wintypes.BOOL

@dataclass
class KeyEvent:
    scan_code: int
    extended: bool
    event_type: str  # "down" or "up"
    vk: int

class NativeKeyHook:
    def __init__(self):
        self._hook_id = None
        self._thread = None
        self._stop_event = threading.Event()
        self._callback = None

    def _hook_proc(self, nCode, wParam, lParam):
        if nCode >= 0 and self._callback:
            try:
                info = lParam.contents
                
                # Ignore injected events to prevent feedback loops if we simulate keys
                if not (info.flags & LLKHF_INJECTED):
                    event_type = "down" if wParam in (WM_KEYDOWN, WM_SYSKEYDOWN) else "up"
                    extended = bool(info.flags & LLKHF_EXTENDED)
                    
                    event = KeyEvent(
                        scan_code=info.scanCode,
                        extended=extended,
                        event_type=event_type,
                        vk=info.vkCode
                    )
                    self._callback(event)
            except Exception as e:
                print(f"Error in keyboard hook: {e}")

        # Always call next hook to not block system events
        # We extract the pointer's address as an integer for CallNextHookEx
        lparam_val = ctypes.cast(lParam, ctypes.c_void_p).value or 0
        return user32.CallNextHookEx(self._hook_id, nCode, wParam, lparam_val)

    def _message_loop(self):
        # We need to keep a reference to the C callback so it's not garbage collected
        self._c_callback = LowLevelKeyboardProc(self._hook_proc)
        
        h_mod = kernel32.GetModuleHandleW(None)
        
        self._hook_id = user32.SetWindowsHookExW(
            WH_KEYBOARD_LL,
            self._c_callback,
            h_mod,
            0
        )
        
        if not self._hook_id:
            err = ctypes.GetLastError()
            print(f"Failed to install keyboard hook. Error code: {err}")
            return

        msg = wintypes.MSG()
        # Message pump is required for hooks to work
        while not self._stop_event.is_set():
            # PeekMessage does not block, allowing us to check the stop event
            if user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1): # PM_REMOVE = 1
                if msg.message == 0x0012: # WM_QUIT
                    break
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            else:
                time.sleep(0.01)

        user32.UnhookWindowsHookEx(self._hook_id)
        self._hook_id = None

    def start(self, callback: Callable[[KeyEvent], None]) -> bool:
        """Start the keyboard hook in a background thread."""
        self.stop()
        self._callback = callback
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._message_loop, daemon=True)
        self._thread.start()
        
        # Wait briefly to let the hook install
        time.sleep(0.05)
        return self._thread.is_alive()

    def stop(self) -> None:
        """Stop the keyboard hook."""
        self._stop_event.set()
        # Post WM_QUIT to the message loop thread so PeekMessage unblocks
        if self._thread and self._thread.is_alive() and self._thread.ident:
            user32.PostThreadMessageW(self._thread.ident, 0x0012, 0, 0)  # WM_QUIT
            self._thread.join(timeout=2.0)
        self._thread = None
        self._callback = None


class ComboRecorder:
    def __init__(self):
        self.keys: list[tuple[int, bool]] = []
        self.held: set[tuple[int, bool]] = set()
        self.finished = False

    def on_event(self, event: KeyEvent) -> None:
        if self.finished:
            return
            
        key_pair = (event.scan_code, event.extended)
        
        if event.event_type == "down":
            if key_pair not in self.held:
                self.held.add(key_pair)
                if key_pair not in self.keys:
                    self.keys.append(key_pair)
        else:
            self.held.discard(key_pair)
            if self.keys and not self.held:
                self.finished = True

def capture_combo(timeout: float = 30.0) -> list[tuple[int, bool]] | None:
    """
    Records a key combination using the native hook.
    Returns a list of (scan, extended) pairs in press order,
    or None on timeout / empty / escape-only.
    """
    recorder = ComboRecorder()
    hook = NativeKeyHook()
    
    if not hook.start(recorder.on_event):
        return None

    try:
        end = time.time() + timeout
        while time.time() < end and not recorder.finished:
            time.sleep(0.02)
    finally:
        hook.stop()

    if not recorder.finished or not recorder.keys:
        return None
        
    # Check if escape was the only key pressed (code 0x01, not extended)
    if set(recorder.keys) == {(0x01, False)}:
        return None
        
    return list(recorder.keys)
