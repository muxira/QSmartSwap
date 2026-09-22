"""QSmartSwap — PyQt6 интерфейс.

- Кейбинд: любой (захват через keyboard.read_hotkey).
- Порт сервера: выбор + Старт/Рестарт/Стоп, по умолчанию 7777, автостарт при запуске.
- Правила: строки "если активен X -> взять Y", кнопка добавления.
- Клавиши слотов: что нажимать в игре для каждого слота (slot1..slot12 + other).
- Вывод: небольшая консоль. Свернуть в трей. Иконка icon.png в корне.
"""

import json
import os
import sys
import threading

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QIcon
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
    QMenu,
)

from gsi_config import GSI_PATH, install_binds, install_config, is_config_installed
from gsi_server import LOGICAL_SLOTS, SLOT_IDS, VALVE_MAP, GsiServerThread, GsiState
from hotkey_logic import DEFAULT_RULES, SLOT_KEYS_DEFAULT, HotkeyManager
from i18n import STRINGS, slot_label
from steam_locate import find_cs2_root, get_cfg_dir

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ICON_PATH = os.path.join(BASE_DIR, "icon.png")
CONFIG_JSON = os.path.join(BASE_DIR, "config.json")
DEFAULT_PORT = 7777
DEFAULT_HOTKEY = "q"
DEFAULT_LANG = "ru"


