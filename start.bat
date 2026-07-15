@echo off
setlocal
cd /d "%~dp0"

set "PATH=%USERPROFILE%\.local\bin;%USERPROFILE%\.cargo\bin;%PATH%"

where uv >nul 2>&1
if errorlevel 1 (
    echo [GalMaster] uv not found. Install with: winget install astral-sh.uv
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo [GalMaster] First run: installing dependencies...
    uv sync
    if errorlevel 1 (
        echo [GalMaster] uv sync failed
        pause
        exit /b 1
    )
)

echo [GalMaster] Starting...
uv run galmaster
if errorlevel 1 (
    echo.
    echo [GalMaster] Exit code %ERRORLEVEL%
    pause
)

endlocal
