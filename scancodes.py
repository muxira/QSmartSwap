"""
Unified scan-code registry for QSmartSwap.

Every physical key is identified by its Set 1 scan code + extended flag.
This module is the SINGLE SOURCE OF TRUTH for key identification:
  - SCANCODE_TABLE: (scan, extended) -> canonical name
  - NAME_TO_SCANCODE: canonical name -> (scan, extended)
  - scancode_display(): human-readable localized name
  - name_to_pair(): name -> (scan, extended) with alias resolution

CRITICAL: keys like Home/Num7, Enter/NumpadEnter, LCtrl/RCtrl share
the same base scan code and are distinguished ONLY by the extended flag.
Never use a bare scan code as a unique key identifier.

Fn is deliberately absent — it is handled by the keyboard's embedded
controller and never reaches the OS.
"""

from __future__ import annotations

# ── Complete scan code table ─────────────────────────────────────────
# Key: (scan_code, extended) -> canonical internal name (lowercase ASCII).
# Names are chosen to be unambiguous: "left ctrl" vs "right ctrl",
# "num 7" vs "7", "num enter" vs "enter", etc.

SCANCODE_TABLE: dict[tuple[int, bool], str] = {
    # --- Letters (A-Z) ---
    (0x1E, False): "a",   (0x30, False): "b",   (0x2E, False): "c",
    (0x20, False): "d",   (0x12, False): "e",   (0x21, False): "f",
    (0x22, False): "g",   (0x23, False): "h",   (0x17, False): "i",
    (0x24, False): "j",   (0x25, False): "k",   (0x26, False): "l",
    (0x32, False): "m",   (0x31, False): "n",   (0x18, False): "o",
    (0x19, False): "p",   (0x10, False): "q",   (0x13, False): "r",
    (0x1F, False): "s",   (0x14, False): "t",   (0x16, False): "u",
    (0x2F, False): "v",   (0x11, False): "w",   (0x2D, False): "x",
    (0x15, False): "y",   (0x2C, False): "z",

    # --- Top-row digits ---
    (0x02, False): "1",   (0x03, False): "2",   (0x04, False): "3",
    (0x05, False): "4",   (0x06, False): "5",   (0x07, False): "6",
    (0x08, False): "7",   (0x09, False): "8",   (0x0A, False): "9",
    (0x0B, False): "0",

    # --- Function keys ---
    (0x3B, False): "f1",  (0x3C, False): "f2",  (0x3D, False): "f3",
    (0x3E, False): "f4",  (0x3F, False): "f5",  (0x40, False): "f6",
    (0x41, False): "f7",  (0x42, False): "f8",  (0x43, False): "f9",
    (0x44, False): "f10", (0x57, False): "f11", (0x58, False): "f12",
    (0x64, False): "f13", (0x65, False): "f14", (0x66, False): "f15",
    (0x67, False): "f16", (0x68, False): "f17", (0x69, False): "f18",
    (0x6A, False): "f19", (0x6B, False): "f20", (0x6C, False): "f21",
    (0x6D, False): "f22", (0x6E, False): "f23", (0x76, False): "f24",

    # --- Modifiers (left/right are DISTINCT) ---
    (0x1D, False): "left ctrl",    (0x1D, True): "right ctrl",
    (0x2A, False): "left shift",   (0x36, False): "right shift",
    (0x38, False): "left alt",     (0x38, True): "right alt",
    (0x5B, True):  "left windows", (0x5C, True): "right windows",

    # --- Utility keys ---
    (0x01, False): "escape",
    (0x0E, False): "backspace",
    (0x0F, False): "tab",
    (0x1C, False): "enter",
    (0x39, False): "space",

    # --- Locks ---
    (0x3A, False): "caps lock",
    (0x45, True):  "num lock",
    (0x46, False): "scroll lock",

    # --- Special ---
    (0x37, True):  "print screen",
    (0x45, False): "pause",
    (0x5D, True):  "menu",        # Context Menu / Apps key

    # --- Symbols ---
    (0x0C, False): "-",    (0x0D, False): "=",
    (0x1A, False): "[",    (0x1B, False): "]",
    (0x27, False): ";",    (0x28, False): "'",
    (0x29, False): "`",    (0x2B, False): "\\",
    (0x33, False): ",",    (0x34, False): ".",
    (0x35, False): "/",

    # --- Navigation cluster (all EXTENDED) ---
    (0x52, True): "insert",    (0x53, True): "delete",
    (0x47, True): "home",      (0x4F, True): "end",
    (0x49, True): "page up",   (0x51, True): "page down",

    # --- Arrow keys (all EXTENDED) ---
    (0x48, True): "up",    (0x50, True): "down",
    (0x4B, True): "left",  (0x4D, True): "right",

    # --- Numpad (all NON-EXTENDED) ---
    (0x52, False): "num 0",  (0x4F, False): "num 1",  (0x50, False): "num 2",
    (0x51, False): "num 3",  (0x4B, False): "num 4",  (0x4C, False): "num 5",
    (0x4D, False): "num 6",  (0x47, False): "num 7",  (0x48, False): "num 8",
    (0x49, False): "num 9",
    (0x37, False): "num *",  (0x4A, False): "num -",  (0x4E, False): "num +",
    (0x53, False): "num .",
    (0x1C, True):  "num enter",
    (0x35, True):  "num /",

    # --- Multimedia / Browser (all EXTENDED, E0-prefixed scan codes) ---
    (0x20, True): "volume mute",
    (0x2E, True): "volume down",
    (0x30, True): "volume up",
    (0x19, True): "media next",
    (0x10, True): "media previous",
    (0x24, True): "media stop",
    (0x22, True): "media play/pause",
    (0x6A, True): "browser back",
    (0x69, True): "browser forward",
    (0x65, True): "browser search",
    (0x66, True): "browser favorites",
    (0x32, True): "browser home",
    (0x6C, True): "launch mail",
    (0x21, True): "launch calculator",
    (0x6B, True): "launch media",
}

