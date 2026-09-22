# QSmartSwap

A smart weapon-switch helper for Counter-Strike 2. Press **one** hotkey and the app
picks the right weapon for you, based on what you are currently holding.

It works through Valve's official **Game State Integration (GSI)**: the game itself
POSTs its state (weapons, round phase) to a local HTTP endpoint. **No game memory
is read or written.** Switching is done by simulating ordinary key presses —
the same as any keyboard macro (AutoHotkey-style).

![icon](icon.png)

## Features

- **Rule-based switching** — rows of *"if slot X is active → take slot Y"*,
  add as many as you like (default: 3).
- **Granular slots** — primary, secondary, knife, Zeus, C4, each grenade type
  (HE / flash / smoke / decoy / molotov), generic grenade cycle, med-shot.
  Grenades and Zeus are tracked separately, not lumped together.
- **Any-complexity hotkey** — single keys and combos (`q`, `ctrl+q`,
  `ctrl+shift+x`, mouse buttons). Matching is done by **scan code**, so binds
  work in any keyboard layout (`q` == `й`), and **left/right modifiers and
  numpad keys are distinct binds** (`right ctrl` ≠ `left ctrl`,
  numpad-del ≠ `delete`).
- **Missing-weapon fallback** — no primary in hands? Binds targeting primary
  automatically go to the pistol (toggleable). No pistol? Redirect to primary /
  knife / nothing — your choice.
- **Kill hotkey** (`ctrl+end` by default) — quits the app instantly from anywhere,
  no focus checks.
- **Safety gates** — the hotkey is ignored when CS2 is not the focused window
  and until a round is actually live (`warmup` / `freezetime` / `live` / `over`).
- **Per-slot output keys** + one-click writer of `qsmartswap_binds.cfg`
  auto-loaded via `autoexec.cfg` — no manual `exec` needed.
- **GSI config installer** — finds CS2 through Steam libraries, writes the
  `.cfg` for the selected port, cleans up stale configs from older versions.
- **Server controls** — port selector (default `7777`, auto-start on launch)
  with Start / Restart / Stop.
- **System tray** — minimize to tray, tray icon menu.
- **Bilingual UI** (RU/EN). Console output is always English.
- Settings persist in `config.json` next to the app/exe.

## Requirements

- Windows 10/11 64-bit
- Counter-Strike 2 installed via Steam
- To run from source: Python 3.10+ with `pip install -r requirements.txt`
  (PyQt6, keyboard, Pillow)

## Quick start (prebuilt exe)

1. Run `QSmartSwap.exe`.
2. Click **Install GSI config**, then **restart CS2** if it was running.
3. Click **Write slot binds (.cfg)** so the game knows which keys select
   `slot6`–`slot12` (the game loads them automatically on start).
4. Set your switch hotkey with **Change...**, add rules if needed, join a match.
5. Press the hotkey in game — the app swaps according to the first matching rule.

## Quick start (from source)

```bat
pip install -r requirements.txt
python main.py
```

## How switching works

1. CS2 sends GSI packets to `http://127.0.0.1:7777/qsmartswap` (~10 Hz).
2. The app tracks the active weapon and owned slots, plus the round phase.
3. On your hotkey: find the first rule whose *"if active"* equals the real
   active slot → apply missing-weapon fallback to the target if needed →
   press the target slot's key.
4. An optimistic prediction bridges the ~100 ms GSI lag, so fast double
   presses alternate correctly instead of re-reading a stale weapon.

### Default rules

| If active | Take  |
|-----------|-------|
| primary   | knife |
| knife     | primary |
| secondary | primary |

### Slot → Valve command map

| Slot        | Valve command | Default key |
|-------------|---------------|-------------|
| primary     | `slot1`  | `1` |
| secondary   | `slot2`  | `2` |
| knife       | `slot3`  | `3` |
| zeus        | `slot11` | *(none — set it yourself)* |
| grenades (cycle) | `slot4` | `4` |
| hegrenade   | `slot6`  | `6` |
| flashbang   | `slot7`  | `7` |
| smokegrenade| `slot8`  | `8` |
| decoy       | `slot9`  | `9` |
| molotov     | `slot10` | `0` |
| c4          | `slot5`  | `5` |
| med-shot    | `slot12` | `h` |

> `slot6`–`slot12` are unbound in a fresh CS2 install — either bind the same
> keys in game settings or use the **Write slot binds** button (recommended).

## Files the app touches

| File | Where | Purpose |
|------|-------|---------|
| `gamestate_integration_qsmartswap.cfg` | `.../csgo/cfg/` | Tells CS2 where to POST GSI data |
| `qsmartswap_binds.cfg` | `.../csgo/cfg/` | `bind "<key>" "slotN"` lines generated from the slot table |
| `autoexec.cfg` | `.../csgo/cfg/` | Appended with `exec qsmartswap_binds` once (never duplicated) |
| `config.json` | next to the app | Your settings (never committed to git) |

Changing the port rewrites the GSI config silently (log only) — restart the
game afterwards.

## Building the exe yourself

```bat
pip install -r requirements.txt pyinstaller
pyinstaller --onefile --windowed --name QSmartSwap --icon icon.ico --add-data "icon.png;." main.py
```

The binary lands in `dist/QSmartSwap.exe`. Keep `icon.png` next to it
(it is also bundled as a fallback).

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Hotkey does nothing, console says `waiting for round start` | Click **Install GSI config** and **restart CS2** (old config lacks `round` data) |
| Hotkey does nothing outside matches | By design: needs game focus + a live round |
| `[hotkey] active=c4: no rule` | Add a rule with `c4` on the left side (e.g. `c4 → knife`) |
| `Left ctrl` bind also fires on `Right ctrl` | Re-capture the bind — only freshly captured combos store exact scan codes |
| Bind worked, then stopped after switching layout | Re-capture it; capture is layout-independent, typed strings are not |
| Port busy / server won't start | Pick another port, press Restart, reinstall the GSI config |

## Fair-play note

GSI is Valve's official, documented integration channel. The app never reads
or writes game memory; it only presses keys. That said, third-party
anti-cheats (FaceIt, ESEA, 5EPlay, etc.) may dislike global keyboard hooks —
use at your own discretion, especially the kill hotkey keeps you in control.

## Project layout

| File | Role |
|------|------|
| `main.py` | PyQt6 UI: hotkeys, port/server, rules, slot keys, tray, console |
| `hotkey_logic.py` | Combo matcher (scan codes), rules engine, fallback, prediction |
| `gsi_server.py` | Local GSI HTTP server + weapon/round state |
| `gsi_config.py` | GSI/bind/autoexec file builders |
| `steam_locate.py` | Steam/CS2 install discovery via registry + `libraryfolders.vdf` |
| `gsi_watch.py` | Standalone GSI packet inspector (debug tool) |
| `i18n.py` | RU/EN strings |
