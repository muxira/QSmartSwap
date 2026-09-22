"""
Установка и проверка конфига Game State Integration для CS2.

Официальный, разрешённый Valve механизм: игра сама шлёт данные
о своём состоянии на локальный HTTP-эндпоинт согласно этому конфигу.
Никакого чтения памяти процесса, никакого стороннего вмешательства в игру.
"""

import os

CFG_FILENAME = "gamestate_integration_qsmartswap.cfg"
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