# ── Reverse table: canonical name -> (scan, extended) ────────────────
NAME_TO_SCANCODE: dict[str, tuple[int, bool]] = {
    name: key for key, name in SCANCODE_TABLE.items()
}

# ── Aliases ──────────────────────────────────────────────────────────
# Normalized names that should resolve to a canonical name.
# Generic modifier names (no left/right) resolve to the left variant.
_ALIASES: dict[str, str] = {
    "esc":       "escape",
    "return":    "enter",
    "ctrl":      "left ctrl",
    "lctrl":     "left ctrl",   "rctrl":     "right ctrl",
    "lcontrol":  "left ctrl",   "rcontrol":  "right ctrl",
    "left control": "left ctrl", "right control": "right ctrl",
    "control":   "left ctrl",
    "shift":     "left shift",
    "lshift":    "left shift",  "rshift":    "right shift",
    "alt":       "left alt",
    "lalt":      "left alt",    "ralt":      "right alt",
    "lmenu":     "left alt",    "rmenu":     "right alt",
    "option":    "left alt",
    "windows":   "left windows",
    "win":       "left windows",
    "lwin":      "left windows", "rwin":     "right windows",
    "super":     "left windows",
    "cmd":       "left windows", "command":  "left windows",
    "num del":   "num .",       # Num . and Num Del are the same key
    "apps":      "menu",
    "context menu": "menu",
    "break":     "pause",
    "page_up":   "page up",    "page_down": "page down",
    "pageup":    "page up",    "pagedown":  "page down",
    "pgup":      "page up",    "pgdn":      "page down",
    "numlock":   "num lock",   "capslock":  "caps lock",
    "scrolllock": "scroll lock",
    "printscreen": "print screen", "prtsc":  "print screen",
    "ins":       "insert",     "del":       "delete",
    "backspace": "backspace",  "bksp":      "backspace",
    "numpad0":   "num 0",      "numpad1":   "num 1",
    "numpad2":   "num 2",      "numpad3":   "num 3",
    "numpad4":   "num 4",      "numpad5":   "num 5",
    "numpad6":   "num 6",      "numpad7":   "num 7",
    "numpad8":   "num 8",      "numpad9":   "num 9",
    "numpad*":   "num *",      "numpad-":   "num -",
    "numpad+":   "num +",      "numpad.":   "num .",
    "numpadenter": "num enter", "numpaddiv": "num /",
    "numpad/":   "num /",
    "multiply":  "num *",      "add":       "num +",
    "subtract":  "num -",      "decimal":   "num .",
    "divide":    "num /",
    "play/pause": "media play/pause",
    "play_pause": "media play/pause",
    "next_track": "media next",
    "prev_track": "media previous",
    "volume_mute": "volume mute",
    "volume_down": "volume down",
    "volume_up":   "volume up",
}

