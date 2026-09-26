@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
    py "%~dp0transcript_ui.py"
    goto :end
)

where python >nul 2>nul
if %errorlevel%==0 (
    python "%~dp0transcript_ui.py"
    goto :end
)

echo.
echo Python was not found.
echo Please install Python 3 from https://www.python.org/
echo Then run this BAT file again.
echo.
pause

:end
endlocal
