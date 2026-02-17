@echo off
title Lancelot Host Agent — Uninstaller
echo.
echo  ========================================
echo   Lancelot Host Agent — Uninstall
echo  ========================================
echo.

:: Stop the running agent via shutdown endpoint
echo  Stopping running agent...
curl -s -X POST -H "Authorization: Bearer lancelot-host-agent" http://127.0.0.1:9111/shutdown >nul 2>&1
timeout /t 2 /nobreak >nul

:: Kill any remaining pythonw agent processes
for /f "tokens=2" %%a in ('tasklist /FI "IMAGENAME eq pythonw.exe" /FO LIST ^| findstr "PID:"') do (
    wmic process where "ProcessId=%%a" get CommandLine 2>nul | findstr /C:"agent.py" >nul 2>&1
    if not errorlevel 1 taskkill /PID %%a /F >nul 2>&1
)

:: Remove scheduled task
echo  Removing scheduled task "LancelotHostAgent"...
schtasks /Delete /TN "LancelotHostAgent" /F >nul 2>&1

if errorlevel 1 (
    echo  Note: Scheduled task was not found (may already be removed).
) else (
    echo  Scheduled task removed.
)

echo.
echo  ========================================
echo   Uninstall Complete
echo  ========================================
echo.
echo  The host agent has been stopped and the
echo  scheduled task has been removed. It will
echo  no longer auto-start on login.
echo.
pause
