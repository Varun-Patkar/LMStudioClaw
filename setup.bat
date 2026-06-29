@echo off
REM One-step setup for LMStudioClaw (double-click friendly).
REM Delegates to setup.ps1, bypassing PowerShell's execution policy so a
REM non-technical user doesn't have to change any system settings.
title LMStudioClaw setup
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
