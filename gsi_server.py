"""
Локальный HTTP-сервер, принимающий POST-запросы от CS2 (Game State Integration)
и хранящий текущее состояние оружия игрока (thread-safe).

Никакого чтения памяти процесса игры — все данные приходят от самой игры
по официальному протоколу GSI.

Гранулярные логические слоты (чтобы ловить гранаты/zeus отдельно):
    primary, secondary, knife, zeus,
    grenades (общий цикл, slot4),
    hegrenade (slot6), flashbang (slot7), smokegrenade (slot8),
    decoy (slot9), molotov (slot10, molotov+incgrenade),
    c4 (slot5), healthshot (slot12), other (slot0/slot13/неизвестное).
"""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

PRIMARY_TYPES = {"Rifle", "SniperRifle", "Submachine Gun", "Shotgun", "Machine Gun"}
SECONDARY_TYPES = {"Pistol"}
KNIFE_TYPES = {"Knife"}

# Valve slot -> логический слот: (valve, дефолтная клавиша, подпись)
LOGICAL_SLOTS: dict[str, dict] = {
    "primary": {"valve": "slot1", "default_key": "1", "label": "Основное (slot1)"},
    "secondary": {"valve": "slot2", "default_key": "2", "label": "Вторичное (slot2)"},
    "knife": {"valve": "slot3", "default_key": "3", "label": "Нож (slot3)"},
    "zeus": {"valve": "slot11", "default_key": "3", "label": "Zeus (slot11 / slot3)"},
    "grenades": {"valve": "slot4", "default_key": "4", "label": "Гранаты, цикл (slot4)"},
    "hegrenade": {"valve": "slot6", "default_key": "6", "label": "HE (slot6)"},
    "flashbang": {"valve": "slot7", "default_key": "7", "label": "Флэш (slot7)"},
    "smokegrenade": {"valve": "slot8", "default_key": "8", "label": "Смок (slot8)"},
    "decoy": {"valve": "slot9", "default_key": "9", "label": "Декоя (slot9)"},
    "molotov": {"valve": "slot10", "default_key": "0", "label": "Молотов (slot10)"},
    "c4": {"valve": "slot5", "default_key": "5", "label": "C4 (slot5)"},
    "healthshot": {"valve": "slot12", "default_key": "h", "label": "Мед. шприц (slot12)"},
    "other": {"valve": "slot0", "default_key": "", "label": "Другое (slot0/13)"},
}

SLOT_IDS = list(LOGICAL_SLOTS.keys())


def weapon_to_slot(wname: str, wtype: str) -> str:
    """Маппинг оружия из GSI в логический слот. wname вида 'weapon_ak47'."""
    name = (wname or "").lower().replace("weapon_", "")

    # точные имена — в первую очередь (гранаты/zeus/c4/шприц)
    if "taser" in name or name == "zeus" or "zeus" in name:
        return "zeus"
    if "hegrenade" in name:
        return "hegrenade"
    if "flashbang" in name:
        return "flashbang"
    if "smokegrenade" in name or name == "smoke":
        return "smokegrenade"
    if "decoy" in name:
        return "decoy"
    if "molotov" in name or "incgrenade" in name or "incendiary" in name:
        return "molotov"
    if name == "c4" or "c4" in name:
        return "c4"
    if "healthshot" in name or "health" in name or "medi" in name:
        return "healthshot"
    # нож: по типу или по имени (weapon_knife, bayonet, karambit, ...)
    if wtype in KNIFE_TYPES or "knife" in name or "bayonet" in name or "karambit" in name:
        return "knife"

    if wtype in PRIMARY_TYPES:
        return "primary"
    if wtype in SECONDARY_TYPES:
        return "secondary"
    if wtype == "Grenade":
        return "grenades"
    if wtype == "C4":
        return "c4"
    # Tablet/StackableItem/Equipment/Fists и прочее
    return "other"


