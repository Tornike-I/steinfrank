# Monster Lab (their 3D interface) + our brain (backend), started with one command.
# Port 8010 intentionally leaves the original Frankenstein backend free on 8000.
param([int]$Port = 8010)
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$py = "$root\backend\.venv\Scripts\python.exe"
$ui = (Resolve-Path "$root\..\monster-lab").Path
$originalData = (Resolve-Path "$root\..\..\backend\data").Path

if (-not (Test-Path $py)) {
    Write-Host "Creating Python venv for the brain..." -ForegroundColor Cyan
    python -m venv "$root\backend\.venv"
    & $py -m pip install -q -r "$root\backend\requirements.txt"
}
if (-not (Test-Path "$root\backend\.env")) {
    Copy-Item "$root\backend\.env.example" "$root\backend\.env"
    Write-Host "Created backend\.env - put ELEVENLABS_API_KEY (brain, voice) and optionally SOKOSUMI_API_KEY there, then run start.ps1 again." -ForegroundColor Yellow
    exit 0
}
if (-not (Test-Path "$ui\node_modules")) {
    Write-Host "Installing the Monster Lab interface..." -ForegroundColor Cyan
    Push-Location $ui; npm install --no-audit --no-fund; Pop-Location
}

Write-Host "Brain (API) on http://localhost:$Port - its own window" -ForegroundColor Green
$env:FRANK_IMPORT_DATA_DIR = $originalData
Start-Process powershell -WorkingDirectory "$root\backend" -ArgumentList "-NoExit", "-Command", "& '$py' -m uvicorn app.main:app --port $Port"

Write-Host "Monster Lab on http://localhost:5173" -ForegroundColor Green
$env:FRANK_URL = "http://localhost:$Port"
Set-Location $ui
npm run dev
