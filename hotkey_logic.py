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


def _codes_for_name(name: str) -> tuple:
    """Key name -> ALL scan codes (both sides for generic names), () if unknown."""
    try:
        import keyboard

        return tuple(keyboard.key_to_scan_codes(_norm_name(name)))
    except Exception:  # noqa: BLE001
        return ()


def parse_combo_groups(hotkey: str) -> list:
    """'Ctrl+Q' -> [frozenset(ctrl codes), frozenset(q codes)].

    Generic names ('ctrl') expand to every known code (either side works —
    legacy behavior). Side-specific names ('right ctrl') exclude the opposite
    side's codes, so typed combos can also be exact. Captures always store
    exact singletons (the only fully reliable source).
    """
    groups = []
    for p in (hotkey or "").lower().replace(",", "+").split("+"):
        p = p.strip()
        if not p:
            continue
        p = _RU_TO_EN.get(p, p)
        p = _ALIASES.get(p, p)
        codes = set(_codes_for_name(p))
        opposite = _OPPOSITE_SIDE.get(p)
        if opposite:
            trimmed = codes - set(_codes_for_name(opposite))
            if trimmed:
                codes = trimmed
        groups.append(frozenset(codes))
    return groups


# side-specific name -> opposite side name (tables list both, trim to exact)
_OPPOSITE_SIDE = {
    "left ctrl": "right ctrl",
    "right ctrl": "left ctrl",
    "left shift": "right shift",
    "right shift": "left shift",
    "left alt": "right alt",
    "right alt": "left alt",
    "alt gr": "left alt",
    "left windows": "right windows",
    "right windows": "left windows",
}


def _coerce_groups(groups) -> list:
    """[[29],[16]] or [(29,),(16,)] or [29,16-as-singletons...] -> [frozenset, ...].

    Each element is one combo key: an int (exact scan code) or an
    iterable of acceptable codes.
    """
    out = []
    for g in groups or []:
        if isinstance(g, int):
            out.append(frozenset({g}))
        else:
            out.append(frozenset(g))
    return out


# --- scan code <-> display name (side/numpad aware) ---

# specific names first so exact codes keep their precise label
_DISPLAY_CANDIDATES = [
    "left ctrl", "right ctrl", "left shift", "right shift",
    "left alt", "right alt", "left windows", "right windows",
    "alt gr",
    "esc", "tab", "caps lock", "space", "enter", "backspace",
    "insert", "home", "pageup", "delete", "end", "pagedown",
    "up", "down", "left", "right",
    "num lock", "num 0", "num 1", "num 2", "num 3", "num 4",
    "num 5", "num 6", "num 7", "num 8", "num 9",
    "num /", "num *", "num -", "num +", "num enter", "num del",
    "print screen", "scroll lock", "pause",
    "ctrl", "shift", "alt", "windows",
] + [chr(c) for c in range(ord("a"), ord("z") + 1)] + [str(d) for d in range(10)] + [
    f"f{i}" for i in range(1, 25)
]

_code_to_name: dict | None = None


def _reverse_map() -> dict:
    global _code_to_name
    if _code_to_name is None:
        rev = {}
        for name in _DISPLAY_CANDIDATES:
            for code in _codes_for_name(name):
                rev.setdefault(code, _norm_name(name))
        _code_to_name = rev
    return _code_to_name


def scancode_display(code: int) -> str:
    """Scan code -> human label ('right ctrl', 'q', ...), 'sc123' if unknown."""
    return _reverse_map().get(code, f"sc{code}")


def combo_display(codes) -> str:
    """[285, 16] -> 'right ctrl+q'."""
    return "+".join(scancode_display(c) for c in codes)


def _esc_codes() -> set:
    return set(_codes_for_name("esc"))


class _ComboRecorder:
    """Collects pressed scan codes in order until all are released.

    Split out for testability — feed it synthetic events without hardware.
    """

    def __init__(self):
        self.codes: list = []
        self.held: set = set()
        self.finished = False

    def on_event(self, event):
        sc = getattr(event, "scan_code", None)
        if not isinstance(sc, int):
            return
        if event.event_type == "down":
            if sc not in self.held:
                self.held.add(sc)
                if sc not in self.codes:
                    self.codes.append(sc)
        else:
            self.held.discard(sc)
            if self.codes and not self.held:
                self.finished = True


