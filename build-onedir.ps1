Set-Location $PSScriptRoot
py -3.14 -m PyInstaller --clean --noconfirm FurnitureUpgrade.spec

# --- stage the onedir package into ..\FurnitureUpgrade-onedir\ ---
$target = Join-Path (Split-Path $PSScriptRoot -Parent) "FurnitureUpgrade-onedir"
if (Test-Path $target) { Remove-Item $target -Recurse -Force }
Move-Item 'dist\FurnitureUpgrade' $target

# Copy runtime folders INTO the onedir package
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

Write-Host "Onedir build done: $target"