Set-Location $PSScriptRoot
py -3.14 -m PyInstaller --onefile --windowed --clean --noconfirm `
  --name FurnitureUpgrade `
  --add-data "fonts;fonts" `
  --hidden-import PySide6.QtCore `
  --hidden-import PySide6.QtGui `
  --hidden-import PySide6.QtWidgets `
  --exclude-module PySide6.QtWebEngineCore `
  --exclude-module PySide6.QtWebEngineWidgets `
  --exclude-module PySide6.QtWebEngineQuick `
  --exclude-module PySide6.QtWebChannel `
  --exclude-module PySide6.QtWebSockets `
  --exclude-module PySide6.QtQml `
  --exclude-module PySide6.QtQuick `
  --exclude-module PySide6.QtQuick3D `
  --exclude-module PySide6.QtQuickWidgets `
  --exclude-module PySide6.QtQuickControls2 `
  --exclude-module PySide6.Qt3DCore `
  --exclude-module PySide6.Qt3DRender `
  --exclude-module PySide6.Qt3DInput `
  --exclude-module PySide6.Qt3DLogic `
  --exclude-module PySide6.Qt3DAnimation `
  --exclude-module PySide6.Qt3DExtras `
  --exclude-module PySide6.QtCharts `
  --exclude-module PySide6.QtDataVisualization `
  --exclude-module PySide6.QtMultimedia `
  --exclude-module PySide6.QtMultimediaWidgets `
  --exclude-module PySide6.QtSpatialAudio `
  --exclude-module PySide6.QtTextToSpeech `
  --exclude-module PySide6.QtNetwork `
  --exclude-module PySide6.QtNetworkAuth `
  --exclude-module PySide6.QtSql `
  --exclude-module PySide6.QtTest `
  --exclude-module PySide6.QtBluetooth `
  --exclude-module PySide6.QtNfc `
  --exclude-module PySide6.QtPositioning `
  --exclude-module PySide6.QtSensors `
  --exclude-module PySide6.QtSerialPort `
  --exclude-module PySide6.QtDesigner `
  --exclude-module PySide6.QtHelp `
  --exclude-module PySide6.QtUiTools `
  --exclude-module PySide6.QtOpenGL `
  --exclude-module PySide6.QtOpenGLWidgets `
  --exclude-module PySide6.QtPdf `
  --exclude-module PySide6.QtPdfWidgets `
  --exclude-module PySide6.QtRemoteObjects `
  --exclude-module PySide6.QtScxml `
  --exclude-module PySide6.QtStateMachine `
  --exclude-module PySide6.QtSvg `
  --exclude-module PySide6.QtSvgWidgets `
  --exclude-module PySide6.QtXml `
  --exclude-module tkinter `
  --exclude-module unittest `
  --exclude-module pydoc `
  --exclude-module email `
  --exclude-module http `
  --exclude-module xmlrpc `
  --exclude-module pdb `
  --exclude-module matplotlib `
  --exclude-module numpy `
  --exclude-module scipy `
  --exclude-module pandas `
  --exclude-module PIL `
  --exclude-module setuptools `
  --exclude-module pip `
  --upx-exclude "vcruntime140.dll" `
  --upx-exclude "vcruntime140_1.dll" `
  --upx-exclude "msvcp140.dll" `
  --upx-exclude "python3.dll" `
  --upx-exclude "python3*.dll" `
  --upx-exclude "Qt6Core.dll" `
  FurnitureUpgrade.py

# --- stage the onefile into ..\FurnitureUpgrade-onefile\ ---
$target = Join-Path (Split-Path $PSScriptRoot -Parent) "FurnitureUpgrade-onefile"
if (Test-Path $target) { Remove-Item $target -Recurse -Force }
New-Item -ItemType Directory -Path $target | Out-Null

# Rename exe inside dist\ then move it into the target
if (Test-Path 'dist\FurnitureUpgrade-onefile.exe') { Remove-Item 'dist\FurnitureUpgrade-onefile.exe' -Force }
Move-Item 'dist\FurnitureUpgrade.exe' 'dist\FurnitureUpgrade-onefile.exe'
Move-Item 'dist\FurnitureUpgrade-onefile.exe' $target

# Copy runtime folders alongside the exe
foreach ($item in @("swfs","data","translations.json","description.json","README.md")) {
    $src = Join-Path $PSScriptRoot $item
    if (Test-Path $src) {
        Copy-Item -Path $src -Destination $target -Recurse -Force
        Write-Host "  copied $item -> $target"
    } else {
        Write-Warning "  missing $item (skipped)"
    }
}

# Clean up the transient dist\ folder
if (Test-Path 'dist') { Remove-Item 'dist' -Recurse -Force }

# Restore our hand-tuned spec (PyInstaller regenerates it from CLI flags)
Copy-Item 'FurnitureUpgrade.spec.txt' 'FurnitureUpgrade.spec' -Force

Write-Host "Onefile build done: $target"