# Russian layout -> Latin key name (physical position mapping)
_RU_TO_EN: dict[str, str] = {
    "й": "q", "ц": "w", "у": "e", "к": "r", "е": "t", "н": "y",
    "г": "u", "ш": "i", "щ": "o", "з": "p", "х": "[", "ъ": "]",
    "ф": "a", "ы": "s", "в": "d", "а": "f", "п": "g", "р": "h",
    "о": "j", "л": "k", "д": "l", "э": "'", "я": "z", "ч": "x",
    "с": "c", "м": "v", "и": "b", "т": "n", "ь": "m", "б": ",",
    "ю": ".", "ё": "`",
}

# ── Modifier identification ──────────────────────────────────────────
MODIFIER_KEYS: frozenset[tuple[int, bool]] = frozenset({
    (0x1D, False), (0x1D, True),   # Left/Right Ctrl
    (0x2A, False), (0x36, False),  # Left/Right Shift
    (0x38, False), (0x38, True),   # Left/Right Alt
    (0x5B, True),  (0x5C, True),   # Left/Right Windows
})

# Generic modifier groups: either left or right satisfies the combo
_GENERIC_MODIFIER_GROUPS: dict[str, list[str]] = {
    "ctrl":    ["left ctrl", "right ctrl"],
    "shift":   ["left shift", "right shift"],
    "alt":     ["left alt", "right alt"],
    "windows": ["left windows", "right windows"],
}

# ── Scan codes that exist ONLY as extended (for migration heuristic) ──
_EXTENDED_ONLY_SCANS: frozenset[int] = frozenset({
    0x5B, 0x5C, 0x5D,  # LWin, RWin, Menu
})

# Scan codes that have BOTH extended and non-extended variants (collision)
_AMBIGUOUS_SCANS: frozenset[int] = frozenset({
    0x1D,  # Left Ctrl / Right Ctrl
    0x38,  # Left Alt / Right Alt
    0x47,  # Num 7 / Home
    0x48,  # Num 8 / Up
    0x49,  # Num 9 / Page Up
    0x4B,  # Num 4 / Left
    0x4D,  # Num 6 / Right
    0x4F,  # Num 1 / End
    0x50,  # Num 2 / Down
    0x51,  # Num 3 / Page Down
    0x52,  # Num 0 / Insert
    0x53,  # Num . / Delete
    0x1C,  # Enter / Num Enter
    0x35,  # / (slash) / Num /
    0x37,  # Num * / Print Screen
    0x45,  # Pause / Num Lock
})


# ── Public API ───────────────────────────────────────────────────────

def normalize_name(name: str) -> str:
    """Normalize a key name: lowercase, strip, RU->EN, resolve aliases."""
    n = (name or "").strip().lower()
    n = _RU_TO_EN.get(n, n)
    n = _ALIASES.get(n, n)
    return n


