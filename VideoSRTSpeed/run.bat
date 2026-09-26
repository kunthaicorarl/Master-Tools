@echo off
title Video SRT SPEED

echo ========================================
echo        Google Video SRT Speed
echo ========================================
echo.

python --version

if errorlevel 1 (
    echo.
    echo Python is not installed or not in PATH.
    pause
    exit /b 1
)

echo.
echo Installing/checking requests...
python -m pip install edge-tts

echo.
echo Starting Programming...
echo.

python srt_to_speech_gui.py

echo.
echo ========================================
echo Download finished.
echo ========================================
pause