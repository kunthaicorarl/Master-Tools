@echo off
setlocal
title Subtitle Translator - Batch

cd /d "%~dp0"

echo ==========================================
echo   Subtitle Translator - Batch Translator
echo ==========================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo ERROR: Python was not found.
    echo Please install Python and enable "Add Python to PATH".
    pause
    exit /b 1
)

echo Checking deep-translator...
python -m pip install --upgrade deep-translator
if errorlevel 1 (
    echo.
    echo WARNING: Could not install/update deep-translator.
    echo The program may fail if the package is missing.
    echo.
)

echo.
echo Starting translator...
python "%~dp0subtitle_translator_ui_batch.py"

if errorlevel 1 (
    echo.
    echo Program exited with an error.
    pause
)

endlocal