def capture_combo(timeout: float = 30.0):
    """Record a combo by scan code. Returns [codes] in press order,
    or None on timeout / empty / esc-only (cancel).

    Left/right modifiers and numpad keys are recorded exactly —
    'right ctrl' and 'left ctrl' are different binds.
    """
    import keyboard
    import time as _time

    rec = _ComboRecorder()
    h = keyboard.hook(rec.on_event, suppress=False)
    try:
        end = _time.time() + timeout
        while _time.time() < end and not rec.finished:
            _time.sleep(0.02)
    finally:
        try:
            keyboard.unhook(h)
        except (KeyError, ValueError, AttributeError):
            pass
    if not rec.finished or not rec.codes:
        return None
    if set(rec.codes) <= _esc_codes():
        return None  # esc-only = cancel
    return list(rec.codes)


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
        self._send_fn = send_fn  # for tests; default is scan-code send
        self._kill_callback = kill_callback  # fired by the kill combo, focus-independent
        self._current_hotkey: str | None = None
        self._current_kill: str | None = None
        # combo = list of groups; group = acceptable scan codes for ONE combo key.
        # Generic names expand to all codes (either side works), captures store
        # exact singletons (left/right/numpad are different binds).
        self._groups: list = []
        self._kill_groups: list = []
        self.rules: list[dict] = [dict(r) for r in DEFAULT_RULES]
        self.slot_keys: dict[str, str] = dict(SLOT_KEYS_DEFAULT)
        self.debounce_ms = debounce_ms
        self.focus_check = focus_check
        self.fallback_primary = fallback_primary
        self.pistol_fallback = pistol_fallback  # "primary" | "knife" | "nothing"
        self._foreground_fn = foreground_fn or _foreground_process_name
        self._hook = None
        self._lock = threading.Lock()
        self._pressed_sc: set = set()
        self._latched = False
        self._kill_latched = False
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
        every combo group needs >=1 of its codes pressed, not exact equality.
        The kill combo is checked first and ignores the focus gate."""
        sc = getattr(event, "scan_code", None)
        if not isinstance(sc, int):
            return
        fire_main = False
        fire_kill = False
        with self._lock:
            if event.event_type == "down":
                self._pressed_sc.add(sc)
            else:
                self._pressed_sc.discard(sc)
                if not self._held_locked(self._kill_groups):
                    self._kill_latched = False
                if not self._held_locked(self._groups):
                    self._latched = False
                return

            # edge: the just-pressed code must belong to the combo
            if (
                self._kill_groups
                and not self._kill_latched
                and self._held_locked(self._kill_groups)
                and any(sc in g for g in self._kill_groups)
            ):
                self._kill_latched = True
                fire_kill = True

            if (
                self._groups
                and not self._latched
                and self._held_locked(self._groups)
                and any(sc in g for g in self._groups)
            ):
                self._latched = True
                fire_main = True

        if fire_kill and self._kill_callback is not None:
            try:
                self._kill_callback()
            except Exception:  # noqa: BLE001
                pass
        if fire_main:
            self._on_hotkey()

    def _held_locked(self, groups: list) -> bool:
        return bool(groups) and all(
            any(c in self._pressed_sc for c in g) for g in groups
        )

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

    def start(self, hotkey: str, kill_hotkey: str | None = None,
              hotkey_groups=None, kill_groups=None):
        """Bind main + kill combos.

        hotkey/kill_hotkey: display strings ('ctrl+q', 'right ctrl+q').
        hotkey_groups/kill_groups: exact scan-code groups from capture_combo
        (e.g. [[29],[16]]) — when given, sides/numpad are exact; otherwise
        groups are parsed from the display string (generic names match
        either side — legacy behavior).
        """
        self.stop()
        groups = _coerce_groups(hotkey_groups) if hotkey_groups else parse_combo_groups(hotkey)
        if not groups or not all(groups):
            self.log_callback(f"!! Cannot bind '{hotkey}': unknown key in combo")
            return False
        kill_display = (kill_hotkey or "").strip()
        if kill_display and parse_combo(kill_display) == parse_combo(hotkey):
            self.log_callback("!! Kill hotkey equals main hotkey — kill disabled")
            kill_display, kill_groups = "", None
        kgroups = _coerce_groups(kill_groups) if kill_groups else (
            parse_combo_groups(kill_display) if kill_display else [])
        if kill_display and (not kgroups or not all(kgroups)):
            self.log_callback(f"!! Cannot bind kill '{kill_display}': unknown key in combo")
            kill_display, kgroups = "", []
        try:
            import keyboard

            with self._lock:
                self._pressed_sc = set()
                self._latched = False
                self._kill_latched = False
                self._groups = groups
                self._kill_groups = kgroups
                self._current_hotkey = hotkey.strip()
                self._current_kill = kill_display or None
                self._hook = keyboard.hook(self._on_key_event, suppress=False)
            self.log_callback(f"Listening hotkey: {self._current_hotkey}")
            if self._current_kill:
                self.log_callback(f"Listening kill hotkey: {self._current_kill}")
            return True
        except Exception as e:  # noqa: BLE001
            self.log_callback(f"!! Failed to bind '{hotkey}': {e}")
            return False

    def stop(self):
        with self._lock:
            hook, self._hook = self._hook, None
            self._groups = []
            self._kill_groups = []
            self._current_hotkey = None
            self._current_kill = None
            self._pressed_sc = set()
            self._latched = False
            self._kill_latched = False
        if hook is not None:
            try:
                import keyboard

                keyboard.unhook(hook)
            except (KeyError, ValueError, AttributeError):
                pass

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
