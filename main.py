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
from PyQt6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
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

from gsi_config import (
    GSI_PATH,
    build_cfg_content,
    install_binds,
    install_config,
    is_config_installed,
    read_config_text,
    remove_legacy_configs,
)
from gsi_server import LOGICAL_SLOTS, SLOT_IDS, VALVE_MAP, GsiServerThread, GsiState
from hotkey_logic import DEFAULT_RULES, SLOT_KEYS_DEFAULT, HotkeyManager
from native_hook import capture_combo
from scancodes import combo_display, migrate_legacy_code
from i18n import STRINGS, slot_label
from game_watch import is_game_running
from steam_locate import find_cs2_root, get_cfg_dir

if getattr(sys, "frozen", False):
    # PyInstaller --onefile: __file__ does not exist, settings live next to the exe
    BASE_DIR = os.path.dirname(sys.executable)
    _MEIPASS = getattr(sys, "_MEIPASS", None)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    _MEIPASS = None


def resource_path(name: str) -> str:
    """Find a bundled asset: exe dir first, then PyInstaller dirs.

    NOTE: with --onedir, --add-data lands in <exe>/_internal/, NOT next to
    the exe (and sys._MEIPASS == exe dir there), so _internal must be probed
    explicitly — otherwise the tray ends up with a null (invisible) icon.
    """
    search = []
    for base in (BASE_DIR, _MEIPASS):
        if base:
            search.append(base)
            search.append(os.path.join(base, "_internal"))
    for base in search:
        candidate = os.path.join(base, name)
        if os.path.isfile(candidate):
            return candidate
    return os.path.join(BASE_DIR, name)


def find_icon() -> str:
    for name in ("icon.ico", "icon.png"):
        path = resource_path(name)
        if os.path.isfile(path):
            return path
    return os.path.join(BASE_DIR, "icon.ico")


ICON_PATH = find_icon()

_app_icon: QIcon | None = None


def app_icon() -> QIcon:
    """Window/tray icon that is NEVER null: falls back to a drawn badge
    if no icon file is found in the bundle."""
    global _app_icon
    if _app_icon is None or _app_icon.isNull():
        icon = QIcon(ICON_PATH) if os.path.isfile(ICON_PATH) else QIcon()
        if icon.isNull():
            pm = QPixmap(64, 64)
            pm.fill(QColor("#181818"))
            painter = QPainter(pm)
            painter.setPen(QColor("#ff7a00"))
            painter.setFont(QFont("Arial", 40, QFont.Weight.Bold))
            painter.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, "Q")
            painter.end()
            icon = QIcon(pm)
        _app_icon = icon
    return _app_icon
# QSMARTSWAP_CONFIG override exists so tests never touch the real config.json
CONFIG_JSON = os.environ.get("QSMARTSWAP_CONFIG", os.path.join(BASE_DIR, "config.json"))
DEFAULT_PORT = 7777
DEFAULT_HOTKEY = "q"
DEFAULT_KILL_HOTKEY = "ctrl+end"
DEFAULT_LANG = "en"
APP_VERSION = "1.1.0"


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
    except OSError as e:
        print(f"!! Failed to save settings to {CONFIG_JSON}: {e}")


def _clean_codes(groups, log_callback=None):
    """Sanitize and migrate stored scan-code groups to [{"scan": int, "extended": bool}]."""
    if not isinstance(groups, list) or not groups:
        return None
    out = []
    for g in groups:
        items = g if isinstance(g, list) else [g]
        cleaned_group = []
        for c in items:
            if isinstance(c, dict) and "scan" in c and "extended" in c:
                cleaned_group.append({"scan": c["scan"], "extended": bool(c["extended"])})
            elif isinstance(c, int):
                # Legacy migration
                scan, extended, ambiguous = migrate_legacy_code(c)
                if ambiguous and log_callback:
                    log_callback(f"⚠️ Migrated ambiguous legacy scan code {c} to scan={scan}, extended={extended}.")
                cleaned_group.append({"scan": scan, "extended": extended})
        if not cleaned_group:
            return None
        out.append(cleaned_group)
    return out


