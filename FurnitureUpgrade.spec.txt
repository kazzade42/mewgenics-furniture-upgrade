# -*- mode: python ; coding: utf-8 -*-
"""
FurnitureUpgrade.spec — debloated onedir build for Mewgenics Furniture Upgrade.

Excludes every PySide6 module the app doesn't use (WebEngine, QML, Quick,
3D, Charts, Multimedia, Network, SQL, etc.) and filters the collected
binaries/datas to drop their DLLs too.

UPX compression is ON by default. UPX is safe on Windows for Qt DLLs;
if you get AV false positives, set upx=False below and rebuild.

Build:
    pyinstaller FurnitureUpgrade.spec

Output:
    dist/FurnitureUpgrade/FurnitureUpgrade.exe
"""

import os
from PyInstaller.utils.hooks import collect_data_files


# ---------------------------------------------------------------------------
# Modules to exclude. Everything here is a PySide6 submodule we don't import.
# If the app crashes on launch with "module not found", add the missing
# module back by removing its line (and its Qt6*.dll from the drop list
# below).
# ---------------------------------------------------------------------------
EXCLUDES = [
    # Big offenders — the whole point of this spec
    'PySide6.QtWebEngineCore',
    'PySide6.QtWebEngineWidgets',
    'PySide6.QtWebEngineQuick',
    'PySide6.QtWebChannel',
    'PySide6.QtWebSockets',
    'PySide6.QtQml',
    'PySide6.QtQuick',
    'PySide6.QtQuick3D',
    'PySide6.QtQuickWidgets',
    'PySide6.QtQuickControls2',
    'PySide6.Qt3DCore',
    'PySide6.Qt3DRender',
    'PySide6.Qt3DInput',
    'PySide6.Qt3DLogic',
    'PySide6.Qt3DAnimation',
    'PySide6.Qt3DExtras',
    'PySide6.QtCharts',
    'PySide6.QtDataVisualization',
    'PySide6.QtMultimedia',
    'PySide6.QtMultimediaWidgets',
    'PySide6.QtSpatialAudio',
    'PySide6.QtTextToSpeech',
    'PySide6.QtNetwork',
    'PySide6.QtNetworkAuth',
    'PySide6.QtSql',
    'PySide6.QtTest',
    'PySide6.QtBluetooth',
    'PySide6.QtNfc',
    'PySide6.QtPositioning',
    'PySide6.QtSensors',
    'PySide6.QtSerialPort',
    'PySide6.QtDesigner',
    'PySide6.QtHelp',
    'PySide6.QtUiTools',
    'PySide6.QtOpenGL',
    'PySide6.QtOpenGLWidgets',
    'PySide6.QtPdf',
    'PySide6.QtPdfWidgets',
    'PySide6.QtRemoteObjects',
    'PySide6.QtScxml',
    'PySide6.QtStateMachine',
    'PySide6.QtSvg',
    'PySide6.QtSvgWidgets',
    'PySide6.QtXml',
    # Python stdlib extras we don't need
    'tkinter',
    'unittest',
    'pydoc',
    'doctest',
    'email',
    'http',
    'xmlrpc',
    'pdb',
    'difflib',
    'asyncio',
    'concurrent',
    'multiprocessing',
    # Third-party that might be dragged in transitively
    'matplotlib',
    'numpy',
    'scipy',
    'pandas',
    'PIL',
    'setuptools',
    'pip',
    'pkg_resources',
]

# Qt DLLs / plugins to strip from the collected binaries after Analysis.
# Match by prefix on the destination path inside the bundle.
DROP_BINARY_PREFIXES = (
    'PySide6/Qt6WebEngine',
    'PySide6/QtWebEngine',
    'PySide6/Qt6Qml',
    'PySide6/Qt6Quick',
    'PySide6/Qt6Charts',
    'PySide6/Qt6DataVisualization',
    'PySide6/Qt6Multimedia',
    'PySide6/Qt6SpatialAudio',
    'PySide6/Qt6TextToSpeech',
    'PySide6/Qt6Network',
    'PySide6/Qt6Sql',
    'PySide6/Qt6Test',
    'PySide6/Qt6Bluetooth',
    'PySide6/Qt6Nfc',
    'PySide6/Qt6Positioning',
    'PySide6/Qt6Sensors',
    'PySide6/Qt6SerialPort',
    'PySide6/Qt6WebSockets',
    'PySide6/Qt6WebChannel',
    'PySide6/Qt6Designer',
    'PySide6/Qt6Help',
    'PySide6/Qt6UiTools',
    'PySide6/Qt6OpenGL',
    'PySide6/Qt6Pdf',
    'PySide6/Qt6RemoteObjects',
    'PySide6/Qt6Scxml',
    'PySide6/Qt6StateMachine',
    'PySide6/Qt6Svg',
    'PySide6/Qt6Xml',
)

# Datas to strip. Same idea as binaries.
DROP_DATA_PREFIXES = (
    'PySide6/Qt6WebEngine',
    'PySide6/Qt6Qml',
    'PySide6/Qt6Quick',
    'PySide6/Qt6Charts',
    'PySide6/Qt6DataVisualization',
    'PySide6/Qt6Multimedia',
    'PySide6/Qt6Pdf',
    'PySide6/Qt6Svg',
)


def _filter_by_prefix(items, drop_prefixes):
    """items is a list of tuples whose first element is a path string."""
    keep = []
    for item in items:
        dest = item[0] if isinstance(item, tuple) else item
        if any(str(dest).startswith(p) for p in drop_prefixes):
            continue
        keep.append(item)
    return keep


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------
a = Analysis(
    ['FurnitureUpgrade.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('fonts', 'fonts'),      # bundle the fonts folder
    ],
    hiddenimports=[
        # Only what we actually use.
        'PySide6.QtCore',
        'PySide6.QtGui',
        'PySide6.QtWidgets',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
    optimize=0,
)

# Apply the binary / data drop lists
a.binaries = _filter_by_prefix(a.binaries, DROP_BINARY_PREFIXES)
a.datas    = _filter_by_prefix(a.datas,    DROP_DATA_PREFIXES)

# ---------------------------------------------------------------------------
# Bundle
# ---------------------------------------------------------------------------
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='FurnitureUpgrade',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,                       # <- UPX compression ON
    upx_exclude=[
        # DLLs that are known to misbehave when UPX-compressed.
        'vcruntime140.dll',
        'vcruntime140_1.dll',
        'msvcp140.dll',
        'python3.dll',
        'python3*.dll',
        'Qt6Core.dll',              # Core is loaded very early; keep it raw
    ],
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,                      # drop a .ico path here later if you want
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[
        'vcruntime140.dll',
        'vcruntime140_1.dll',
        'msvcp140.dll',
        'python3.dll',
        'python3*.dll',
        'Qt6Core.dll',
    ],
    name='FurnitureUpgrade',
)