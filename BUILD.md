# Building Furniture Upgrade from Source

## Prerequisites

- Windows 10 or later
- Python 3.14 (https://www.python.org/downloads/) — check "Add Python to PATH"
- PowerShell 5.1 or later (built into Windows)

## One-time setup

    py -3.14 -m pip install -r requirements.txt
    py -3.14 -m pip install pyinstaller

## Build the onedir package (fast launch)

    .\build-onedir.ps1

Output: ..\FurnitureUpgrade-onedir\ containing the exe, _internal\, and runtime data.

## Build the onefile exe (portable, slower launch)

    .\build-onefile.ps1

Output: ..\FurnitureUpgrade-onefile\FurnitureUpgrade-onefile.exe plus runtime data.

## Run without building (dev mode)

    py -3.14 FurnitureUpgrade.py

## What the executable does

- Opens a user-selected SQLite save file (*.sav) via a file dialog
- Reads and writes rows in the furniture table
- Never connects to the network
- Never touches the Windows registry
- Never installs anything
- Writes only to: the user-selected save file, the app's own folder (backups + config)

## PyInstaller false positive

This mod ships as a PyInstaller-bundled Python application. PyInstaller packs
a Python interpreter and all dependencies into the exe, which some AV
heuristics flag generically. The source above is the exact code PyInstaller
bundles - building it yourself produces the same binary.
