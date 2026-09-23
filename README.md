<div align="center">
  <img src="icon.ico" width="128" height="128" alt="QSmartSwap Logo">
  
  # QSmartSwap

  **A smart, rule-based weapon-switch assistant for Counter-Strike 2.**
  
  [![Platform: Windows](https://img.shields.io/badge/Platform-Windows%2010%20%7C%2011-blue?logo=windows)](#requirements)
  [![Python](https://img.shields.io/badge/Python-3.10+-yellow?logo=python)](#requirements)
  [![License](https://img.shields.io/badge/License-MIT-green)](#fair-play-note)
</div>

<br/>

**QSmartSwap** allows you to press **one** hotkey and automatically equip the correct weapon based on what you're currently holding. It uses Valve's official **Game State Integration (GSI)** to accurately determine your active weapon and round state.

> 🛡️ **Safe & Secure:** No game memory is read or written. The app only receives data via a local HTTP server and sends standard keyboard events using a native Windows hook — exactly like any ordinary keyboard macro (e.g., AutoHotkey or Razer Synapse).

---

## ✨ Features

- 🎯 **Rule-Based Switching:** Configure logical rows like *"if primary is active → take knife"* or *"if knife is active → take primary"*. Add as many rules as you want.
- 🔫 **Granular Slot Tracking:** Tracks primary, secondary, knife, Zeus, C4, med-shots, and **each individual grenade type** (HE, flash, smoke, decoy, molotov) separately.
- ⌨️ **Native Scan-Code Hotkeys:** Binds are tracked at the lowest hardware level using a custom Windows `WH_KEYBOARD_LL` hook.
  - Independent of keyboard layouts (`Q` == `Й`).
  - Distinguishes between Numpad and Navigation keys (e.g., `Num 7` ≠ `Home`).
  - Distinguishes between Left and Right modifiers (`Left Ctrl` ≠ `Right Ctrl`).
- 🔄 **Smart Missing-Weapon Fallbacks:** No primary in your hands? The app automatically falls back to your pistol. No pistol? It redirects to your knife. 
- ⏱️ **Zero-Lag Prediction:** Optimistic state prediction bridges the ~100ms GSI delay, allowing lightning-fast double-presses (Q-Q) to work flawlessly.
- 🛡️ **Safety Gates:** The hotkey strictly ignores inputs when CS2 is minimized, not in focus, or outside of a live round (ignores warmup/freezetime).
- 🖥️ **High-DPI UI:** Built with PyQt6 and a native `PerMonitorV2` Windows manifest. Crisp, responsive, and beautiful on high-resolution displays.
- ⚙️ **Automated Setup:** Automatically locates CS2, installs GSI configs, and writes `qsmartswap_binds.cfg` directly to your `autoexec.cfg`.

---

## 🚀 Quick Start (Prebuilt)

1. Download and run `QSmartSwap.exe` from the latest release.
2. Click **Install GSI config** (and restart CS2 if it was running).
3. Click **Write slot binds (.cfg)**. This ensures CS2 binds keys to hidden slots (`slot6`–`slot12`).
4. Set your **Switch Hotkey** via the `Capture` button.
5. Join a match and press the hotkey — QSmartSwap will seamlessly cycle weapons based on your rules!

---

## 🛠️ Quick Start (From Source)

```bat
git clone https://github.com/yourusername/QSmartSwap.git
cd QSmartSwap
pip install -r requirements.txt
python main.py
```

### 📦 Building the Executable

To build the optimized `onedir` binary with the embedded High-DPI manifest and icons:

```bat
pip install -r requirements.txt pyinstaller
pyinstaller -y --clean QSmartSwap.spec
```
The compiled application will be generated in `dist/QSmartSwap/`.

---

## 🕹️ How It Works

1. CS2 continuously sends GSI JSON packets to `http://127.0.0.1:7777/qsmartswap` at ~10 Hz.
2. The app tracks the **active weapon**, **owned inventory slots**, and the **round phase**.
3. Upon pressing your hotkey, the app:
   - Finds the first rule matching your currently active slot.
   - Applies fallbacks (e.g. if the target is `primary` but you don't own one).
   - Simulates a raw `SendInput` keystroke mapped to the target weapon.

### 📋 Default Logic Rules

| If holding... | Switch to... |
|---------------|--------------|
| `primary`     | `knife`      |
| `knife`       | `primary`    |
| `secondary`   | `primary`    |

### ⌨️ Slot → Valve Command Map

| Slot | Valve Command | Default Key |
|------|---------------|-------------|
| `primary` | `slot1` | `1` |
| `secondary` | `slot2` | `2` |
| `knife` | `slot3` | `3` |
| `zeus` | `slot11` | *(none)* |
| `c4` | `slot5` | `5` |
| `hegrenade` | `slot6` | `6` |
| `flashbang` | `slot7` | `7` |
| `smokegrenade` | `slot8` | `8` |
| `decoy` | `slot9` | `9` |
| `molotov` | `slot10` | `0` |
| `med-shot` | `slot12` | `h` |

> *Note: Slots 6–12 are unbound in a fresh CS2 installation. Use the **Write slot binds** button in the app to automate binding these slots in-game.*

---

## 🗂️ Managed Files

| File | Location | Purpose |
|------|----------|---------|
| `gamestate_integration_qsmartswap.cfg` | `.../csgo/cfg/` | Configures CS2 to POST GSI data to the app. |
| `qsmartswap_binds.cfg` | `.../csgo/cfg/` | Generates the `bind "<key>" "slotN"` commands. |
| `autoexec.cfg` | `.../csgo/cfg/` | Appends `exec qsmartswap_binds` so keys auto-load on start. |
| `config.json` | Next to `QSmartSwap.exe` | Saves your app UI settings (never committed to git). |

---

## 🩺 Troubleshooting

| Symptom | Solution |
|---------|----------|
| **Hotkey does nothing, console says `waiting for round start`** | Click **Install GSI config** and restart CS2. Older configs lack round data. |
| **Hotkey does nothing outside matches** | By design. GSI prevents switching when not in a live round. |
| **`[hotkey] active=c4: no rule`** | Add a custom rule with `c4` on the left side (e.g. `c4 → knife`). |
| **Server won't start / Port busy** | Select a new port in the UI, click **Restart**, and reinstall the GSI config. |

---

## ⚖️ Fair-Play Note

GSI is Valve's official, documented integration channel. QSmartSwap **never** reads or writes game memory; it solely relies on legitimate GSI data and standard OS keyboard simulation. However, aggressive third-party anti-cheats (FaceIt, ESEA) may block global keyboard hooks. Use at your own discretion on third-party matchmaking services.

---
<div align="center">
  <i>Built with PyQt6 • Powered by CS2 Game State Integration</i>
</div>
