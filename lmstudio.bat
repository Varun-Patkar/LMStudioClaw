@echo off
title LMStudioClaw
cd /d "%~dp0"

REM --- Stop any running instance so we relaunch cleanly (frees the web port) ---
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe' OR Name='python.exe'\" | Where-Object { $_.CommandLine -like '*lmstudioclaw.cli*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" >nul 2>&1

@REM REM --- Build the React frontend so the served UI is always current ---
@REM where npm >nul 2>&1
@REM if %ERRORLEVEL%==0 (
@REM   pushd frontend
@REM   if not exist node_modules (
@REM     echo Installing frontend dependencies...
@REM     call npm install
@REM   )
@REM   echo Building UI...
@REM   call npm run build
@REM   popd
@REM ) else (
@REM   echo npm not found on PATH - serving the existing build in lmstudioclaw\web\static.
@REM )

REM --- Launch the controller: it serves the API, WebSockets, and the built React UI ---
start "" "%~dp0venv\Scripts\pythonw.exe" -m lmstudioclaw.cli
