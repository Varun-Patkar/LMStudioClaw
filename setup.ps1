<#
.SYNOPSIS
    One-step setup for LMStudioClaw — for people who just want it to work.

.DESCRIPTION
    Run this once after downloading the project. It:
      1. Checks that Python is installed.
      2. Creates an isolated virtual environment in .\venv (if missing).
      3. Installs LMStudioClaw and its dependencies into that environment.
      4. Builds the web interface if Node.js/npm is available (optional — a prebuilt
         UI ships in the repo, so this only refreshes it).
      5. Prints exactly how to start the app.

    It is safe to run again at any time; existing pieces are reused, not rebuilt.

.NOTES
    Windows only. No code changes are made — this only prepares your machine.
#>

$ErrorActionPreference = "Stop"
# Always operate from the folder this script lives in, so double-clicking works.
Set-Location -Path $PSScriptRoot

function Write-Step  ($m) { Write-Host "`n==> $m" -ForegroundColor Cyan }
function Write-Ok    ($m) { Write-Host "    $m" -ForegroundColor Green }
function Write-Warn2 ($m) { Write-Host "    $m" -ForegroundColor Yellow }

Write-Host "LMStudioClaw setup" -ForegroundColor White
Write-Host "==================" -ForegroundColor White

# --- 1. Python --------------------------------------------------------------
Write-Step "Checking for Python"
$python = $null
foreach ($candidate in @("python", "py")) {
    if (Get-Command $candidate -ErrorAction SilentlyContinue) { $python = $candidate; break }
}
if (-not $python) {
    Write-Warn2 "Python was not found on your PATH."
    Write-Warn2 "Install Python 3.11+ from https://www.python.org/downloads/ (tick 'Add to PATH'), then re-run this script."
    Read-Host "`nPress Enter to close"
    exit 1
}
Write-Ok "Found '$python'."

# --- 2. Virtual environment -------------------------------------------------
Write-Step "Preparing the virtual environment (.\venv)"
if (-not (Test-Path ".\venv\Scripts\python.exe")) {
    & $python -m venv venv
    Write-Ok "Created a fresh environment."
} else {
    Write-Ok "Existing environment reused."
}
$venvPython = ".\venv\Scripts\python.exe"

# --- 3. Install the app -----------------------------------------------------
Write-Step "Installing LMStudioClaw and dependencies (this can take a few minutes)"
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -e ".[dev]"
Write-Ok "Installed."

# --- 4. Build the web interface (optional) ----------------------------------
Write-Step "Building the web interface"
if (Get-Command npm -ErrorAction SilentlyContinue) {
    Push-Location frontend
    if (-not (Test-Path ".\node_modules")) {
        Write-Ok "Installing UI dependencies..."
        npm install
    }
    npm run build
    Pop-Location
    Write-Ok "Web interface built."
} else {
    Write-Warn2 "Node.js/npm not found — using the prebuilt interface that ships with the project."
    Write-Warn2 "(Only needed if you want to change the UI. Install Node.js from https://nodejs.org/ to enable this.)"
}

# --- 5. Done ----------------------------------------------------------------
Write-Step "All set!"
Write-Host ""
Write-Host "  To start LMStudioClaw, double-click " -NoNewline; Write-Host "lmstudio.bat" -ForegroundColor White
Write-Host "  (or run 'venv\Scripts\lmstudio' from this folder)."
Write-Host ""
Write-Host "  On first launch a setup screen opens in your browser. If your LM Studio"
Write-Host "  server needs an API key, paste it there and click 'Save & continue'."
Write-Host ""
Read-Host "Press Enter to close"
