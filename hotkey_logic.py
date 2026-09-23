"""
Weapon switch logic: listens to ONE global hotkey (combo of any complexity),
looks at the currently active weapon (via GsiState) and sends the target slot key.

Rules: list of pairs (if active A -> take B).
Each target slot has its own configured key (slot_keys).

Guards against false triggers:
1. Custom combo matching over held keys — fires even if unrelated keys
   are held at the moment (walking on W + pressing Q).
2. Focus: ignored outside cs2.exe / csgo.exe.
3. Round: enabled only after round.phase live/freezetime/warmup/over
   arrives from the game (not on the first random weapon packet).

Anti-spam: optimistic prediction — after firing we assume the target slot
is active until the game confirms (GSI runs at ~10 Hz). Fast doubles
alternate correctly instead of reading a stale active slot.

Missing-weapon fallback (configurable): if the rule target is not owned,
primary falls back to secondary, and secondary falls back to primary/knife
(user choice) — only if the replacement is actually owned. Matching always
uses the real active slot, so one press = one predictable outcome.

Keycodes, not layout: matching and sending is done by scan code (+extended flag)
whenever possible, so binds work in any keyboard layout.

Sending keys is plain keyboard simulation (like any macro/AutoHotkey bind),
NOT reading or writing the game process memory.
"""

import threading
import time

from gsi_server import LOGICAL_SLOTS, SLOT_IDS, GsiState
import scancodes
from scancodes import name_to_pair, pair_to_name, parse_combo_groups, combo_display
from native_hook import NativeKeyHook, KeyEvent

SLOT_KEYS_DEFAULT = {slot: LOGICAL_SLOTS[slot]["default_key"] for slot in SLOT_IDS}

DEFAULT_RULES = [
    {"active": "primary", "target": "knife"},
    {"active": "knife", "target": "primary"},
    {"active": "secondary", "target": "primary"},
]

ALLOWED_PROCESSES = {"cs2.exe", "csgo.exe"}

def _coerce_groups(groups) -> list[frozenset[tuple[int, bool]]]:
    """Ensure groups are proper frozensets of (scan, extended) pairs."""
    out = []
    for g in groups or []:
        cleaned = set()
        for item in g:
            if isinstance(item, dict) and "scan" in item and "extended" in item:
                cleaned.add((item["scan"], item["extended"]))
            elif isinstance(item, tuple) and len(item) == 2:
                cleaned.add(item)
        if cleaned:
            out.append(frozenset(cleaned))
    return out

def _foreground_process_name() -> str | None:
    """Foreground window exe name (lower) or None if undetermined."""
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return None
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if not handle:
            return None
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(1024)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                full = buf.value or ""
                base = full.replace("/", "\\").split("\\")[-1].lower()
                return base or None
            return None
        finally:
            kernel32.CloseHandle(handle)
    except Exception:  # noqa: BLE001
        return None


