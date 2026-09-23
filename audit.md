# Audit Notes for Nexus Mods Review

This file exists to make the source-to-binary review as quick as possible.

## What the executable is
A PyInstaller-bundled Python 3.14 + PySide6 desktop application. No DLL
injection, no process hooks, no native code beyond what PyInstaller and
PySide6 ship.

## Entry point
`FurnitureUpgrade.py` — the `main()` function at the bottom of the file.

## File I/O surface (complete list)
- Reads: user-selected *.sav file (opened via QFileDialog)
- Reads: translations.json, data/*, swfs/*, fonts/* (bundled with the exe
  or sitting next to it)
- Writes: the user-selected *.sav file (after making a backup copy)
- Writes: backups/*.backup_* (timestamped copies of the save file)
- Writes: FurnitureUpgrade-config.json (app settings, next to the exe)

## Network surface
None. No sockets, no HTTP, no DNS, no telemetry. Grep the source for
`socket`, `urllib`, `requests`, `http` — all absent.

## Registry surface
None. Grep the source for `winreg` — absent.

## Subprocess surface
None. Grep the source for `subprocess`, `os.system`, `os.popen` — all absent.

## Build reproduction
See BUILD.md. The same source produces a byte-identical exe (modulo
timestamps) on Windows 10+ with Python 3.14 and PyInstaller.

## SHA-256 of uploaded archives

- **FurnitureUpgrade-Raw.zip**:       `63AE6CA88894D8A752334430A88EC55576340283A44B974C67D2CCD403B45FA8`
- **FurnitureUpgrade-largefast.zip**: `99D2C8B0FF003F2D1085F7C48DC75588901F11D1E5FC9BB1C8CF3D04F8492EC9`
- **FurnitureUpgrade-smallslow.zip**: `1BE323B1C3D597717B1B2E6001030A663EEAEF7A9DF453E60FAECA4BFBEE0FBA`
