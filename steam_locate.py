"""
Поиск пути установки Steam и Counter-Strike 2 (папка cfg).

Работает через:
1) реестр Windows (HKCU\\Software\\Valve\\Steam -> SteamPath)
2) парсинг libraryfolders.vdf, чтобы найти ВСЕ Steam-библиотеки
   (игра может быть установлена не на том же диске, что Steam)
3) поиск папки "Counter-Strike Global Offensive" в каждой библиотеке
   (это имя папки используется и для CS2, оставлено для обратной совместимости)
"""

import os
import re
import winreg

CS_FOLDER_NAME = "Counter-Strike Global Offensive"


def find_steam_path() -> str | None:
    """Возвращает путь к папке Steam, либо None если не найдено."""
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam")
        path, _ = winreg.QueryValueEx(key, "SteamPath")
        winreg.CloseKey(key)
        if path and os.path.isdir(path):
            return os.path.normpath(path)
    except OSError:
        pass

    # запасные варианты, если реестр не помог
    for candidate in (
        r"C:\Program Files (x86)\Steam",
        r"C:\Program Files\Steam",
    ):
        if os.path.isdir(candidate):
            return candidate

    return None


def _parse_library_folders(steam_path: str) -> list[str]:
    """Парсит libraryfolders.vdf и возвращает список путей всех Steam-библиотек."""
    libraries = [steam_path]  # сам Steam тоже считается библиотекой

    vdf_path = os.path.join(steam_path, "steamapps", "libraryfolders.vdf")
    if not os.path.isfile(vdf_path):
        return libraries

    try:
        with open(vdf_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except OSError:
        return libraries

    # ищем строки вида "path"		"D:\\SteamLibrary"
    for match in re.finditer(r'"path"\s+"([^"]+)"', content):
        raw = match.group(1)
        # vdf хранит бэкслеши экранированными как \\
        cleaned = raw.replace("\\\\", "\\")
        if os.path.isdir(cleaned) and cleaned not in libraries:
            libraries.append(cleaned)

    return libraries


def find_cs2_root() -> str | None:
    """
    Возвращает корневую папку установки CS2
    (ту, что содержит подпапку "game"), либо None.
    """
    steam_path = find_steam_path()
    if not steam_path:
        return None

    for lib in _parse_library_folders(steam_path):
        candidate = os.path.join(lib, "steamapps", "common", CS_FOLDER_NAME)
        if os.path.isdir(candidate):
            return candidate

    return None


def get_cfg_dir(cs2_root: str) -> str:
    """Путь к папке game/csgo/cfg внутри установки CS2."""
    return os.path.join(cs2_root, "game", "csgo", "cfg")