def name_to_pair(name: str) -> tuple[int, bool] | None:
    """Resolve a key name to (scan_code, extended), or None if unknown."""
    canonical = normalize_name(name)
    return NAME_TO_SCANCODE.get(canonical)


def pair_to_name(scan: int, extended: bool) -> str:
    """(scan_code, extended) -> canonical name, or 'sc{hex}' if unknown."""
    return SCANCODE_TABLE.get((scan, extended), f"sc{scan:#04x}")


def is_modifier(scan: int, extended: bool) -> bool:
    """Check if the key is a modifier (Ctrl/Shift/Alt/Win)."""
    return (scan, extended) in MODIFIER_KEYS


# ── Localized display names ──────────────────────────────────────────

# Display names per language. Keys not listed here fall back to
# the canonical name with first-letter capitalization.
_DISPLAY_NAMES: dict[str, dict[str, str]] = {
    "ru": {
        # Modifiers
        "left ctrl":      "Ctrl (левый)",
        "right ctrl":     "Ctrl (правый)",
        "left shift":     "Shift (левый)",
        "right shift":    "Shift (правый)",
        "left alt":       "Alt (левый)",
        "right alt":      "Alt (правый)",
        "left windows":   "Win (левый)",
        "right windows":  "Win (правый)",
        # Navigation
        "insert":    "Insert",      "delete":    "Delete",
        "home":      "Home",        "end":       "End",
        "page up":   "Page Up",     "page down": "Page Down",
        # Arrows
        "up": "Стрелка вверх",  "down": "Стрелка вниз",
        "left": "Стрелка влево", "right": "Стрелка вправо",
        # Utility
        "escape":    "Esc",         "backspace": "Backspace",
        "tab":       "Tab",         "enter":     "Enter",
        "space":     "Пробел",
        "caps lock": "Caps Lock",   "num lock":  "Num Lock",
        "scroll lock": "Scroll Lock",
        "print screen": "Print Screen",
        "pause":     "Pause",       "menu": "Menu",
        # Numpad
        "num 0": "Num 0", "num 1": "Num 1", "num 2": "Num 2",
        "num 3": "Num 3", "num 4": "Num 4", "num 5": "Num 5",
        "num 6": "Num 6", "num 7": "Num 7", "num 8": "Num 8",
        "num 9": "Num 9",
        "num *": "Num *", "num -": "Num -", "num +": "Num +",
        "num .": "Num .",
        "num enter": "Num Enter",   "num /": "Num /",
        # Media
        "volume mute":      "Без звука",
        "volume down":      "Тише",
        "volume up":        "Громче",
        "media next":       "Следующий трек",
        "media previous":   "Предыдущий трек",
        "media stop":       "Стоп (медиа)",
        "media play/pause": "Воспроизведение",
    },
    "en": {
        # Modifiers
        "left ctrl":      "Ctrl (left)",
        "right ctrl":     "Ctrl (right)",
        "left shift":     "Shift (left)",
        "right shift":    "Shift (right)",
        "left alt":       "Alt (left)",
        "right alt":      "Alt (right)",
        "left windows":   "Win (left)",
        "right windows":  "Win (right)",
        # Navigation
        "insert":    "Insert",      "delete":    "Delete",
        "home":      "Home",        "end":       "End",
        "page up":   "Page Up",     "page down": "Page Down",
        # Arrows
        "up": "Up Arrow",    "down": "Down Arrow",
        "left": "Left Arrow", "right": "Right Arrow",
        # Utility
        "escape":    "Esc",         "backspace": "Backspace",
        "tab":       "Tab",         "enter":     "Enter",
        "space":     "Space",
        "caps lock": "Caps Lock",   "num lock":  "Num Lock",
        "scroll lock": "Scroll Lock",
        "print screen": "Print Screen",
        "pause":     "Pause",       "menu": "Menu",
        # Numpad
        "num 0": "Num 0", "num 1": "Num 1", "num 2": "Num 2",
        "num 3": "Num 3", "num 4": "Num 4", "num 5": "Num 5",
        "num 6": "Num 6", "num 7": "Num 7", "num 8": "Num 8",
        "num 9": "Num 9",
        "num *": "Num *", "num -": "Num -", "num +": "Num +",
        "num .": "Num .",
        "num enter": "Num Enter",   "num /": "Num /",
        # Media
        "volume mute":      "Mute",
        "volume down":      "Volume Down",
        "volume up":        "Volume Up",
        "media next":       "Next Track",
        "media previous":   "Previous Track",
        "media stop":       "Stop (media)",
        "media play/pause": "Play/Pause",
    },
}


