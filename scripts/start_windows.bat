@echo off
chcp 65001 > nul
setlocal
title MOSS MCP
cd /d "%~dp0.."

where pwsh >nul 2>nul
if not errorlevel 1 ( set "PS_EXE=pwsh" ) else ( set "PS_EXE=powershell" )

"%PS_EXE%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_windows.ps1"
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo [ERROR] MOSS MCP exited with code %EXIT_CODE%.
)

echo.
pause
exit /b %EXIT_CODE%
