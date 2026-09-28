@echo off
title Video Downloader

echo ========================================
echo        Google Video Downloader
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
python -m pip install requests
python -m pip install -U yt-dlp

echo.
echo Starting download...
echo.

python video_downloader_ui.py


echo.
echo ========================================
echo Download finished.
echo ========================================
pause