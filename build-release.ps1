# build-release.ps1 - bikin FADB-{version}.zip dari main tree
# Isi: semua file KECUALI readme.md, git*, theme, license, script ini sendiri.
# Pakai: .\build-release.ps1 [-Version 6.0.0]
param([string]$Version = "")

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $Version) {
    $m = Select-String -Pattern 'APP_VERSION = "([^"]+)"' -Path (Join-Path $root "app.py")
    $Version = $m.Matches[0].Groups[1].Value
}
$zip = Join-Path $root "FADB-$Version.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }

$excludeDirs = @("Theme", ".git", "__pycache__", "logs")
$excludeFiles = @("README.md", "LICENSE", "build-release.ps1")
$items = Get-ChildItem $root -Force | Where-Object {
    -not ($_.PSIsContainer -and ($excludeDirs -contains $_.Name)) -and
    -not ((-not $_.PSIsContainer) -and ($excludeFiles -contains $_.Name -or $_.Name -like ".git*" -or $_.Name -like "*.pyc" -or $_.Name -like "FADB-*.zip"))
}
Compress-Archive -Path ($items.FullName) -DestinationPath $zip
Write-Output "OK: $zip"
Write-Output "--- isi:"
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::OpenRead($zip).Entries.FullName | Out-String
