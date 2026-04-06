@echo off
:: Work Package Sorter — Installer
:: Double-click this file to install the app automatically.

echo.
echo  Work Package Sorter - Installer
echo  This will set up everything automatically.
echo  Please do not close this window.
echo.

:: Allow PowerShell scripts to run for this session
powershell -Command "Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned -Force" >nul 2>&1

:: Run the installer script
powershell -ExecutionPolicy Bypass -File "%~dp0installer.ps1"

if errorlevel 1 (
    echo.
    echo Installation encountered an error. Please contact your administrator.
    pause
)
