"""
Установка и проверка конфига Game State Integration для CS2.

Официальный, разрешённый Valve механизм: игра сама шлёт данные
о своём состоянии на локальный HTTP-эндпоинт согласно этому конфигу.
Никакого чтения памяти процесса, никакого стороннего вмешательства в игру.
"""

import os

CFG_FILENAME = "gamestate_integration_qsmartswap.cfg"
BINDS_FILENAME = "qsmartswap_binds.cfg"
GSI_PORT = 7777
GSI_PATH = "/qsmartswap"


def build_cfg_content(port: int = GSI_PORT, path: str = GSI_PATH) -> str:
    return f'''"QSmartSwap GSI"
{{
    "uri" "http://127.0.0.1:{port}{path}"
    "timeout" "5.0"
    "buffer"  "0.1"
    "throttle" "0.1"
    "heartbeat" "30.0"
    "data"
    {{
        "provider"       "1"
        "map"            "1"
        "round"          "1"
        "player_id"      "1"
        "player_state"   "1"
        "player_weapons" "1"
    }}
}}
'''


def is_config_installed(cfg_dir: str) -> bool:
    return os.path.isfile(os.path.join(cfg_dir, CFG_FILENAME))


def install_config(cfg_dir: str, port: int = GSI_PORT, path: str = GSI_PATH) -> str:
    """Пишет cfg под заданный порт. Возвращает полный путь к файлу."""
    os.makedirs(cfg_dir, exist_ok=True)
    target = os.path.join(cfg_dir, CFG_FILENAME)
    with open(target, "w", encoding="utf-8") as f:
        f.write(build_cfg_content(port, path))
    return target


# --- файл биндов слотов для CS2 ---

# имя клавиши keyboard-lib -> имя клавиши в синтаксисе bind CS2
KEY_TO_CS = {
    "space": "space",
    "enter": "enter",
    "tab": "tab",
    "esc": "escape",
    "escape": "escape",
    "backspace": "backspace",
    "capslock": "capslock",
    "insert": "ins",
    "delete": "del",
    "home": "home",
    "end": "end",
    "pageup": "pgup",
    "pagedown": "pgdn",
    "up": "uparrow",
    "down": "downarrow",
    "left": "leftarrow",
    "right": "rightarrow",
}


def cs_key_name(key: str) -> str | None:
    """Возвращает имя клавиши для CS2-bind, либо None если такое не биндится напрямую
    (комбинации с модификаторами вида ctrl+q игра одним bind не поддерживает)."""
    k = (key or "").strip().lower()
    if not k or "+" in k:
        return None
    return KEY_TO_CS.get(k, k)


def build_binds_content(slot_keys: dict, valve_map: dict) -> tuple[str, list[str]]:
    """Строит содержимое qsmartswap_binds.cfg.
    Возвращает (текст, предупреждения) — предупреждения про пустые/дубли/комбо."""
    warnings: list[str] = []
    lines = [
        "// QSmartSwap slot binds — сгенерировано автоматически",
        "// Как применить: в консоли игры: exec qsmartswap_binds",
        "",
    ]
    used: dict[str, str] = {}  # cs_key -> valve
    for slot, valve in valve_map.items():
        if slot == "other":
            continue
        key = (slot_keys.get(slot) or "").strip()
        if not key:
            warnings.append(f"{slot}: клавиша не задана — пропущен")
            continue
        cs = cs_key_name(key)
        if cs is None:
            warnings.append(f"{slot}: комбо '{key}' одним bind не биндится — забинди вручную")
            lines.append(f'// {slot} ({valve}): комбо "{key}" — забинди вручную через настройки игры')
            continue
        if cs in used:
            warnings.append(f"клавиша '{key}' уже занята слотом {used[cs]} — {slot} перезапишет её!")
        used[cs] = valve
        lines.append(f'bind "{cs}" "{valve}"  // {slot}')
    lines.append("")
    return "\n".join(lines), warnings


def install_binds(cfg_dir: str, slot_keys: dict, valve_map: dict) -> tuple[str, list[str]]:
    """Пишет qsmartswap_binds.cfg. Возвращает (путь, предупреждения)."""
    os.makedirs(cfg_dir, exist_ok=True)
    target = os.path.join(cfg_dir, BINDS_FILENAME)
    content, warnings = build_binds_content(slot_keys, valve_map)
    with open(target, "w", encoding="utf-8") as f:
        f.write(content)
    return target, warnings
