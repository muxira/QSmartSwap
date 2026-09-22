STRINGS = {
    "ru": {
        "title": "QSmartSwap — переключатель оружия CS2",
        "hotkey_label": "Кейбинд (любая клавиша/комбо):",
        "hotkey_change": "Изменить...",
        "hotkey_capture": "Нажми клавиши...",
        "port_label": "Порт сервера:",
        "server_start": "Старт",
        "server_restart": "Рестарт",
        "server_stop": "Стоп",
        "status_server_off": "Сервер: выкл",
        "status_server_on": "Сервер: порт {port}",
        "status_gsi_wait": "Жду данные от игры...",
        "status_gsi_ok": "Игра: {active} | owned: {owned}",
        "rules_title": "Правила: если активен слот X → взять слот Y",
        "rules_add": "+ Добавить",
        "rules_if": "Если активен",
        "rules_then": "Взять",
        "slotkeys_title": "Клавиши слотов (что нажимать в игре):",
        "slotkeys_reset": "Сбросить",
        "console_label": "Вывод:",
        "install_cfg": "Установить GSI-конфиг",
        "minimize_tray": "Свернуть в трей",
        "lang_label": "Язык:",
        "tray_show": "Показать",
        "tray_exit": "Выход",
        "tray_hidden": "QSmartSwap свёрнут в трей",
        "popup_title": "Конфиг установлен",
        "popup_text": (
            "GSI-конфиг для CS2 установлен:\n{path}\n\n"
            "Если игра сейчас запущена — перезапустите её, "
            "чтобы конфиг подхватился."
        ),
        "err_no_steam": "Не удалось найти установку Steam. Укажите папку CS2 вручную.",
        "err_no_cs2": "Не удалось найти установку CS2 в библиотеках Steam. Укажите папку вручную.",
        "choose_cs2_folder": "Укажите папку установки CS2 (там, где лежит папка game)",
        "same_slot_error": "Слоты в правиле должны отличаться!",
    },
    "en": {
        "title": "QSmartSwap — CS2 weapon switcher",
        "hotkey_label": "Keybind (any key/combo):",
        "hotkey_change": "Change...",
        "hotkey_capture": "Press keys...",
        "port_label": "Server port:",
        "server_start": "Start",
        "server_restart": "Restart",
        "server_stop": "Stop",
        "status_server_off": "Server: off",
        "status_server_on": "Server: port {port}",
        "status_gsi_wait": "Waiting for game data...",
        "status_gsi_ok": "Game: {active} | owned: {owned}",
        "rules_title": "Rules: if slot X is active → take slot Y",
        "rules_add": "+ Add",
        "rules_if": "If active",
        "rules_then": "Take",
        "slotkeys_title": "Slot keys (what to press in game):",
        "slotkeys_reset": "Reset",
        "console_label": "Output:",
        "install_cfg": "Install GSI config",
        "minimize_tray": "Minimize to tray",
        "lang_label": "Language:",
        "tray_show": "Show",
        "tray_exit": "Exit",
        "tray_hidden": "QSmartSwap minimized to tray",
        "popup_title": "Config installed",
        "popup_text": (
            "GSI config for CS2 was installed:\n{path}\n\n"
            "If the game is currently running, please restart it."
        ),
        "err_no_steam": "Could not find a Steam installation. Please select the CS2 folder manually.",
        "err_no_cs2": "Could not find a CS2 installation in Steam libraries. Please select the folder manually.",
        "choose_cs2_folder": "Select the CS2 install folder (the one containing the 'game' folder)",
        "same_slot_error": "Rule slots must differ!",
    },
}

SLOT_LABELS = {
    "primary": ("Основное (slot1)", "Primary (slot1)"),
    "secondary": ("Вторичное (slot2)", "Secondary (slot2)"),
    "knife": ("Нож (slot3)", "Knife (slot3)"),
    "zeus": ("Zeus (slot11)", "Zeus (slot11)"),
    "grenades": ("Гранаты (slot4)", "Grenades (slot4)"),
    "hegrenade": ("HE (slot6)", "HE (slot6)"),
    "flashbang": ("Флэш (slot7)", "Flash (slot7)"),
    "smokegrenade": ("Смок (slot8)", "Smoke (slot8)"),
    "decoy": ("Декоя (slot9)", "Decoy (slot9)"),
    "molotov": ("Молотов (slot10)", "Molotov (slot10)"),
    "c4": ("C4 (slot5)", "C4 (slot5)"),
    "healthshot": ("Шприц (slot12)", "Healthshot (slot12)"),
    "other": ("Другое (slot0/13)", "Other (slot0/13)"),
}


def slot_label(slot_id: str, lang: str) -> str:
    pair = SLOT_LABELS.get(slot_id, (slot_id, slot_id))
    return pair[0] if lang == "ru" else pair[1]
