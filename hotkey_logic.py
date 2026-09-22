"""
Weapon switch logic: listens to ONE global hotkey (combo of any complexity:
"q", "ctrl+q", "ctrl+shift+x", ...), looks at the currently active weapon
(via GsiState) and sends the target slot key.

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

Keycodes, not layout: matching and sending is done by scan code whenever
possible, so binds work in any keyboard layout (q == й). Rule matching
additionally normalizes RU layout names to latin.

Sending keys is plain keyboard simulation (like any macro/AutoHotkey bind),
NOT reading or writing the game process memory.

NOTE: console/log output of this module is ALWAYS English, regardless of
the application UI language.
"""

import threading
import time

from gsi_server import LOGICAL_SLOTS, SLOT_IDS, GsiState

SLOT_KEYS_DEFAULT = {slot: LOGICAL_SLOTS[slot]["default_key"] for slot in SLOT_IDS}

DEFAULT_RULES = [
    {"active": "primary", "target": "knife"},
    {"active": "knife", "target": "primary"},
    {"active": "secondary", "target": "primary"},
]

ALLOWED_PROCESSES = {"cs2.exe", "csgo.exe"}

_ALIASES = {
    "control": "ctrl",
    "lcontrol": "ctrl",
    "rcontrol": "ctrl",
    "lctrl": "ctrl",
    "rctrl": "ctrl",
    "lshift": "shift",
    "rshift": "shift",
    "lmenu": "alt",
    "rmenu": "alt",
    "lalt": "alt",
    "ralt": "alt",
    "option": "alt",
    "cmd": "windows",
    "command": "windows",
    "win": "windows",
    "lwin": "windows",
    "rwin": "windows",
    "super": "windows",
    "return": "enter",
    "esc": "esc",
}

# RU physical layout -> latin (qwerty position equivalents), so a bind made
# on latin works while RU layout is active and vice versa.
_RU_TO_EN = {
    "й": "q", "ц": "w", "у": "e", "к": "r", "е": "t", "н": "y",
    "г": "u", "ш": "i", "щ": "o", "з": "p", "х": "[", "ъ": "]",
    "ф": "a", "ы": "s", "в": "d", "а": "f", "п": "g", "р": "h",
    "о": "j", "л": "k", "д": "l", "э": "'", "я": "z", "ч": "x",
    "с": "c", "м": "v", "и": "b", "т": "n", "ь": "m", "б": ",",
    "ю": ".", "ё": "`",
}


def _norm_name(name: str) -> str:
    n = (name or "").strip().lower()
    n = _RU_TO_EN.get(n, n)
    return _ALIASES.get(n, n)


def parse_combo(hotkey: str) -> frozenset:
    """'Ctrl+Shift+Q' -> frozenset({'ctrl','shift','q'}). Layout-independent."""
    parts = []
    for p in (hotkey or "").lower().replace(",", "+").split("+"):
        p = p.strip()
        if not p:
            continue
        p = _RU_TO_EN.get(p, p)
        parts.append(_ALIASES.get(p, p))
    return frozenset(parts)


