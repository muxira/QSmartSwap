"""
Логика переключения оружия: слушает ОДИН глобальный хоткей, смотрит
на текущее активное оружие (через GsiState) и отправляет клавишу
целевого слота.

Правила: список пар (если активен A -> взять B).
Клавиша для каждого целевого слота настраивается отдельно (slot_keys).

Отправка клавиш — это просто симуляция нажатия клавиатуры (как любой
макрос/автохоткей-бинд), НЕ чтение и НЕ запись в память процесса игры.
"""

import time

from gsi_server import LOGICAL_SLOTS, SLOT_IDS, GsiState

SLOT_KEYS_DEFAULT = {slot: LOGICAL_SLOTS[slot]["default_key"] for slot in SLOT_IDS}

DEFAULT_RULES = [
    {"active": "primary", "target": "knife"},
    {"active": "knife", "target": "primary"},
    {"active": "secondary", "target": "primary"},
]


class HotkeyManager:
    def __init__(self, state: GsiState, log_callback, send_fn=None, debounce_ms: int = 150):
        self.state = state
        self.log_callback = log_callback
        self._send_fn = send_fn  # для тестов; по умолчанию keyboard.send
        self._current_hotkey: str | None = None
        self.rules: list[dict] = [dict(r) for r in DEFAULT_RULES]
        self.slot_keys: dict[str, str] = dict(SLOT_KEYS_DEFAULT)
        self.debounce_ms = debounce_ms
        self._last_fire = 0.0

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

        keyboard.send(key)

    # --- срабатывание ---

    def _on_hotkey(self):
        now = time.time() * 1000
        if now - self._last_fire < self.debounce_ms:
            return
        self._last_fire = now

        snap = self.state.snapshot()
        active = snap["active_slot"]

        if not snap["connected"] or active is None:
            self.log_callback("[hotkey] нет данных от игры — нажми, зайдя в матч")
            return

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
        self.log_callback(f"[hotkey] {active} -> {target} ('{key_to_send}'){owned_note}")

    def start(self, hotkey: str):
        self.stop()
        if not (hotkey or "").strip():
            self.log_callback("!! Пустой хоткей")
            return False
        try:
            import keyboard

            keyboard.add_hotkey(hotkey, self._on_hotkey)
            self._current_hotkey = hotkey
            self.log_callback(f"Слушаю хоткей: {hotkey}")
            return True
        except Exception as e:  # noqa: BLE001
            self.log_callback(f"!! Не удалось забиндить '{hotkey}': {e}")
            return False

    def stop(self):
        if self._current_hotkey:
            try:
                import keyboard

                keyboard.remove_hotkey(self._current_hotkey)
            except (KeyError, ValueError):
                pass
            self._current_hotkey = None

    @property
    def current_hotkey(self):
        return self._current_hotkey