class HotkeyManager:
    def __init__(
        self,
        state: GsiState,
        log_callback,
        send_fn=None,
        debounce_ms: int = 35,
        focus_check: bool = True,
        foreground_fn=None,
        fallback_primary: bool = True,
        pistol_fallback: str = "primary",
        kill_callback=None,
    ):
        self.state = state
        self.log_callback = log_callback
        self._send_fn = send_fn
        self._kill_callback = kill_callback
        self._current_hotkey: str | None = None
        self._current_kill: str | None = None
        
        self._groups: list[frozenset[tuple[int, bool]]] = []
        self._kill_groups: list[frozenset[tuple[int, bool]]] = []
        
        self.rules: list[dict] = [dict(r) for r in DEFAULT_RULES]
        self.slot_keys: dict[str, str] = dict(SLOT_KEYS_DEFAULT)
        self.debounce_ms = max(debounce_ms, 100)
        self.focus_check = focus_check
        self.fallback_primary = fallback_primary
        self.pistol_fallback = pistol_fallback
        self._foreground_fn = foreground_fn or _foreground_process_name
        self._hook = NativeKeyHook()
        self._lock = threading.Lock()
        
        self._pressed_keys: set[tuple[int, bool]] = set()
        self._latched = False
        self._kill_latched = False
        self._last_fire = 0.0
        self._last_quiet_log = 0.0
        
        self._predicted: str | None = None
        self._predicted_base: str | None = None
        self._predicted_at = 0.0
        self._predict_ttl = 0.8

    def set_rules(self, rules: list[dict]):
        clean = []
        for r in rules:
            a, t = r.get("active"), r.get("target")
            if a in SLOT_IDS and t in SLOT_IDS and a != t:
                clean.append({"active": a, "target": t})
        self.rules = clean

    def set_slot_key(self, slot: str, key: str):
        if slot in SLOT_IDS:
            self.slot_keys[slot] = (key or "").strip()

    def _held_modifier_codes(self) -> list[tuple[int, bool]]:
        combo_codes = set()
        for g in self._groups:
            combo_codes.update(g)
        
        held_mods = []
        for key in self._pressed_keys:
            if key in combo_codes and scancodes.is_modifier(key[0], key[1]):
                held_mods.append(key)
        return sorted(held_mods)

    def _send(self, key: str):
        if self._send_fn is not None:
            self._send_fn(key)
            return
        with self._lock:
            mod_codes = self._held_modifier_codes()
        if mod_codes:
            for sc, ext in mod_codes:
                self._send_sc(sc, up=True, extended=ext)
            import time
            time.sleep(0.005)
        self.send_keycode(key)

    @staticmethod
    def _send_sc(code: int, up: bool = False, extended: bool = False):
        import ctypes
        from ctypes import wintypes

        INPUT_KEYBOARD = 1
        KEYEVENTF_SCANCODE = 0x0008
        KEYEVENTF_KEYUP = 0x0002
        KEYEVENTF_EXTENDEDKEY = 0x0001

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [
                ("wVk", wintypes.WORD),
                ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
            ]

        class INPUT(ctypes.Structure):
            class _INPUT(ctypes.Union):
                _fields_ = [("ki", KEYBDINPUT)]
            _fields_ = [("type", wintypes.DWORD), ("_input", _INPUT)]

        flags = KEYEVENTF_SCANCODE
        if up: flags |= KEYEVENTF_KEYUP
        if extended: flags |= KEYEVENTF_EXTENDEDKEY
        
        inp = INPUT(type=INPUT_KEYBOARD)
        inp._input.ki = KEYBDINPUT(wVk=0, wScan=code, dwFlags=flags, time=0, dwExtraInfo=None)
        ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))

    @staticmethod
    def send_keycode(key: str):
        parts = [scancodes.normalize_name(p) for p in key.replace(",", "+").split("+")]
        parts = [p for p in parts if p]
        if not parts:
            raise ValueError("empty key")
        
        def send_part(p, up=False):
            res = name_to_pair(p)
            if res:
                code, extended = res
                HotkeyManager._send_sc(code, up=up, extended=extended)
            else:
                import keyboard
                if up: keyboard.release(p)
                else: keyboard.press(p)
        
        mods, main = parts[:-1], parts[-1]
        for m in mods: send_part(m, False)
        send_part(main, False)
        send_part(main, True)
        for m in reversed(mods): send_part(m, True)

    def _quiet(self, msg: str, throttle_s: float = 3.0):
        now = time.time()
        if now - self._last_quiet_log >= throttle_s:
            self._last_quiet_log = now
            self.log_callback(msg)

    def _on_key_event(self, event: KeyEvent):
        key_pair = (event.scan_code, event.extended)
        fire_main = False
        fire_kill = False
        
        with self._lock:
            if event.event_type == "down":
                self._pressed_keys.add(key_pair)
            else:
                self._pressed_keys.discard(key_pair)
                if not self._held_locked(self._kill_groups):
                    self._kill_latched = False
                if not self._held_locked(self._groups):
                    self._latched = False
                return

            # check kill combo
            if (
                self._kill_groups
                and not self._kill_latched
                and self._held_locked(self._kill_groups)
                and any(key_pair in g for g in self._kill_groups)
            ):
                self._kill_latched = True
                fire_kill = True

            # check main combo
            if (
                self._groups
                and not self._latched
                and self._held_locked(self._groups)
                and any(key_pair in g for g in self._groups)
            ):
                self._latched = True
                fire_main = True

        if fire_kill and self._kill_callback is not None:
            try:
                self._kill_callback()
            except Exception:
                pass
        if fire_main:
            self._on_hotkey()

    def _held_locked(self, groups: list) -> bool:
        return bool(groups) and all(
            any(c in self._pressed_keys for c in g) for g in groups
        )

    def _effective_active(self, snap_active: str | None) -> str | None:
        if (
            self._predicted
            and (time.time() - self._predicted_at) < self._predict_ttl
            and snap_active == self._predicted_base
        ):
            return self._predicted
        return snap_active

    def _on_hotkey(self):
        now_ms = time.time() * 1000
        if now_ms - self._last_fire < self.debounce_ms:
            return
        self._last_fire = now_ms

        if self.focus_check:
            proc = None
            try:
                proc = self._foreground_fn()
            except Exception:
                proc = None
            if proc is not None and proc not in ALLOWED_PROCESSES:
                self._quiet(f"[hotkey] ignored: focus not in game ({proc})")
                return

        snap = self.state.snapshot()
        if not snap["connected"] or snap["active_slot"] is None:
            self._quiet("[hotkey] no game data yet — join a match")
            return
        if not snap.get("round_seen"):
            self._quiet("[hotkey] waiting for round start (no round.phase — reinstall GSI config)")
            return
        if not snap.get("armed"):
            self._quiet(f"[hotkey] round not live (phase={snap.get('round_phase')}) — waiting")
            return

        owned = snap.get("owned", [])
        active = self._effective_active(snap["active_slot"])

        target = None
        for r in self.rules:
            if r["active"] == active:
                target = r["target"]
                break

        if target is None:
            self.log_callback(f"[hotkey] active={active}: no rule — doing nothing")
            return

        real_target = target
        fb_note = ""
        if target == "primary" and "primary" not in owned:
            if self.fallback_primary and "secondary" in owned:
                real_target = "secondary"
                fb_note = " [fallback: no primary]"
        elif target == "secondary" and "secondary" not in owned:
            repl = self.pistol_fallback
            if repl in ("primary", "knife") and repl in owned:
                real_target = repl
                fb_note = " [fallback: no pistol]"

        key_to_send = (self.slot_keys.get(real_target) or "").strip()
        if not key_to_send:
            self.log_callback(f"[hotkey] active={active} -> {target}: no slot key set!")
            return

        owned_note = "" if real_target in owned else " (not in inventory?)"
        try:
            self._send(key_to_send)
        except Exception as e:
            self.log_callback(f"!! Failed to press '{key_to_send}': {e}")
            return
            
        self._predicted = real_target
        self._predicted_base = snap["active_slot"]
        self._predicted_at = time.time()
        self.log_callback(f"[hotkey] {active} -> {target} ('{key_to_send}'){fb_note}{owned_note}")

    def start(self, hotkey: str, kill_hotkey: str | None = None,
              hotkey_groups=None, kill_groups=None):
        self.stop()
        
        groups = _coerce_groups(hotkey_groups) if hotkey_groups else parse_combo_groups(hotkey)
        if not groups or not all(groups):
            self.log_callback(f"!! Cannot bind '{hotkey}': unknown key in combo")
            return False
            
        kill_display = (kill_hotkey or "").strip()
        if kill_display and parse_combo_groups(kill_display) == parse_combo_groups(hotkey):
            self.log_callback("!! Kill hotkey equals main hotkey — kill disabled")
            kill_display, kill_groups = "", None
            
        kgroups = _coerce_groups(kill_groups) if kill_groups else (
            parse_combo_groups(kill_display) if kill_display else [])
            
        if kill_display and (not kgroups or not all(kgroups)):
            self.log_callback(f"!! Cannot bind kill '{kill_display}': unknown key in combo")
            kill_display, kgroups = "", []
            
        try:
            with self._lock:
                self._pressed_keys = set()
                self._latched = False
                self._kill_latched = False
                self._groups = groups
                self._kill_groups = kgroups
                self._current_hotkey = hotkey.strip()
                self._current_kill = kill_display or None
                
            self._hook.start(self._on_key_event)
            
            self.log_callback(f"Listening hotkey: {self._current_hotkey}")
            if self._current_kill:
                self.log_callback(f"Listening kill hotkey: {self._current_kill}")
            return True
        except Exception as e:
            self.log_callback(f"!! Failed to bind '{hotkey}': {e}")
            return False

    def stop(self):
        self._hook.stop()
        with self._lock:
            self._groups = []
            self._kill_groups = []
            self._current_hotkey = None
            self._current_kill = None
            self._pressed_keys = set()
            self._latched = False
            self._kill_latched = False

    @property
    def current_hotkey(self):
        return self._current_hotkey

    @property
    def current_kill(self):
        return self._current_kill

    @property
    def main_groups(self):
        return [sorted(g) for g in self._groups]

    @property
    def kill_groups(self):
        return [sorted(g) for g in self._kill_groups]