def scancode_display(scan: int, extended: bool, lang: str = "en") -> str:
    """Human-readable localized name for a scan code pair.

    Falls back to canonical name with title case, then 'sc0xNN'.
    """
    canonical = SCANCODE_TABLE.get((scan, extended))
    if canonical is None:
        return f"sc{scan:#04x}"
    display_table = _DISPLAY_NAMES.get(lang, _DISPLAY_NAMES["en"])
    display = display_table.get(canonical)
    if display is not None:
        return display
    # Fallback: title case the canonical name
    # Single chars (letters/digits/symbols) -> uppercase
    if len(canonical) == 1:
        return canonical.upper()
    return canonical.title()


def combo_display(keys: list[tuple[int, bool]], lang: str = "en") -> str:
    """Format a combo as 'Ctrl (right)+Q' style string."""
    return "+".join(scancode_display(s, e, lang) for s, e in keys)


# ── Generic modifier expansion (for parsed string combos) ────────────

def expand_name_to_pairs(name: str) -> list[tuple[int, bool]]:
    """Resolve a name to all matching (scan, extended) pairs.

    Generic modifiers like 'ctrl' expand to both left and right variants.
    Specific names like 'left ctrl' return exactly one pair.
    """
    canonical = normalize_name(name)
    if canonical in _GENERIC_MODIFIER_GROUPS:
        result = []
        for specific in _GENERIC_MODIFIER_GROUPS[canonical]:
            pair = NAME_TO_SCANCODE.get(specific)
            if pair:
                result.append(pair)
        return result
    pair = NAME_TO_SCANCODE.get(canonical)
    return [pair] if pair else []


def parse_combo_groups(hotkey_str: str) -> list[frozenset[tuple[int, bool]]]:
    """Parse a hotkey string like 'ctrl+q' into groups of acceptable pairs.

    Each group is a frozenset of (scan, extended) pairs that satisfy
    that position in the combo. Generic 'ctrl' produces a group with
    both left and right ctrl.
    """
    groups: list[frozenset[tuple[int, bool]]] = []
    for part in (hotkey_str or "").lower().replace(",", "+").split("+"):
        part = part.strip()
        if not part:
            continue
        pairs = expand_name_to_pairs(part)
        if not pairs:
            return []  # unknown key -> whole combo invalid
        groups.append(frozenset(pairs))
    return groups


# ── Migration helper ─────────────────────────────────────────────────

def migrate_legacy_code(code: int) -> tuple[int, bool, bool]:
    """Convert a legacy integer scan code to (scan, extended, ambiguous).

    Legacy format: code >= 256 means extended (code - 256 = real scan).
    Code < 256 means raw scan code — extended is guessed heuristically.
    Returns (scan, extended, was_ambiguous).
    """
    if code >= 256:
        return (code - 256, True, False)

    # Check if this scan code only exists as extended
    if code in _EXTENDED_ONLY_SCANS:
        return (code, True, False)

    # Check if it's ambiguous (exists as both extended and non-extended)
    if code in _AMBIGUOUS_SCANS:
        # Default to non-extended (left modifier / numpad key)
        return (code, False, True)

    # Unambiguous: only non-extended variant exists
    return (code, False, False)
