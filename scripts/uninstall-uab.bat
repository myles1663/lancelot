@echo off
title Lancelot UAB Daemon — Uninstaller
echo.
echo  ========================================
echo   Lancelot UAB Daemon — Uninstall
echo  ========================================
echo.

:: Kill any running UAB daemon processes
echo  Stopping UAB daemon processes...
for /f "tokens=2" %%a in ('tasklist /FI "IMAGENAME eq node.exe" /FO LIST ^| findstr "PID:"') do (
    wmic process where "ProcessId=%%a" get CommandLine 2>nul | findstr /C:"daemon.js" | findstr /C:"7900" >nul 2>&1
    if not errorlevel 1 taskkill /PID %%a /F >nul 2>&1
)

:: Remove the scheduled task
echo  Removing scheduled task "LancelotUABDaemon"...
schtasks /Delete /TN "LancelotUABDaemon" /F >nul 2>&1

if errorlevel 1 (
    echo  No scheduled task found (may already be removed).
) else (
    echo  Scheduled task removed.
)

echo.
echo  ========================================
echo   Uninstall Complete
echo  ========================================
echo.
echo  The UAB daemon has been stopped and the
echo  auto-start task has been removed.
echo.
echo  To reinstall: run scripts\install-uab.bat
echo.
pause
