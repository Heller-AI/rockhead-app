@echo off
REM ============================================================
REM  Build script for Rock Head Gradient Analyzer (.exe)
REM  Run this on Windows. Produces dist\RockHeadApp\ folder.
REM ============================================================

echo.
echo === Rock Head Gradient Analyzer - Build Script ===
echo.

REM Move to the folder this script lives in
cd /d "%~dp0"

echo [1/4] Checking Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found. Install Python 3.10+ and tick "Add to PATH".
    pause
    exit /b 1
)

echo [2/4] Installing/updating dependencies...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install pyinstaller
if errorlevel 1 (
    echo ERROR: Dependency install failed.
    pause
    exit /b 1
)

echo [3/4] Cleaning previous build...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist RockHeadApp.spec del /q RockHeadApp.spec

echo [4/4] Building executable with PyInstaller...
pyinstaller ^
    --name RockHeadApp ^
    --noconfirm ^
    --clean ^
    --add-data "rockhead_gradient_app.py;." ^
    --add-data "config.toml;.streamlit" ^
    --add-data "sample_pile_data.csv;." ^
    --collect-all streamlit ^
    --collect-all altair ^
    --collect-all ezdxf ^
    --collect-all matplotlib ^
    --collect-all streamlit_javascript ^
    --collect-all openpyxl ^
    --collect-data plotly ^
    --collect-data scipy ^
    --hidden-import=streamlit.web.cli ^
    --hidden-import=streamlit.runtime.scriptrunner.magic_funcs ^
    launcher.py

if errorlevel 1 (
    echo ERROR: PyInstaller build failed.
    pause
    exit /b 1
)

echo.
echo === BUILD COMPLETE ===
echo.
echo Output folder: dist\RockHeadApp\
echo Run with:     dist\RockHeadApp\RockHeadApp.exe
echo.
echo Next step (optional): wrap into a Windows installer with Inno Setup.
echo See: installer.iss
echo.
pause
