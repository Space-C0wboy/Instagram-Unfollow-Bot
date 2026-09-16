$ErrorActionPreference = "Stop"
$env:PLAYWRIGHT_BROWSERS_PATH = "0"   # install Chromium inside the playwright package so collect_all picks it up
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\python -m playwright install chromium
.\.venv\Scripts\python scripts/make_icon.py
if ($LASTEXITCODE -ne 0) { throw "icon generation failed" }
.\.venv\Scripts\python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw "pytest failed" }
# PyInstaller's Chromium tree exceeds Windows' MAX_PATH from a deep checkout, so build
# in a short folder under the user profile and copy only the zip back into dist\.
$work = Join-Path $env:USERPROFILE "igcleanup-build"
if (Test-Path $work) {
    $empty = Join-Path $env:TEMP ("igcleanup-empty-" + [guid]::NewGuid())
    New-Item -ItemType Directory -Path $empty | Out-Null
    robocopy $empty $work /MIR /NFL /NDL /NJH /NJS /NP | Out-Null
    Remove-Item -Recurse -Force $work, $empty
}
.\.venv\Scripts\python -m PyInstaller igcleanup.spec --noconfirm --distpath "$work/dist" --workpath "$work/build"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
New-Item -ItemType Directory -Force "$work\dist\Instagram Cleanup/backups" | Out-Null
New-Item -ItemType Directory -Force dist | Out-Null
Compress-Archive -Path "$work\dist\Instagram Cleanup" -DestinationPath "dist\Instagram-Cleanup.zip" -Force
Write-Host "Built dist\Instagram-Cleanup.zip (unpacked copy in $work\dist)"