def _name_to_scancode(name: str):
    """Key name -> first scan code, or None if unresolvable."""
    try:
        import keyboard

        codes = keyboard.key_to_scan_codes(_norm_name(name))
        return codes[0] if codes else None
    except Exception:  # noqa: BLE001
        return None


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
    ):
        self.state = state
        self.log_callback = log_callback
        self._send_fn = send_fn  # for tests; default is scan-code send
        self._current_hotkey: str | None = None
        self._required: frozenset = frozenset()
        self._required_sc: frozenset = frozenset()
        self.rules: list[dict] = [dict(r) for r in DEFAULT_RULES]
        self.slot_keys: dict[str, str] = dict(SLOT_KEYS_DEFAULT)
        self.debounce_ms = debounce_ms
        self.focus_check = focus_check
        self.fallback_primary = fallback_primary
        self.pistol_fallback = pistol_fallback  # "primary" | "knife" | "nothing"
        self._foreground_fn = foreground_fn or _foreground_process_name
        self._hook = None
        self._lock = threading.Lock()
        self._pressed: set[str] = set()
        self._pressed_sc: set[int] = set()
        self._latched = False
        self._last_fire = 0.0
        self._last_quiet_log = 0.0
        # optimistic prediction for spam
        self._predicted: str | None = None
        self._predicted_base: str | None = None
        self._predicted_at = 0.0
        self._predict_ttl = 0.8

    # --- settings ---

    def set_rules(self, rules: list[dict]):
        """rules: [{'active': ..., 'target': ...}, ...] — garbage is dropped."""
        clean = []
        for r in rules:
            a, t = r.get("active"), r.get("target")
            if a in SLOT_IDS and t in SLOT_IDS and a != t:
                clean.append({"active": a, "target": t})
        self.rules = clean

    def set_slot_key(self, slot: str, key: str):
        if slot in SLOT_IDS:
            self.slot_keys[slot] = (key or "").strip()

    def _send(self, key: str):
        if self._send_fn is not None:
            self._send_fn(key)
            return
        self.send_keycode(key)

    @staticmethod
    def send_keycode(key: str):
        """Send a key/combo by scan code (layout-independent), fallback to string."""
        import keyboard

        parts = [_norm_name(p) for p in key.replace(",", "+").split("+")]
        parts = [p for p in parts if p]
        if not parts:
            raise ValueError("empty key")
        codes = [_name_to_scancode(p) for p in parts]
        if all(c is not None for c in codes):
            if len(codes) == 1:
                keyboard.press_and_release(codes[0])
            else:
                mods, main = codes[:-1], codes[-1]
                for m in mods:
                    keyboard.press(m)
                try:
                    keyboard.press_and_release(main)
                finally:
                    for m in reversed(mods):
                        try:
                            keyboard.release(m)
                        except Exception:  # noqa: BLE001
                            pass
        else:
            keyboard.press_and_release(key)

    def _quiet(self, msg: str, throttle_s: float = 3.0):
        now = time.time()
        if now - self._last_quiet_log >= throttle_s:
            self._last_quiet_log = now
            self.log_callback(msg)

    # --- combo matching ---

    def _on_key_event(self, event):
        """keyboard.hook callback. Fires with unrelated keys held too:
        requires required ⊆ pressed, not exact equality. Names OR scan codes."""
        name = _norm_name(getattr(event, "name", "") or "")
        sc = getattr(event, "scan_code", None)
        if not name and sc is None:
            return
        with self._lock:
            if event.event_type == "down":
                if name:
                    self._pressed.add(name)
                if isinstance(sc, int):
                    self._pressed_sc.add(sc)
            else:
                if name:
                    self._pressed.discard(name)
                if isinstance(sc, int):
                    self._pressed_sc.discard(sc)
                if not self._combo_held_locked():
                    self._latched = False
                return

            if self._latched or not self._combo_held_locked():
                return
            # edge: the just-pressed key must belong to the combo
            name_ok = bool(name) and name in self._required
            sc_ok = isinstance(sc, int) and sc in self._required_sc
            if not (name_ok or sc_ok):
                return
            self._latched = True
            self._on_hotkey()

    def _combo_held_locked(self) -> bool:
        if self._required and self._required.issubset(self._pressed):
            return True
        if self._required_sc and self._required_sc.issubset(self._pressed_sc):
            return True
        return False

    def _effective_active(self, snap_active: str | None) -> str | None:
        """Active slot with prediction applied (for spam faster than 10 Hz GSI)."""
        if (
            self._predicted
            and (time.time() - self._predicted_at) < self._predict_ttl
            and snap_active == self._predicted_base
        ):
            return self._predicted
        return snap_active

    # --- firing ---

    def _on_hotkey(self):
        now_ms = time.time() * 1000
        if now_ms - self._last_fire < self.debounce_ms:
            return
        self._last_fire = now_ms

        if self.focus_check:
            proc = None
            try:
                proc = self._foreground_fn()
            except Exception:  # noqa: BLE001
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

        # missing-weapon fallback: redirect the TARGET, never the matching.
        # Matching always uses the real active slot, so one press = one
        # predictable outcome (no double-clicking).
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
        except Exception as e:  # noqa: BLE001
            self.log_callback(f"!! Failed to press '{key_to_send}': {e}")
            return
        # prediction: assume we now hold the real target until GSI confirms
        self._predicted = real_target
        self._predicted_base = snap["active_slot"]
        self._predicted_at = time.time()
        self.log_callback(f"[hotkey] {active} -> {target} ('{key_to_send}'){fb_note}{owned_note}")

    def start(self, hotkey: str):
        self.stop()
        required = parse_combo(hotkey)
        if not required:
            self.log_callback("!! Empty hotkey")
            return False
        try:
            import keyboard

            sc = set()
            for p in required:
                code = _name_to_scancode(p)
                if code is not None:
                    sc.add(code)
            with self._lock:
                self._pressed = set()
                self._pressed_sc = set()
                self._latched = False
                self._required = required
                self._required_sc = frozenset(sc)
                self._current_hotkey = hotkey.strip()
                self._hook = keyboard.hook(self._on_key_event, suppress=False)
            self.log_callback(f"Listening hotkey: {self._current_hotkey}")
            return True
        except Exception as e:  # noqa: BLE001
            self.log_callback(f"!! Failed to bind '{hotkey}': {e}")
            return False

    def stop(self):
        with self._lock:
            hook, self._hook = self._hook, None
            self._required = frozenset()
            self._required_sc = frozenset()
            self._current_hotkey = None
            self._pressed = set()
            self._pressed_sc = set()
            self._latched = False
        if hook is not None:
            try:
                import keyboard

                keyboard.unhook(hook)
            except (KeyError, ValueError, AttributeError):
                pass

    @property
    def current_hotkey(self):
        return self._current_hotkey
