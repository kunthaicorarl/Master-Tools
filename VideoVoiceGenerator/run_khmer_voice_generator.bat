@echo off
title Khmer Voice Generator

echo ==========================================
echo   Khmer Voice Generator
echo ==========================================
echo.

python -m pip install --upgrade pip
python -m pip install PySide6 edge-tts

echo.
echo Starting application...
echo.

python "%~dp0khmer_voice_generator.py"

pause
