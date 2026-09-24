"""
Установка и проверка конфига Game State Integration для CS2.

Официальный, разрешённый Valve механизм: игра сама шлёт данные
о своём состоянии на локальный HTTP-эндпоинт согласно этому конфигу.
Никакого чтения памяти процесса, никакого стороннего вмешательства в игру.
"""

import os

CFG_FILENAME = "gamestate_integration_qsmartswap.cfg"
BINDS_FILENAME = "qsmartswap_binds.cfg"
AUTOEXEC_FILENAME = "autoexec.cfg"
AUTOEXEC_EXEC_LINE = "exec qsmartswap_binds"
GSI_PORT = 7777
GSI_PATH = "/qsmartswap"

# stale configs from earlier versions — game would POST to two ports at once
LEGACY_CFG_NAMES = (
    "gamestate_integration_weapon_toggle.cfg",
    "gamestate_integration_weapon_watch.cfg",
)


def build_cfg_content(port: int = GSI_PORT, path: str = GSI_PATH) -> str:
    return f'''"QSmartSwap GSI"
{{
    "uri" "http://127.0.0.1:{port}{path}"
    "timeout" "5.0"
    "buffer"  "0.1"
    "throttle" "0.1"
    "heartbeat" "3.0"
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


def read_config_text(cfg_dir: str) -> str | None:
    """Current GSI file content, or None if missing/unreadable."""
    try:
        with open(os.path.join(cfg_dir, CFG_FILENAME), "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def remove_legacy_configs(cfg_dir: str) -> list[str]:
    """Delete stale GSI configs from earlier versions. Returns removed names."""
    removed = []
    for name in LEGACY_CFG_NAMES:
        path = os.path.join(cfg_dir, name)
        try:
            if os.path.isfile(path):
                os.remove(path)
                removed.append(name)
        except OSError:
            pass
    return removed


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
    """Build qsmartswap_binds.cfg content.
    Returns (text, warnings). Warnings are ALWAYS English (console output)."""
    warnings: list[str] = []
    lines = [
        "// QSmartSwap slot binds — generated automatically, do not edit manually",
        "// loaded automatically via autoexec.cfg",
        "",
    ]
    used: dict[str, str] = {}  # cs_key -> valve
    for slot, valve in valve_map.items():
        if slot == "other":
            continue
        key = (slot_keys.get(slot) or "").strip()
        if not key:
            warnings.append(f"{slot}: no key set — skipped")
            continue
        cs = cs_key_name(key)
        if cs is None:
            warnings.append(f"{slot}: combo '{key}' cannot be bound with one bind — bind it manually")
            lines.append(f'// {slot} ({valve}): combo "{key}" — bind manually in game settings')
            continue
        if cs in used:
            warnings.append(f"key '{key}' already used by slot {used[cs]} — {slot} overwrites it!")
        used[cs] = valve
        lines.append(f'bind "{cs}" "{valve}"  // {slot}')
    lines.append("")
    return "\n".join(lines), warnings


def ensure_autoexec(cfg_dir: str) -> str | None:
    """Make sure autoexec.cfg runs our binds. Returns autoexec path if touched, else None."""
    os.makedirs(cfg_dir, exist_ok=True)
    target = os.path.join(cfg_dir, AUTOEXEC_FILENAME)
    try:
        with open(target, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError:
        content = ""
    if AUTOEXEC_EXEC_LINE in content:
        return None
    with open(target, "a", encoding="utf-8") as f:
        if content and not content.endswith("\n"):
            f.write("\n")
        f.write(f"\n// QSmartSwap — load slot binds\n{AUTOEXEC_EXEC_LINE}\n")
    return target


def install_binds(cfg_dir: str, slot_keys: dict, valve_map: dict) -> tuple[str, list[str], str | None]:
    """Write qsmartswap_binds.cfg + hook it into autoexec.cfg.
    Returns (binds_path, warnings, autoexec_path_or_None_if_already_hooked)."""
    os.makedirs(cfg_dir, exist_ok=True)
    target = os.path.join(cfg_dir, BINDS_FILENAME)
    content, warnings = build_binds_content(slot_keys, valve_map)
    with open(target, "w", encoding="utf-8") as f:
        f.write(content)
    autoexec = ensure_autoexec(cfg_dir)
    return target, warnings, autoexec