class GsiState:
    """Потокобезопасное хранилище последнего известного состояния."""

    def __init__(self):
        self._lock = threading.Lock()
        self.owned: set[str] = set()
        self.active_slot: str | None = None
        self.connected = False
        self.last_update = 0.0
        self.last_raw_summary = ""

    def update_from_payload(self, data: dict) -> dict:
        """Разбирает GSI-пакет. Возвращает новый снапшот."""
        weapons = data.get("player", {}).get("weapons", {}) or {}

        owned: set[str] = set()
        found_active: str | None = None
        summary_lines = []

        for _key, w in weapons.items():
            if not isinstance(w, dict):
                continue
            wtype = w.get("type", "?")
            wname = w.get("name", "?")
            wstate = w.get("state", "?")

            slot = weapon_to_slot(wname, wtype)
            owned.add(slot)

            # active может приходить как "active" (обычно) — берём первое
            if wstate == "active" and found_active is None:
                found_active = slot

            summary_lines.append(f"{wname} [{wtype}] state={wstate}")

        with self._lock:
            # пустой пакет (смерть/спектатор/меню) — инвентарь не затираем,
            # иначе правила будут думать что оружия нет
            if weapons:
                self.owned = owned
            # ВАЖНО: если в пакете нет active (меню/табло/смерть) —
            # не затираем предыдущий слот, чтобы не было ложных срабатываний
            if found_active is not None:
                self.active_slot = found_active
            self.connected = True
            self.last_update = time.time()
            self.last_raw_summary = "; ".join(summary_lines) if summary_lines else "(нет оружия в пакете)"
            return self._snapshot_locked()

    def _snapshot_locked(self) -> dict:
        owned = set(self.owned)
        return {
            "active_slot": self.active_slot,
            "owned": sorted(owned),
            "has_primary": "primary" in owned,
            "has_secondary": "secondary" in owned,
            "has_knife": "knife" in owned,
            "connected": self.connected,
            "last_update": self.last_update,
            "summary": self.last_raw_summary,
        }

    def snapshot(self) -> dict:
        with self._lock:
            return self._snapshot_locked()


def _make_handler(state: GsiState, log_callback, expected_path: str):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass  # глушим стандартные логи http.server

        def do_POST(self):
            if expected_path and self.path != expected_path:
                self.send_response(404)
                self.end_headers()
                return
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length)
            self.send_response(200)
            self.end_headers()

            try:
                data = json.loads(raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                log_callback("!! Получен невалидный JSON от игры")
                return

            old = state.snapshot()
            new = state.update_from_payload(data)
            # логируем только изменения, иначе при throttle 0.1 будет спам 10 строк/сек
            if (new["active_slot"] != old["active_slot"]) or (new["owned"] != old["owned"]):
                log_callback(
                    f"active={new['active_slot']} owned={','.join(new['owned']) or '-'}"
                )

    return Handler


class GsiServerThread(threading.Thread):
    def __init__(self, state: GsiState, log_callback, port: int, path: str = ""):
        super().__init__(daemon=True)
        self.state = state
        self.log_callback = log_callback
        self.port = port
        self.path = path
        self._server: HTTPServer | None = None

    def run(self):
        handler_cls = _make_handler(self.state, self.log_callback, self.path)
        try:
            self._server = HTTPServer(("127.0.0.1", self.port), handler_cls)
        except OSError as e:
            self.log_callback(f"!! Не удалось запустить сервер на порту {self.port}: {e}")
            return
        self.log_callback(f"GSI-сервер слушает http://127.0.0.1:{self.port}{self.path}")
        try:
            self._server.serve_forever()
        except Exception:  # noqa: BLE001
            pass

    def stop(self):
        if self._server:
            try:
                self._server.shutdown()
            except Exception:  # noqa: BLE001
                pass
            try:
                self._server.server_close()
            except Exception:  # noqa: BLE001
                pass
            self._server = None
