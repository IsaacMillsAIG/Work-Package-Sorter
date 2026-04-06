@echo off
title Building sorter.exe...

:: Always run from the folder this script lives in
cd /d "%~dp0"

echo.
echo ============================================================
echo   Building sorter.exe from work_package_sorter.py
echo ============================================================
echo.

:: Check Python
py --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found. Install from python.org first.
    pause & exit /b 1
)

:: Install PyInstaller if needed
echo Installing PyInstaller...
py -m pip install pyinstaller pdfplumber pypdf pyyaml --quiet --break-system-packages 2>nul
py -m pip install pyinstaller pdfplumber pypdf pyyaml --quiet 2>nul

:: Build the exe
echo.
echo Compiling work_package_sorter.py into sorter.exe...
py -m PyInstaller --onefile --name sorter --distpath . --workpath build_tmp --specpath build_tmp --noconfirm work_package_sorter.py

if errorlevel 1 (
    echo.
    echo ERROR: Build failed. See output above.
    pause & exit /b 1
)

:: Clean up temp files
if exist build_tmp rmdir /s /q build_tmp

echo.
echo ============================================================
echo   SUCCESS: sorter.exe is ready in this folder.
echo   Now run: npm run build
echo ============================================================
echo.
pause