def load_settings() -> dict:
    try:
        with open(CONFIG_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def save_settings(data: dict):
    try:
        with open(CONFIG_JSON, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


class MainWindow(QMainWindow):
    log_signal = pyqtSignal(str)
    hotkey_captured = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.settings = load_settings()
        self.lang = self.settings.get("lang", DEFAULT_LANG)
        if self.lang not in STRINGS:
            self.lang = DEFAULT_LANG
        self.t = lambda k: STRINGS[self.lang].get(k, k)

        self.state = GsiState()
        self.server_thread: GsiServerThread | None = None
        self.server_port: int | None = None
        self.hotkey_mgr = HotkeyManager(self.state, self._log)
        self._capturing = False
        self.rule_rows: list[dict] = []  # {active: QComboBox, target: QComboBox}
        self.slot_edits: dict[str, QLineEdit] = {}

        self._build_ui()
        self._apply_settings_to_logic()
        self._retranslate()

        self.log_signal.connect(self._append_log)
        self.hotkey_captured.connect(self._on_hotkey_captured)

        # автостарт сервера при запуске
        self.start_server(self.settings.get("port", DEFAULT_PORT), silent=False)
        hotkey = self.settings.get("hotkey", DEFAULT_HOTKEY)
        self.hotkey_edit.setText(hotkey)
        self.hotkey_mgr.start(hotkey)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh_status)
        self.timer.start(500)

        # первый запуск: если GSI-конфига нет — ставим сами и показываем
        # попап про перезапуск игры (иначе round.phase не прилетит никогда)
        QTimer.singleShot(600, self._first_run_check)

    # --- UI ---

    def _build_ui(self):
        self.setWindowTitle(self.t("title"))
        if os.path.isfile(ICON_PATH):
            self.setWindowIcon(QIcon(ICON_PATH))

        central = QWidget()
        self.setCentralWidget(central)
        main = QVBoxLayout(central)
        main.setSpacing(6)

        # хоткей
        row_hotkey = QHBoxLayout()
        self.lbl_hotkey = QLabel()
        self.hotkey_edit = QLineEdit()
        self.hotkey_edit.setReadOnly(True)
        self.btn_hotkey = QPushButton()
        self.btn_hotkey.clicked.connect(self._capture_hotkey)
        row_hotkey.addWidget(self.lbl_hotkey)
        row_hotkey.addWidget(self.hotkey_edit, 1)
        row_hotkey.addWidget(self.btn_hotkey)
        main.addLayout(row_hotkey)

        # порт + сервер
        row_port = QHBoxLayout()
        self.lbl_port = QLabel()
        self.spin_port = QSpinBox()
        self.spin_port.setRange(1024, 65535)
        self.spin_port.setValue(int(self.settings.get("port", DEFAULT_PORT)))
        self.btn_start = QPushButton()
        self.btn_restart = QPushButton()
        self.btn_stop = QPushButton()
        self.btn_start.clicked.connect(lambda: self.start_server(self.spin_port.value()))
        self.btn_restart.clicked.connect(lambda: self.start_server(self.spin_port.value(), restart=True))
        self.btn_stop.clicked.connect(self.stop_server)
        row_port.addWidget(self.lbl_port)
        row_port.addWidget(self.spin_port)
        row_port.addWidget(self.btn_start)
        row_port.addWidget(self.btn_restart)
        row_port.addWidget(self.btn_stop)
        main.addLayout(row_port)

        # статусы
        row_status = QHBoxLayout()
        self.lbl_server_status = QLabel()
        self.lbl_gsi_status = QLabel()
        self.lbl_gsi_status.setTextFormat(Qt.TextFormat.PlainText)
        row_status.addWidget(self.lbl_server_status)
        row_status.addWidget(self.lbl_gsi_status, 1)
        main.addLayout(row_status)

        # правила
        self.grp_rules = QGroupBox()
        rules_layout = QVBoxLayout(self.grp_rules)
        rules_header = QHBoxLayout()
        self.lbl_rule_if = QLabel()
        self.lbl_rule_then = QLabel()
        rules_header.addWidget(self.lbl_rule_if, 1)
        rules_header.addWidget(self.lbl_rule_then, 1)
        rules_header.addSpacing(40)
        rules_layout.addLayout(rules_header)
        self.rules_container = QVBoxLayout()
        rules_layout.addLayout(self.rules_container)
        self.btn_add_rule = QPushButton()
        self.btn_add_rule.clicked.connect(lambda: self.add_rule_row())
        rules_layout.addWidget(self.btn_add_rule)
        main.addWidget(self.grp_rules)

        # клавиши слотов
        self.grp_slots = QGroupBox()
        slots_outer = QVBoxLayout(self.grp_slots)
        self.slots_grid = QGridLayout()
        slots_outer.addLayout(self.slots_grid)
        for i, slot in enumerate(SLOT_IDS):
            lbl = QLabel()
            lbl.setProperty("slot", slot)
            edit = QLineEdit()
            edit.setMaximumWidth(90)
            edit.setProperty("slot", slot)
            edit.editingFinished.connect(self._on_slot_keys_changed)
            self.slot_edits[slot] = edit
            self.slots_grid.addWidget(lbl, i // 3, (i % 3) * 2)
            self.slots_grid.addWidget(edit, i // 3, (i % 3) * 2 + 1)
        self.btn_slots_reset = QPushButton()
        self.btn_slots_reset.clicked.connect(self._reset_slot_keys)
        self.btn_slots_write = QPushButton()
        self.btn_slots_write.clicked.connect(self._write_binds)
        row_slots_btns = QHBoxLayout()
        row_slots_btns.addWidget(self.btn_slots_reset)
        row_slots_btns.addWidget(self.btn_slots_write)
        slots_outer.addLayout(row_slots_btns)
        main.addWidget(self.grp_slots)

        # консоль
        self.lbl_console = QLabel()
        main.addWidget(self.lbl_console)
        self.console = QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setMaximumBlockCount(500)
        self.console.setFixedHeight(110)
        main.addWidget(self.console)

        # низ
        row_bottom = QHBoxLayout()
        self.btn_install = QPushButton()
        self.btn_install.clicked.connect(self._install_cfg)
        self.lbl_lang = QLabel()
        self.combo_lang = QComboBox()
        self.combo_lang.addItem("Русский", "ru")
        self.combo_lang.addItem("English", "en")
        self.combo_lang.setCurrentIndex(0 if self.lang == "ru" else 1)
        self.combo_lang.currentIndexChanged.connect(self._on_lang_changed)
        self.btn_tray = QPushButton()
        self.btn_tray.clicked.connect(self._hide_to_tray)
        row_bottom.addWidget(self.btn_install)
        row_bottom.addWidget(self.lbl_lang)
        row_bottom.addWidget(self.combo_lang)
        row_bottom.addWidget(self.btn_tray)
        main.addLayout(row_bottom)

        # трей
        self.tray = QSystemTrayIcon(self)
        if os.path.isfile(ICON_PATH):
            self.tray.setIcon(QIcon(ICON_PATH))
        menu = QMenu()
        self.act_show = QAction()
        self.act_show.triggered.connect(self._show_from_tray)
        self.act_exit = QAction()
        self.act_exit.triggered.connect(self._quit_from_tray)
        menu.addAction(self.act_show)
        menu.addAction(self.act_exit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)

        rules = self.settings.get("rules") or [dict(r) for r in DEFAULT_RULES]
        for r in rules[:20]:
            self.add_rule_row(r.get("active"), r.get("target"))

    def _retranslate(self):
        self.setWindowTitle(self.t("title"))
        self.lbl_hotkey.setText(self.t("hotkey_label"))
        self.btn_hotkey.setText(self.t("hotkey_change"))
        self.lbl_port.setText(self.t("port_label"))
        self.btn_start.setText(self.t("server_start"))
        self.btn_restart.setText(self.t("server_restart"))
        self.btn_stop.setText(self.t("server_stop"))
        self.grp_rules.setTitle(self.t("rules_title"))
        self.lbl_rule_if.setText(self.t("rules_if"))
        self.lbl_rule_then.setText(self.t("rules_then"))
        self.btn_add_rule.setText(self.t("rules_add"))
        self.grp_slots.setTitle(self.t("slotkeys_title"))
        self.btn_slots_reset.setText(self.t("slotkeys_reset"))
        self.btn_slots_write.setText(self.t("slotkeys_write"))
        self.lbl_console.setText(self.t("console_label"))
        self.btn_install.setText(self.t("install_cfg"))
        self.lbl_lang.setText(self.t("lang_label"))
        self.btn_tray.setText(self.t("minimize_tray"))
        self.act_show.setText(self.t("tray_show"))
        self.act_exit.setText(self.t("tray_exit"))
        # подписи слотов + комбобоксы правил
        for lbl in self.findChildren(QLabel):
            slot = lbl.property("slot")
            if slot:
                lbl.setText(slot_label(slot, self.lang))
        for row in self.rule_rows:
            for combo in (row["active"], row["target"]):
                cur = combo.currentData()
                combo.blockSignals(True)
                combo.clear()
                for slot in SLOT_IDS:
                    combo.addItem(slot_label(slot, self.lang), slot)
                if cur in SLOT_IDS:
                    combo.setCurrentIndex(SLOT_IDS.index(cur))
                combo.blockSignals(False)
        self._refresh_status()

    # --- настройки ---

    def _apply_settings_to_logic(self):
        rules = self.settings.get("rules")
        if isinstance(rules, list) and rules:
            self.hotkey_mgr.set_rules(rules)
        slot_keys = self.settings.get("slot_keys") or {}
        for slot in SLOT_IDS:
            val = slot_keys.get(slot, SLOT_KEYS_DEFAULT.get(slot, ""))
            self.hotkey_mgr.set_slot_key(slot, val)
            if slot in self.slot_edits:
                self.slot_edits[slot].setText(val)

    def _collect_settings(self) -> dict:
        return {
            "hotkey": self.hotkey_edit.text().strip(),
            "port": self.spin_port.value(),
            "lang": self.lang,
            "rules": [
                {"active": r["active"].currentData(), "target": r["target"].currentData()}
                for r in self.rule_rows
            ],
            "slot_keys": {s: e.text().strip() for s, e in self.slot_edits.items()},
        }

    def _persist(self):
        save_settings(self._collect_settings())

    # --- правила ---

    def add_rule_row(self, active: str | None = None, target: str | None = None):
        if len(self.rule_rows) >= 20:
            return
        row_widget = QWidget()
        layout = QHBoxLayout(row_widget)
        layout.setContentsMargins(0, 0, 0, 0)
        cb_active = QComboBox()
        cb_target = QComboBox()
        for slot in SLOT_IDS:
            cb_active.addItem(slot_label(slot, self.lang), slot)
            cb_target.addItem(slot_label(slot, self.lang), slot)
        cb_active.setCurrentIndex(SLOT_IDS.index(active) if active in SLOT_IDS else 0)
        cb_target.setCurrentIndex(SLOT_IDS.index(target) if target in SLOT_IDS else 1)
        cb_active.currentIndexChanged.connect(self._on_rules_changed)
        cb_target.currentIndexChanged.connect(self._on_rules_changed)
        btn_del = QPushButton("✕")
        btn_del.setFixedWidth(36)
        layout.addWidget(cb_active, 1)
        layout.addWidget(cb_target, 1)
        layout.addWidget(btn_del)
        self.rules_container.addWidget(row_widget)
        row = {"widget": row_widget, "active": cb_active, "target": cb_target}
        btn_del.clicked.connect(lambda: self._remove_rule_row(row))
        self.rule_rows.append(row)
        self._on_rules_changed()

    def _remove_rule_row(self, row: dict):
        if row in self.rule_rows:
            self.rule_rows.remove(row)
            row["widget"].deleteLater()
            self._on_rules_changed()

    def _on_rules_changed(self):
        rules = []
        for r in self.rule_rows:
            a, t = r["active"].currentData(), r["target"].currentData()
            if a == t:
                self._log(self.t("same_slot_error"))
                # сдвигаем target на следующий слот чтобы не было петли
                idx = (SLOT_IDS.index(t) + 1) % len(SLOT_IDS)
                r["target"].blockSignals(True)
                r["target"].setCurrentIndex(idx)
                r["target"].blockSignals(False)
                t = r["target"].currentData()
            rules.append({"active": a, "target": t})
        self.hotkey_mgr.set_rules(rules)
        self._persist()

    # --- клавиши слотов ---

    def _on_slot_keys_changed(self):
        for slot, edit in self.slot_edits.items():
            self.hotkey_mgr.set_slot_key(slot, edit.text())
        self._persist()

    def _reset_slot_keys(self):
        for slot, edit in self.slot_edits.items():
            edit.setText(SLOT_KEYS_DEFAULT.get(slot, ""))
        self._on_slot_keys_changed()

    # --- хоткей ---

    def _capture_hotkey(self):
        if self._capturing:
            return
        self._capturing = True
        self._prev_hotkey = self.hotkey_edit.text().strip()
        # останавливаем старый хук чтобы он не срабатывал во время захвата
        try:
            self.hotkey_mgr.stop()
        except Exception:  # noqa: BLE001
            pass
        self.btn_hotkey.setText(self.t("hotkey_capture"))

        def worker():
            try:
                import keyboard

                hk = keyboard.read_hotkey(suppress=False)
                self.hotkey_captured.emit(hk or "")
            except Exception as e:  # noqa: BLE001
                self.log_signal.emit(f"!! Захват клавиши не удался: {e}")
                self.hotkey_captured.emit("")

        threading.Thread(target=worker, daemon=True).start()

    def _on_hotkey_captured(self, hk: str):
        self._capturing = False
        self.btn_hotkey.setText(self.t("hotkey_change"))
        hk = (hk or "").strip()
        if not hk or hk.lower() == "esc":
            # отмена — возвращаем старый хоткей
            if getattr(self, "_prev_hotkey", ""):
                self.hotkey_mgr.start(self._prev_hotkey)
            return  # отмена
        self.hotkey_edit.setText(hk)
        self.hotkey_mgr.start(hk)
        self._persist()

    # --- сервер ---

    def start_server(self, port: int, restart: bool = False, silent: bool = False):
        self.stop_server(silent=True)
        state = self.state
        thread = GsiServerThread(state, self._log, port, GSI_PATH)
        thread.start()
        self.server_thread = thread
        self.server_port = port
        self.spin_port.setValue(port)
        if not silent:
            action = "Рестарт" if restart else "Старт"
            self._log(f"{action} сервера на порту {port}")
        self._persist()
        self._refresh_status()

    def stop_server(self, silent: bool = False):
        if self.server_thread:
            self.server_thread.stop()
            self.server_thread.join(timeout=2.0)
            self.server_thread = None
            self.server_port = None
            if not silent:
                self._log("Сервер остановлен")
        self._refresh_status()

    @property
    def server_running(self) -> bool:
        return self.server_thread is not None and self.server_thread.is_alive()

    # --- GSI конфиг ---

    def _install_cfg(self):
        port = self.spin_port.value()
        cs2_root = find_cs2_root()
        if not cs2_root:
            QMessageBox.warning(self, "QSmartSwap", self.t("err_no_steam"))
            chosen = QFileDialog.getExistingDirectory(self, self.t("choose_cs2_folder"), os.path.expanduser("~"))
            if not chosen:
                return
            cs2_root = chosen
        try:
            cfg_dir = get_cfg_dir(cs2_root)
            target = install_config(cfg_dir, port, GSI_PATH)
        except OSError as e:
            QMessageBox.critical(self, "QSmartSwap", str(e))
            return
        QMessageBox.information(self, self.t("popup_title"), self.t("popup_text").format(path=target))
        self._log(f"GSI-конфиг записан: {target} (порт {port})")

    def _first_run_check(self):
        """При первом запуске: авто-установка GSI + попап про перезапуск игры."""
        if self.settings.get("cfg_autoinstalled"):
            return
        try:
            cs2_root = find_cs2_root()
        except Exception:  # noqa: BLE001
            cs2_root = None
        if not cs2_root:
            self._log("Первый запуск: CS2 не найден — поставь GSI-конфиг кнопкой позже")
            return
        try:
            cfg_dir = get_cfg_dir(cs2_root)
            if is_config_installed(cfg_dir):
                # конфиг уже есть, но мог быть старого формата без round —
                # перезаписываем под актуальный порт чтобы round.phase точно летел
                target = install_config(cfg_dir, self.spin_port.value(), GSI_PATH)
            else:
                target = install_config(cfg_dir, self.spin_port.value(), GSI_PATH)
        except OSError as e:
            self._log(f"Первый запуск: не смог записать GSI-конфиг: {e}")
            return
        self.settings["cfg_autoinstalled"] = True
        self._persist()
        QMessageBox.information(self, self.t("popup_title"), self.t("popup_text").format(path=target))
        self._log(f"GSI-конфиг записан: {target} (порт {self.spin_port.value()})")

    def _write_binds(self):
        """Кнопка 'Забиндить слоты': пишет qsmartswap_binds.cfg в cfg CS2."""
        slot_keys = {s: e.text().strip() for s, e in self.slot_edits.items()}
        cs2_root = find_cs2_root()
        if not cs2_root:
            QMessageBox.warning(self, "QSmartSwap", self.t("err_no_steam"))
            chosen = QFileDialog.getExistingDirectory(self, self.t("choose_cs2_folder"), os.path.expanduser("~"))
            if not chosen:
                return
            cs2_root = chosen
        try:
            cfg_dir = get_cfg_dir(cs2_root)
            from gsi_config import BINDS_FILENAME  # noqa: F401  (для читаемости пути в логе)

            target, warnings = install_binds(cfg_dir, slot_keys, VALVE_MAP)
        except OSError as e:
            QMessageBox.critical(self, "QSmartSwap", str(e))
            return
        warn_text = ("\n" + "\n".join(f"! {w}" for w in warnings)) if warnings else ""
        QMessageBox.information(
            self, self.t("binds_title"), self.t("binds_text").format(path=target, warnings=warn_text)
        )
        self._log(f"Бинды записаны: {target}")
        for w in warnings:
            self._log(f"! {w}")

    # --- лог/статус ---

    def _log(self, msg: str):
        try:
            self.log_signal.emit(msg)
        except RuntimeError:
            print(msg)

    def _append_log(self, msg: str):
        self.console.appendPlainText(msg)

    def _refresh_status(self):
        if self.server_running:
            self.lbl_server_status.setText(self.t("status_server_on").format(port=self.server_port))
        else:
            self.lbl_server_status.setText(self.t("status_server_off"))
        snap = self.state.snapshot()
        phase = snap.get("round_phase") or "-"
        if snap["connected"] and snap["active_slot"]:
            owned = ",".join(snap["owned"]) or "-"
            base = self.t("status_gsi_ok").format(active=snap["active_slot"], owned=owned)
            self.lbl_gsi_status.setText(f"{base} | round={phase}")
        else:
            self.lbl_gsi_status.setText(f"{self.t('status_gsi_wait')} (round={phase})")

    # --- язык/трей/выход ---

    def _on_lang_changed(self, idx: int):
        self.lang = self.combo_lang.itemData(idx) or "ru"
        self.t = lambda k: STRINGS[self.lang].get(k, k)
        self._retranslate()
        self._persist()

    def _hide_to_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            QMessageBox.warning(self, "QSmartSwap", "Tray недоступен")
            return
        self.tray.show()
        self.hide()
        self.tray.showMessage("QSmartSwap", self.t("tray_hidden"), QSystemTrayIcon.MessageIcon.Information, 2000)

    def _show_from_tray(self):
        self.showNormal()
        self.activateWindow()
        self.tray.hide()

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._show_from_tray()

    def _quit_from_tray(self):
        self.tray.hide()
        self.close()

    def closeEvent(self, event):
        self._persist()
        try:
            self.hotkey_mgr.stop()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.stop_server(silent=True)
        except Exception:  # noqa: BLE001
            pass
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)
    win = MainWindow()
    win.resize(600, 720)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
