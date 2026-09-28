@echo off
setlocal
title AI Movie Subtitle Translator

cd /d "%~dp0"

echo ==========================================
echo   AI Movie Subtitle Translator
echo ==========================================
echo.

where py >nul 2>nul
if %errorlevel%==0 (
    set "PY=py"
) else (
    set "PY=python"
)

echo Checking Python...
%PY% --version
if errorlevel 1 (
    echo.
    echo Python was not found.
    echo Install Python 3.11+ and enable "Add Python to PATH".
    pause
    exit /b 1
)

echo.
echo Installing/updating required packages...
%PY% -m pip install --upgrade pip
%PY% -m pip install PySide6 PySide6-Essentials PySide6-Addons

if errorlevel 1 (
    echo.
    echo Failed to install packages.
    pause
    exit /b 1
)

echo.
echo Starting application...
%PY% "%~dp0movie_ai_translator.py"

if errorlevel 1 (
    echo.
    echo Application stopped with an error.
    pause
)

endlocal
