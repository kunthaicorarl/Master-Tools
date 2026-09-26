@echo off
setlocal

title Split Video + Transcript into 5 Minute Parts

echo.
echo ============================================================
echo   Split Video + Transcript into 5 Minute Parts
echo ============================================================
echo.

REM Check Python
where python >nul 2>nul
if errorlevel 1 (
    echo ERROR: Python was not found in PATH.
    echo Install Python 3 and make sure "Add Python to PATH" is enabled.
    pause
    exit /b 1
)

REM Check FFmpeg
where ffmpeg >nul 2>nul
if errorlevel 1 (
    echo ERROR: FFmpeg was not found in PATH.
    echo Install FFmpeg and add its bin folder to PATH.
    pause
    exit /b 1
)

where ffprobe >nul 2>nul
if errorlevel 1 (
    echo ERROR: FFprobe was not found in PATH.
    echo Install FFmpeg and add its bin folder to PATH.
    pause
    exit /b 1
)

echo Starting...
echo.

python "%~dp0split_video_transcript.py"

if errorlevel 1 (
    echo.
    echo ============================================================
    echo   PROCESS FAILED
    echo ============================================================
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   PROCESS COMPLETE
echo ============================================================
echo.
pause
endlocal