class CollapsibleBox(QWidget):
    def __init__(self, title="", parent=None, is_expanded=False):
        super().__init__(parent)
        self.toggle_button = QPushButton(title)
        self.toggle_button.setCheckable(True)
        self.toggle_button.setChecked(is_expanded)
        self.toggle_button.setStyleSheet("text-align: left; padding: 5px; font-weight: bold;")
        self.toggle_button.toggled.connect(self.on_toggle)
        
        self.content_area = QWidget()
        self.content_layout = QVBoxLayout(self.content_area)
        self.content_layout.setContentsMargins(0, 5, 0, 0)
        self.content_area.setVisible(is_expanded)
        
        lay = QVBoxLayout(self)
        lay.setSpacing(0)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.toggle_button)
        lay.addWidget(self.content_area)
        
    def on_toggle(self, checked):
        self.content_area.setVisible(checked)
        
    def setTitle(self, title):
        self.toggle_button.setText(title)


class MainWindow(QMainWindow):
    log_signal = pyqtSignal(str)
    hotkey_captured = pyqtSignal(str, object)
    quit_signal = pyqtSignal()

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
        self.hotkey_mgr = HotkeyManager(self.state, self._log, kill_callback=self._emit_quit)
        self._capturing = False
        self._capture_target = "main"
        self._captured_codes = None
        # exact scan-code groups from capture (None = legacy string bind)
        self._main_codes = _clean_codes(self.settings.get("hotkey_sc"), self._log_no_emit)
        self._kill_codes = _clean_codes(self.settings.get("kill_sc"), self._log_no_emit)
        self.rule_rows: list[dict] = []  # {active: QComboBox, target: QComboBox}
        self.slot_edits: dict[str, QLineEdit] = {}
        # guard: while UI is being built the fields are still empty —
        # _persist() must not clobber settings with those empties
        self._loading = True

        self._build_ui()
        self._apply_settings_to_logic()
        self._retranslate()

        self.log_signal.connect(self._append_log)
        self.hotkey_captured.connect(self._on_hotkey_captured)
        self.quit_signal.connect(self._on_kill_hotkey)

        # автостарт сервера при запуске
        self.start_server(self.settings.get("port", DEFAULT_PORT), silent=False)
        hotkey = self.settings.get("hotkey") or DEFAULT_HOTKEY
        kill_hotkey = self.settings.get("kill_hotkey") or DEFAULT_KILL_HOTKEY
        self.hotkey_edit.setText(hotkey)
        self.kill_edit.setText(kill_hotkey)
        self.hotkey_mgr.start(hotkey, kill_hotkey, self._main_codes, self._kill_codes)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh_status)
        self.timer.start(500)

        # вотчер игры: выходим сами, когда cs2.exe закрылся (если галочка вкл).
        # _game_was_seen страхует от выхода при старте, когда игры ещё нет.
        self._game_was_seen = False
        self.game_timer = QTimer(self)
        self.game_timer.timeout.connect(self._poll_game_exit)
        self.game_timer.start(2000)

        # первый запуск: если GSI-конфига нет — ставим сами и показываем
        # попап про перезапуск игры (иначе round.phase не прилетит никогда)
        QTimer.singleShot(600, self._first_run_check)

        self._loading = False

    def _log_no_emit(self, msg: str):
        print(msg)

    # --- UI ---

    def _build_ui(self):
        self.setWindowTitle(f"{self.t('title')} v{APP_VERSION}")
        self.setWindowIcon(app_icon())

        central = QWidget()
        self.setCentralWidget(central)
        main = QVBoxLayout(central)
        main.setSizeConstraint(QVBoxLayout.SizeConstraint.SetFixedSize)
        main.setSpacing(6)
        
        # Накладываем ограничение и на компоновщик самого окна, чтобы оно сжималось нативно
        self.layout().setSizeConstraint(QVBoxLayout.SizeConstraint.SetFixedSize)

        # хоткей
        row_hotkey = QHBoxLayout()
        self.lbl_hotkey = QLabel()
        self.hotkey_edit = QLineEdit()
        self.hotkey_edit.setReadOnly(True)
        self.btn_hotkey = QPushButton()
        self.btn_hotkey.clicked.connect(lambda: self._capture_hotkey("main"))
        row_hotkey.addWidget(self.lbl_hotkey)
        row_hotkey.addWidget(self.hotkey_edit, 1)
        row_hotkey.addWidget(self.btn_hotkey)
        main.addLayout(row_hotkey)

        # килл-бинд: выход из программы
        row_kill = QHBoxLayout()
        self.lbl_kill = QLabel()
        self.kill_edit = QLineEdit()
        self.kill_edit.setReadOnly(True)
        self.btn_kill = QPushButton()
        self.btn_kill.clicked.connect(lambda: self._capture_hotkey("kill"))
        row_kill.addWidget(self.lbl_kill)
        row_kill.addWidget(self.kill_edit, 1)
        row_kill.addWidget(self.btn_kill)
        main.addLayout(row_kill)

        # автовыход вместе с игрой — галочка рядом с киллбиндом
        row_exit = QHBoxLayout()
        self.chk_autoexit = QCheckBox()
        self.chk_autoexit.setChecked(bool(self.settings.get("autoexit_on_game_close", False)))
        self.chk_autoexit.stateChanged.connect(self._on_autoexit_changed)
        row_exit.addWidget(self.chk_autoexit)
        row_exit.addStretch(1)
        main.addLayout(row_exit)

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
        self.grp_rules = CollapsibleBox(is_expanded=self.settings.get("ui_rules_open", True))
        self.grp_rules.toggle_button.toggled.connect(lambda: self._persist())
        rules_layout = self.grp_rules.content_layout
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
        self.chk_fallback = QCheckBox()
        self.chk_fallback.setChecked(self.settings.get("fallback_primary", True))
        self.chk_fallback.stateChanged.connect(self._on_fallback_changed)
        rules_layout.addWidget(self.chk_fallback)
        row_pf = QHBoxLayout()
        self.lbl_pf = QLabel()
        self.combo_pf = QComboBox()
        self.combo_pf.addItem("", "primary")
        self.combo_pf.addItem("", "knife")
        self.combo_pf.addItem("", "nothing")
        pf_val = self.settings.get("pistol_fallback", "primary")
        idx = self.combo_pf.findData(pf_val)
        self.combo_pf.setCurrentIndex(idx if idx >= 0 else 0)
        self.combo_pf.currentIndexChanged.connect(self._on_pistol_fallback_changed)
        row_pf.addWidget(self.lbl_pf)
        row_pf.addWidget(self.combo_pf, 1)
        rules_layout.addLayout(row_pf)
        main.addWidget(self.grp_rules)

        # клавиши слотов
        self.grp_slots = CollapsibleBox(is_expanded=self.settings.get("ui_slots_open", False))
        self.grp_slots.toggle_button.toggled.connect(lambda: self._persist())
        slots_outer = self.grp_slots.content_layout
        self.slots_grid = QGridLayout()
        slots_outer.addLayout(self.slots_grid)
        for i, slot in enumerate(SLOT_IDS):
            lbl = QLabel()
            lbl.setProperty("slot", slot)
            edit = QLineEdit()
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
        self.grp_console = CollapsibleBox(is_expanded=self.settings.get("ui_console_open", False))
        self.grp_console.toggle_button.toggled.connect(lambda: self._persist())
        self.console = QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setMaximumBlockCount(500)
        self.console.setMinimumHeight(110)
        self.grp_console.content_layout.addWidget(self.console)
        main.addWidget(self.grp_console)

        # Settings
        self.grp_settings = CollapsibleBox(is_expanded=self.settings.get("ui_settings_open", False))
        self.grp_settings.toggle_button.toggled.connect(lambda: self._persist())
        
        self.chk_start_minimized = QCheckBox("Start Minimized to Tray")
        self.chk_start_minimized.setChecked(self.settings.get("start_minimized", False))
        self.chk_start_minimized.stateChanged.connect(lambda: self._persist())
        self.grp_settings.content_layout.addWidget(self.chk_start_minimized)
        
        row_cs2 = QHBoxLayout()
        self.lbl_cs2_path = QLabel("CS2 Path:")
        self.edit_cs2_path = QLineEdit(self.settings.get("cs2_path", ""))
        self.edit_cs2_path.setPlaceholderText("Auto-detect (or browse...)")
        self.edit_cs2_path.editingFinished.connect(lambda: self._persist())
        self.btn_cs2_browse = QPushButton("Browse...")
        self.btn_cs2_browse.clicked.connect(self._browse_cs2_path)
        self.btn_cs2_detect = QPushButton("Auto")
        self.btn_cs2_detect.clicked.connect(self._detect_cs2_path)
        row_cs2.addWidget(self.lbl_cs2_path)
        row_cs2.addWidget(self.edit_cs2_path, 1)
        row_cs2.addWidget(self.btn_cs2_browse)
        row_cs2.addWidget(self.btn_cs2_detect)
        self.grp_settings.content_layout.addLayout(row_cs2)
        main.addWidget(self.grp_settings)

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
        self.tray.setIcon(app_icon())
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
        self.setWindowTitle(f"{self.t('title')} v{APP_VERSION}")
        self.lbl_hotkey.setText(self.t("hotkey_label"))
        self.btn_hotkey.setText(self.t("hotkey_change"))
        self.lbl_kill.setText(self.t("kill_label"))
        self.btn_kill.setText(self.t("hotkey_change"))
        self.chk_autoexit.setText(self.t("autoexit"))
        self.lbl_port.setText(self.t("port_label"))
        self.btn_start.setText(self.t("server_start"))
        self.btn_restart.setText(self.t("server_restart"))
        self.btn_stop.setText(self.t("server_stop"))
        self.grp_rules.setTitle(self.t("rules_title"))
        self.lbl_rule_if.setText(self.t("rules_if"))
        self.lbl_rule_then.setText(self.t("rules_then"))
        self.btn_add_rule.setText(self.t("rules_add"))
        self.chk_fallback.setText(self.t("fallback_primary"))
        self.lbl_pf.setText(self.t("pistol_fallback_label"))
        for idx in range(self.combo_pf.count()):
            data = self.combo_pf.itemData(idx)
            strkey = {"primary": "pf_primary", "knife": "pf_knife", "nothing": "pf_nothing"}.get(data, "")
            if strkey:
                self.combo_pf.setItemText(idx, self.t(strkey))
        self.grp_slots.setTitle(self.t("slotkeys_title"))
        self.btn_slots_reset.setText(self.t("slotkeys_reset"))
        self.btn_slots_write.setText(self.t("slotkeys_write"))
        self.grp_console.setTitle(self.t("console_label"))
        self.grp_settings.setTitle("Settings")
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
        # migration from the old viceversa flag
        if "viceversa" in self.settings and "fallback_primary" not in self.settings:
            vv = bool(self.settings["viceversa"])
            self.settings["fallback_primary"] = vv
            if "pistol_fallback" not in self.settings:
                self.settings["pistol_fallback"] = "primary" if vv else "nothing"
        self.hotkey_mgr.fallback_primary = bool(self.settings.get("fallback_primary", True))
        self.hotkey_mgr.pistol_fallback = self.settings.get("pistol_fallback", "primary")
        self.chk_fallback.blockSignals(True)
        self.chk_fallback.setChecked(self.hotkey_mgr.fallback_primary)
        self.chk_fallback.blockSignals(False)
        idx = self.combo_pf.findData(self.hotkey_mgr.pistol_fallback)
        self.combo_pf.blockSignals(True)
        self.combo_pf.setCurrentIndex(idx if idx >= 0 else 0)
        self.combo_pf.blockSignals(False)
        self.chk_autoexit.blockSignals(True)
        self.chk_autoexit.setChecked(bool(self.settings.get("autoexit_on_game_close", False)))
        self.chk_autoexit.blockSignals(False)
        slot_keys = self.settings.get("slot_keys") or {}
        for slot in SLOT_IDS:
            # `or` (not get-with-default): heals configs wiped by the old
            # init-order bug, where empty fields overwrote real values
            val = slot_keys.get(slot) or SLOT_KEYS_DEFAULT.get(slot, "")
            self.hotkey_mgr.set_slot_key(slot, val)
            if slot in self.slot_edits:
                self.slot_edits[slot].setText(val)

    def _collect_settings(self) -> dict:
        return {
            "hotkey": self.hotkey_edit.text().strip(),
            "kill_hotkey": self.kill_edit.text().strip(),
            "hotkey_sc": self._main_codes,
            "kill_sc": self._kill_codes,
            "port": self.spin_port.value(),
            "lang": self.lang,
            "fallback_primary": self.chk_fallback.isChecked(),
            "pistol_fallback": self.combo_pf.currentData() or "nothing",
            "autoexit_on_game_close": self.chk_autoexit.isChecked(),
            "rules": [
                {"active": r["active"].currentData(), "target": r["target"].currentData()}
                for r in self.rule_rows
            ],
            "slot_keys": {s: e.text().strip() for s, e in self.slot_edits.items()},
            "ui_rules_open": self.grp_rules.toggle_button.isChecked(),
            "ui_slots_open": self.grp_slots.toggle_button.isChecked(),
            "ui_console_open": self.grp_console.toggle_button.isChecked(),
            "ui_settings_open": self.grp_settings.toggle_button.isChecked(),
            "cs2_path": self.edit_cs2_path.text().strip(),
            "start_minimized": self.chk_start_minimized.isChecked(),
        }

    def _persist(self):
        if getattr(self, "_loading", False):
            return  # init not finished — fields are still empty, don't clobber settings
        # merge, not replace — otherwise extra flags (cfg_autoinstalled) are lost
        self.settings.update(self._collect_settings())
        self.settings.pop("viceversa", None)  # legacy flag, migrated
        save_settings(self.settings)

    def _on_fallback_changed(self):
        self.hotkey_mgr.fallback_primary = self.chk_fallback.isChecked()
        self._log(f"Fallback primary->secondary {'ON' if self.hotkey_mgr.fallback_primary else 'OFF'}")
        self._persist()

    def _on_pistol_fallback_changed(self):
        self.hotkey_mgr.pistol_fallback = self.combo_pf.currentData() or "nothing"
        self._log(f"Pistol fallback: {self.hotkey_mgr.pistol_fallback}")
        self._persist()

    def _on_autoexit_changed(self):
        on = self.chk_autoexit.isChecked()
        self._log(f"Auto-exit on game close {'ON' if on else 'OFF'}")
        self._persist()

    def _poll_game_exit(self):
        """Раз в 2 сек: игра была и пропала + галочка вкл → закрываемся."""
        try:
            running = is_game_running()
        except Exception:  # noqa: BLE001
            return
        if running:
            self._game_was_seen = True
            return
        if self._game_was_seen and self.chk_autoexit.isChecked():
            self._log("Game process ended — auto-exit enabled, quitting")
            self.close()

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
                # console output stays English regardless of UI language
                self._log("Rule slots must differ — target shifted")
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

    def _capture_hotkey(self, target: str = "main"):
        if self._capturing:
            return
        self._capturing = True
        self._capture_target = target
        self._prev_hotkey = self.hotkey_edit.text().strip()
        self._prev_kill = self.kill_edit.text().strip()
        self._prev_main_codes = list(self._main_codes or [])
        self._prev_kill_codes = list(self._kill_codes or [])
        self._captured_codes = None
        # останавливаем старый хук чтобы он не срабатывал во время захвата
        self.hotkey_mgr.stop()
        
        btn = self.btn_kill if target == "kill" else self.btn_hotkey
        btn.setText(self.t("hotkey_capture"))

        def worker():
            try:
                # scan-code capture: left/right/numpad are distinct binds
                codes = capture_combo(timeout=30.0)
                if codes is None:
                    self.hotkey_captured.emit("", None)
                else:
                    # Convert list of tuples to list of dicts for storage
                    stored_codes = [{"scan": sc, "extended": ext} for sc, ext in codes]
                    self.hotkey_captured.emit(combo_display(codes, lang=self.lang), [stored_codes])
            except Exception as e:  # noqa: BLE001
                self.log_signal.emit(f"!! Hotkey capture failed: {e}")
                self.hotkey_captured.emit("", None)

        threading.Thread(target=worker, daemon=True).start()

    def _on_hotkey_captured(self, hk: str, codes: object):
        self._capturing = False
        self.btn_hotkey.setText(self.t("hotkey_change"))
        self.btn_kill.setText(self.t("hotkey_change"))
        hk = (hk or "").strip()
        if not hk or not codes:
            # отмена — возвращаем старые хоткеи
            self.hotkey_mgr.start(
                getattr(self, "_prev_hotkey", "") or DEFAULT_HOTKEY,
                getattr(self, "_prev_kill", "") or None,
                getattr(self, "_prev_main_codes", None) or None,
                getattr(self, "_prev_kill_codes", None) or None,
            )
            return  # отмена
        if getattr(self, "_capture_target", "main") == "kill":
            self.kill_edit.setText(hk)
            self._kill_codes = codes
        else:
            self.hotkey_edit.setText(hk)
            self._main_codes = codes
        self.hotkey_mgr.start(self.hotkey_edit.text().strip(), self.kill_edit.text().strip(),
                              self._main_codes, self._kill_codes)
        self._persist()

    def _emit_quit(self):
        try:
            self.quit_signal.emit()
        except RuntimeError:
            pass

    def _on_kill_hotkey(self):
        self._log("Kill hotkey pressed — exiting")
        self._force_quit()

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
            action = "restarted" if restart else "started"
            self._log(f"Server {action} on port {port}")
        self._persist()
        self._refresh_status()
        if not silent:
            # keep GSI config in sync with the port (no popup — just log)
            self._sync_cfg_with_port(port)

    def _notify_restart_required(self, path: str):
        """Restart reminder that also reaches tray-started users.

        A modal box parented to a hidden window may never appear on Windows,
        so when the main window is hidden we show a parentless topmost dialog.
        """
        title = self.t("popup_title")
        text = self.t("popup_text").format(path=path)
        if self.isVisible():
            QMessageBox.information(self, title, text)
            return
        box = QMessageBox(
            QMessageBox.Icon.Information,
            title,
            text,
            QMessageBox.StandardButton.Ok,
            None,
        )
        box.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        box.setModal(True)
        box.show()
        box.raise_()
        box.activateWindow()
        box.exec()

    def _sync_cfg_with_port(self, port: int):
        """Rewrite GSI config if it differs (e.g. template or port changed).

        Popup only when it matters: the game reads the cfg once at startup,
        so a restart reminder is shown iff the game process is running
        right now. Otherwise a log line is enough.
        """
        try:
            cs2_root = find_cs2_root()
            if not cs2_root:
                return
            cfg_dir = get_cfg_dir(cs2_root)
            expected = build_cfg_content(port, GSI_PATH)
            if read_config_text(cfg_dir) != expected:
                target = install_config(cfg_dir, port, GSI_PATH)
                self._log(f"GSI config updated for port {port}: {target} (restart game if running)")
                try:
                    game_running = is_game_running()
                except Exception:  # noqa: BLE001
                    game_running = False
                if game_running:
                    # defer: showing a modal dialog synchronously here is
                    # unreliable (init may be unfinished / window hidden to
                    # tray). When the timer fires the UI is settled.
                    self._pending_restart_notice = target
                    self._log("Game is running — restart reminder scheduled")
                    QTimer.singleShot(1500, self._flush_restart_notice)
        except Exception:  # noqa: BLE001
            pass

    def _flush_restart_notice(self):
        path = getattr(self, "_pending_restart_notice", None)
        self._pending_restart_notice = None
        if path:
            self._notify_restart_required(path)

    def stop_server(self, silent: bool = False):
        if self.server_thread:
            self.server_thread.stop()
            self.server_thread.join(timeout=2.0)
            self.server_thread = None
            self.server_port = None
            if not silent:
                self._log("Server stopped")
        self._refresh_status()

    @property
    def server_running(self) -> bool:
        return self.server_thread is not None and self.server_thread.is_alive()

    def _get_cs2_root(self):
        return find_cs2_root(self.settings.get("cs2_path"))

    def _browse_cs2_path(self):
        chosen = QFileDialog.getExistingDirectory(self, self.t("choose_cs2_folder"), os.path.expanduser("~"))
        if chosen:
            self.edit_cs2_path.setText(chosen)
            self._persist()

    def _detect_cs2_path(self):
        res = find_cs2_root(None)
        if res:
            self.edit_cs2_path.setText(res)
            self._log(f"CS2 Auto-detected: {res}")
            self._persist()
        else:
            self._log("Failed to auto-detect CS2 path.")

    # --- GSI конфиг ---

    def _install_cfg(self):
        port = self.spin_port.value()
        cs2_root = self._get_cs2_root()
        if not cs2_root:
            QMessageBox.warning(self, "QSmartSwap", self.t("err_no_steam"))
            self._browse_cs2_path()
            cs2_root = self._get_cs2_root()
            if not cs2_root:
                return
        try:
            cfg_dir = get_cfg_dir(cs2_root)
            target = install_config(cfg_dir, port, GSI_PATH)
        except OSError as e:
            QMessageBox.critical(self, "QSmartSwap", str(e))
            return
        QMessageBox.information(self, self.t("popup_title"), self.t("popup_text").format(path=target))
        self._log(f"GSI config written: {target} (port {port})")

    def _first_run_check(self):
        """First launch: install GSI config only if missing/outdated + popup.

        If the file already matches the current port — do nothing, no popup.
        (The old code rewrote the file and popped up on EVERY launch because
        _persist() dropped the cfg_autoinstalled flag — fixed as well.)"""
        try:
            cs2_root = self._get_cs2_root()
        except Exception:  # noqa: BLE001
            cs2_root = None
        if not cs2_root:
            self._log("First run: CS2 not found — install GSI config via button later")
            return
        try:
            cfg_dir = get_cfg_dir(cs2_root)
            expected = build_cfg_content(self.spin_port.value(), GSI_PATH)
            current = read_config_text(cfg_dir) if is_config_installed(cfg_dir) else None
            for stale in remove_legacy_configs(cfg_dir):
                self._log(f"Removed stale config: {stale}")
            if current == expected:
                self.settings["cfg_autoinstalled"] = True
                self._persist()
                self._log("GSI config up to date — no action needed")
                return
            target = install_config(cfg_dir, self.spin_port.value(), GSI_PATH)
        except OSError as e:
            self._log(f"First run: failed to write GSI config: {e}")
            return
        self.settings["cfg_autoinstalled"] = True
        self._persist()
        self._notify_restart_required(target)
        self._log(f"GSI config written: {target} (port {self.spin_port.value()})")

    def _write_binds(self):
        """'Bind slots' button: write qsmartswap_binds.cfg + autoexec hook."""
        if getattr(self, "_writing_binds", False):
            return
        self._writing_binds = True
        try:
            slot_keys = {s: e.text().strip() for s, e in self.slot_edits.items()}
            cs2_root = self._get_cs2_root()
            if not cs2_root:
                QMessageBox.warning(self, "QSmartSwap", self.t("err_no_steam"))
                self._browse_cs2_path()
                cs2_root = self._get_cs2_root()
                if not cs2_root:
                    return
            try:
                cfg_dir = get_cfg_dir(cs2_root)
                target, warnings, autoexec = install_binds(cfg_dir, slot_keys, VALVE_MAP)
            except OSError as e:
                QMessageBox.critical(self, "QSmartSwap", str(e))
                return
            autoexec_text = autoexec if autoexec else "autoexec.cfg (exec line already present)"
            warn_text = ("\n" + "\n".join(f"! {w}" for w in warnings)) if warnings else ""
            QMessageBox.information(
                self,
                self.t("binds_title"),
                self.t("binds_text").format(path=target, autoexec=autoexec_text, warnings=warn_text),
            )
            self._log(f"Binds written: {target} | autoload: {autoexec_text}")
            for w in warnings:
                self._log(f"! {w}")
        finally:
            self._writing_binds = False

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
        mmap = snap.get("map_phase") or "-"
        act = snap.get("player_activity") or "-"
        if snap["connected"] and snap["active_slot"]:
            owned = ",".join(snap["owned"]) or "-"
            base = self.t("status_gsi_ok").format(active=snap["active_slot"], owned=owned)
            self.lbl_gsi_status.setText(f"{base} | round={phase} map={mmap} act={act}")
        else:
            self.lbl_gsi_status.setText(f"{self.t('status_gsi_wait')} (round={phase} map={mmap} act={act})")

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
        self._force_quit()

    def _force_quit(self):
        """Full cleanup + hard quit regardless of window visibility."""
        self._persist()
        try:
            self.hotkey_mgr.stop()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.stop_server(silent=True)
        except Exception:  # noqa: BLE001
            pass
        QApplication.quit()

    def closeEvent(self, event):
        self._pending_restart_notice = None
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
        QApplication.quit()


def main():
    # Per-Monitor DPI awareness V2
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)
    win = MainWindow()
    
    if win.settings.get("start_minimized", False):
        if QSystemTrayIcon.isSystemTrayAvailable():
            win._hide_to_tray()
        else:
            win.show()
    else:
        win.show()
    
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
