"""
Логика переключения оружия: слушает ОДИН глобальный хоткей (комбинация
любой сложности: "q", "ctrl+q", "ctrl+shift+x", ...), смотрит
на текущее активное оружие (через GsiState) и отправляет клавишу
целевого слота.

Правила: список пар (если активен A -> взять B).
Клавиша для каждого целевого слота настраивается отдельно (slot_keys).

Три защиты от ложных срабатываний:
1. Своя проверка комбо по зажатым клавишам — срабатывает, даже если
   в момент нажатия held посторонние клавиши (идёшь на W + жмёшь Q).
2. Фокус: вне cs2.exe / csgo.exe хоткей игнорируется.
3. Раунд: функции включаются только когда от игры пришёл round.phase
   live/freezetime/warmup (а не первый попавшийся пакет про оружие).

Анти-спам: оптимистичный прогноз — после срабатывания считаем активным
целевой слот, пока игра не прислала обновление (GSI идёт ~10 Гц).
Поэтому быстрые даблы чередуются корректно, а не читают протухший active.

Отправка клавиш — это просто симуляция нажатия клавиатуры (как любой
макрос/автохоткей-бинд), НЕ чтение и НЕ запись в память процесса игры.
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


def parse_combo(hotkey: str) -> frozenset:
    """'Ctrl+Shift+Q' -> frozenset({'ctrl','shift','q'})."""
    parts = []
    for p in (hotkey or "").lower().replace(",", "+").split("+"):
        p = p.strip()
        if not p:
            continue
        parts.append(_ALIASES.get(p, p))
    return frozenset(parts)


def _foreground_process_name() -> str | None:
    """Имя exe активного окна (lower) или None если не определили."""
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
        debounce_ms: int = 80,
        focus_check: bool = True,
        foreground_fn=None,
    ):
        self.state = state
        self.log_callback = log_callback
        self._send_fn = send_fn  # для тестов; по умолчанию keyboard.press_and_release
        self._current_hotkey: str | None = None
        self._required: frozenset = frozenset()
        self.rules: list[dict] = [dict(r) for r in DEFAULT_RULES]
        self.slot_keys: dict[str, str] = dict(SLOT_KEYS_DEFAULT)
        self.debounce_ms = debounce_ms
        self.focus_check = focus_check
        self._foreground_fn = foreground_fn or _foreground_process_name
        self._hook = None
        self._lock = threading.Lock()
        self._pressed: set[str] = set()
        self._latched = False
        self._last_fire = 0.0
        self._last_quiet_log = 0.0
        # оптимистичный прогноз для спама
        self._predicted: str | None = None
        self._predicted_base: str | None = None
        self._predicted_at = 0.0
        self._predict_ttl = 0.8

    # --- настройки ---

    def set_rules(self, rules: list[dict]):
        """rules: [{'active': ..., 'target': ...}, ...] — мусор отбрасывается."""
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
        import keyboard

        keyboard.press_and_release(key)

    def _quiet(self, msg: str, throttle_s: float = 3.0):
        now = time.time()
        if now - self._last_quiet_log >= throttle_s:
            self._last_quiet_log = now
            self.log_callback(msg)

    # --- матчинг комбо ---

    def _on_key_event(self, event):
        """keyboard.hook callback. Срабатывает и с зажатыми посторонними клавишами:
        требуем required ⊆ pressed, а не точного равенства."""
        try:
            name = (_ALIASES.get(event.name, event.name) if event.name else "")
        except AttributeError:
            return
        if not name:
            return
        with self._lock:
            if event.event_type == "down":
                self._pressed.add(name)
            else:
                self._pressed.discard(name)
                if not self._required.issubset(self._pressed):
                    self._latched = False
                return

            if not self._required or self._latched:
                return
            if name not in self._required:
                return
            if self._required.issubset(self._pressed):
                self._latched = True
                self._on_hotkey()

    def _effective_active(self, snap_active: str | None) -> str | None:
        """Активный слот с учётом прогноза (для спама быстрее 10 Гц GSI)."""
        if (
            self._predicted
            and (time.time() - self._predicted_at) < self._predict_ttl
            and snap_active == self._predicted_base
        ):
            return self._predicted
        return snap_active

    # --- срабатывание ---

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
                self._quiet(f"[hotkey] игнор: фокус не в игре ({proc})")
                return

        snap = self.state.snapshot()
        if not snap["connected"] or snap["active_slot"] is None:
            self._quiet("[hotkey] нет данных от игры — зайди в матч")
            return
        if not snap.get("round_seen"):
            self._quiet("[hotkey] жду начала раунда (нет round.phase — переустанови GSI-конфиг)")
            return
        if not snap.get("armed"):
            self._quiet(f"[hotkey] раунд не идёт (phase={snap.get('round_phase')}) — жду live")
            return

        active = self._effective_active(snap["active_slot"])

        target = None
        for r in self.rules:
            if r["active"] == active:
                target = r["target"]
                break

        if target is None:
            self.log_callback(f"[hotkey] active={active}: нет правила — ничего не делаю")
            return

        key_to_send = (self.slot_keys.get(target) or "").strip()
        if not key_to_send:
            self.log_callback(f"[hotkey] active={active} -> {target}: не задана клавиша слота!")
            return

        owned_note = "" if target in snap.get("owned", []) else " (нет в инвентаре?)"
        try:
            self._send(key_to_send)
        except Exception as e:  # noqa: BLE001
            self.log_callback(f"!! Не смог нажать '{key_to_send}': {e}")
            return
        # прогноз: считаем что теперь держим target, пока GSI не подтвердит
        self._predicted = target
        self._predicted_base = snap["active_slot"]
        self._predicted_at = time.time()
        self.log_callback(f"[hotkey] {active} -> {target} ('{key_to_send}'){owned_note}")

    def start(self, hotkey: str):
        self.stop()
        required = parse_combo(hotkey)
        if not required:
            self.log_callback("!! Пустой хоткей")
            return False
        try:
            import keyboard

            with self._lock:
                self._pressed = set()
                self._latched = False
                self._required = required
                self._current_hotkey = hotkey.strip()
                self._hook = keyboard.hook(self._on_key_event, suppress=False)
            self.log_callback(f"Слушаю хоткей: {self._current_hotkey}")
            return True
        except Exception as e:  # noqa: BLE001
            self.log_callback(f"!! Не удалось забиндить '{hotkey}': {e}")
            return False

    def stop(self):
        with self._lock:
            hook, self._hook = self._hook, None
            self._required = frozenset()
            self._current_hotkey = None
            self._pressed = set()
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
