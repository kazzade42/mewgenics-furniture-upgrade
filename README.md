# Furniture Upgrade

Merges duplicate furniture in your Mewgenics save into tiered `_plus1`
through `_plus5` variants with boosted stats (2 copies → plus1, 4 → plus2,
8 → plus3, and so on). Run the included tool before launching the game
whenever you've collected new duplicates you want to merge.

## Installation

1. Drop this whole folder into your Mewtator `mods` folder.
2. Enable the mod in Mewtator.
3. Run the FurnitureUpgrade tool (see below) before launching the game
   each time you want to merge newly-collected duplicate furniture.
4. Launch Mewgenics through Mewtator as usual.

## Two ways to run the tool

**Raw Python** (this package): run `python FurnitureUpgrade.py` after
installing the one dependency:

```
pip install -r requirements.txt
```

Works on Windows, macOS, and Linux (including Steam Deck) as-is.

**Pre-built executable** (Faster or Slower package, downloaded
separately): just double-click the exe, no Python install needed.

- **Faster** — a folder with the exe plus its files alongside it.
  Bigger download, quicker to start each time.
- **Slower** — a single exe file. Smaller download, but unpacks
  itself to a temp folder on every launch, so it takes a bit longer
  to open. Pick this one if you want a single portable file.

## Building your own executable

You don't need to do this to use the mod — the raw Python script works
fine on its own. This is only if you want your own `.exe` instead of
running `FurnitureUpgrade.py` directly.

**Prerequisites:**

1. Install **Python 3.14** specifically (from python.org, so the `py`
   launcher registers it). A different 3.x version will not work —
   this project depends on a Python 3.14 install that has full
   PySide6 support.
2. Install build dependencies:

   ```
   py -3.14 -m pip install "setuptools<81" pyinstaller PySide6-Essentials
   ```

   The `setuptools<81` pin matters: PyInstaller's dependency chain
   still imports `pkg_resources`, which setuptools 81+ removed. You
   may see a deprecation warning during the build — that's expected
   and harmless as long as the pin is in place.
3. In PowerShell, allow the build scripts to run for this session:

   ```
   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
   ```

**To build:**

- **Faster** (folder + exe): run `.\build-onedir.ps1`. Output goes to
  `dist\FurnitureUpgrade-onedir\`.
- **Slower** (single exe): run `.\build-onefile.ps1`. Output goes to
  `dist\FurnitureUpgrade-onefile.exe`.

Both scripts `cd` into their own folder automatically, so you can run
them from anywhere as long as `FurnitureUpgrade.py`, `FurnitureUpgrade.spec`,
and `FurnitureUpgrade.spec.txt` are sitting alongside them.

> **One quirk to know about:** the onefile build passes `FurnitureUpgrade.py`
> straight to PyInstaller, which makes PyInstaller regenerate `FurnitureUpgrade.spec`
> from scratch — overwriting the tuned one this project actually uses.
> `build-onefile.ps1` restores the real spec from `FurnitureUpgrade.spec.txt`
> automatically at the end of the run, so don't delete `.spec.txt` even
> though it looks like a redundant backup — it's the one the onedir
> build actually depends on.

After building either version, copy `data/`, `translations.json`,
`description.json`, and `config.json` alongside the output — those
aren't bundled into the exe and need to travel with it. `fonts/` is
bundled automatically, no need to copy it separately.

## Configuration

Settings (theme, font size, UI language) live in
`FurnitureUpgrade-config.json`, created automatically next to the
exe or script on first run. Portable — delete it to reset to
defaults, or move it with the tool to another machine.

## Backups & undo

Every merge automatically backs up your save into a `backups/`
folder created next to the exe/script. Use the **Undo** button to
restore your most recent backup. A **Prune** option keeps only the
N most recent backups so this folder doesn't grow forever.

## Known quirks

- Windows Defender or another antivirus may flag the exe on first
  run. This is a known false-positive pattern with UPX-compressed
  PyInstaller executables (a heuristic flag, not an actual threat).
  If it bothers you, the raw Python version sidesteps this entirely.
- Close Mewgenics before running the tool, and don't run the game
  and the tool at the same time.
- The game loads certain aspects of the furniture at launch. Quitting to menu may seem to technically work, but this caused a couple issues during testing.
- Row header doesn't fill in all the way without manual extension, will fix at some point.
- preserve_excess button mislabeled in the code somewhere, no big deal, simply refers to what to do with extra copies of items that don't merge into a proper tier and still works correctly.

## Requirements

- **Mewtator**, to load the game-data files in this package.
- **Nothing else** for the exe versions. The raw Python version needs
  Python 3.10+ and the one package in `requirements.txt`.
- **Optional:** [Shop Filter](https://www.nexusmods.com/mewgenics/mods/XXX)
  (a separate download) keeps upgraded furniture from cluttering Jack's
  shop late-game. Not required — Furniture Upgrade works fine without
  it — but recommended once you're deep into merging.

## Credits

- Translations: machine-translated by DeepSeek (2026-09-22).
  Native speakers are welcome to suggest polish for any language.
- Fonts extracted from Mewgenics' own game files.
- Built with DeepSeek-V4.1-Flash, with help from Claude Code for
  pieces DeepSeek's tooling couldn't reach on its own.
