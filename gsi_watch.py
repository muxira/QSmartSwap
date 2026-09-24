"""
Мини-сервер для проверки Game State Integration (CS2).
Ничего не симулирует, ничего не нажимает — только слушает запросы
от игры и печатает, что пришло по оружию, чтобы понять формат данных.

Запуск:
    python gsi_watch.py

Порт должен совпадать с портом в .cfg файле (по умолчанию 3000).
"""

import json
from http.server import BaseHTTPRequestHandler, HTTPServer

from gsi_config import GSI_PORT

PORT = GSI_PORT


class GSIHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # глушим стандартные логи http.server, у нас свой вывод
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)

        # всегда отвечаем 200, иначе игра может решить, что эндпоинт не рабочий
        self.send_response(200)
        self.end_headers()

        try:
            data = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            print("!! Пришёл невалидный JSON, пропускаю")
            return

        self.print_weapon_info(data)

    def print_weapon_info(self, data: dict):
        print("=" * 60)

        weapons = data.get("player", {}).get("weapons")

        if not weapons:
            # это нормально в первые секунды после коннекта, или если
            # в cfg не включены player_weapons / нет активного раунда
            print("Нет данных об оружии в этом пакете (пока пусто)")
            return

        for slot_key, w in weapons.items():
            name = w.get("name", "?")
            wtype = w.get("type", "?")
            state = w.get("state", "?")  # active / holstered / reload и т.д.
            ammo_clip = w.get("ammo_clip", "-")
            ammo_reserve = w.get("ammo_reserve", "-")
            print(
                f"{slot_key}: {name:<25} type={wtype:<10} "
                f"state={state:<10} clip={ammo_clip} reserve={ammo_reserve}"
            )

        # отдельно вытащим — есть ли что-то похожее на primary
        has_primary = any(
            w.get("type") in ("Rifle", "SniperRifle", "Submachine Gun", "Shotgun", "Machine Gun")
            for w in weapons.values()
        )
        has_pistol = any(w.get("type") == "Pistol" for w in weapons.values())
        has_knife = any(w.get("type") == "Knife" for w in weapons.values())

        print(f"--> primary: {has_primary} | pistol: {has_pistol} | knife: {has_knife}")


def main():
    server = HTTPServer(("127.0.0.1", PORT), GSIHandler)
    print(f"Слушаю GSI на http://127.0.0.1:{PORT}")
    print("Зайди в игру (в любой матч/тренировку с оружием) и смотри на вывод здесь.")
    print("Ctrl+C для остановки.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nОстанавливаюсь.")
        server.server_close()


if __name__ == "__main__":
    main()
