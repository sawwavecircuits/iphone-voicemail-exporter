@echo off
REM Build voicemail_exporter as a standalone Windows EXE using PyInstaller.

setlocal

set SCRIPT_DIR=%~dp0
cd /d "%SCRIPT_DIR%"

echo =^> Checking for PyInstaller...
python -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
    echo Installing PyInstaller...
    pip install pyinstaller
)

echo =^> Building voicemail_exporter...
python -m PyInstaller ^
    --onefile ^
    --name voicemail_exporter ^
    voicemail_exporter.py

echo.
echo Build complete! Binary is at: dist\voicemail_exporter.exe
echo.
echo Usage:
echo   dist\voicemail_exporter.exe --help
echo   dist\voicemail_exporter.exe --output .\my_voicemails

endlocal
