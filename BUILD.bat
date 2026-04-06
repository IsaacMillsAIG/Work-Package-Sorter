@echo off
title Work Package Sorter — Setup
echo.
echo ============================================================
echo   Work Package Sorter — First-Time Setup
echo ============================================================
echo.
echo This will install Node.js dependencies and build the app.
echo Make sure Node.js is installed (nodejs.org) before continuing.
echo.
pause

echo.
echo [1/3] Checking Node.js...
node --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo ERROR: Node.js not found!
    echo Please download and install it from https://nodejs.org
    echo Then run this script again.
    echo.
    pause
    exit /b 1
)
node --version
echo Node.js found!

echo.
echo [2/3] Installing dependencies...
call npm install
if errorlevel 1 (
    echo.
    echo ERROR: npm install failed. Check your internet connection.
    pause
    exit /b 1
)

echo.
echo [3/3] Building installer (.exe)...
call npm run build
if errorlevel 1 (
    echo.
    echo ERROR: Build failed. See messages above.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   SUCCESS! Your installer is in the "dist" folder.
echo   Share the .exe in that folder with your coworkers.
echo ============================================================
echo.
explorer dist
pause
