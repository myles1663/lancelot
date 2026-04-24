@echo off
setlocal
title Lancelot UAB Daemon - Installer

echo.
echo  ========================================
echo   Lancelot UAB Daemon - Install Service
echo  ========================================
echo.

where node >nul 2>&1
if errorlevel 1 (
    echo  ERROR: node.exe not found in PATH.
    echo  Please install Node.js 18+ and ensure it is in your PATH.
    echo.
    pause
    exit /b 1
)

for /f "usebackq delims=" %%v in (`node -p "process.versions.node.split('.')[0]"`) do set "NODE_MAJOR=%%v"
if not defined NODE_MAJOR (
    echo  ERROR: Unable to determine Node.js version.
    echo.
    pause
    exit /b 1
)
if %NODE_MAJOR% LSS 18 (
    echo  ERROR: Node.js 18+ required. Found major version: %NODE_MAJOR%
    echo  Please upgrade Node.js from https://nodejs.org/
    echo.
    pause
    exit /b 1
)

set "ROOT_DIR=%~dp0.."
set "UAB_DIR=%ROOT_DIR%\packages\uab"
pushd "%UAB_DIR%" >nul 2>&1
if errorlevel 1 (
    echo  ERROR: packages\uab directory not found.
    echo  Expected at: %UAB_DIR%
    echo.
    pause
    exit /b 1
)
set "UAB_DIR=%CD%"

if not exist "dist\daemon.js" (
    echo  dist\daemon.js not found - building UAB...
    call npm install
    if errorlevel 1 (
        echo  ERROR: npm install failed.
        popd >nul
        echo.
        pause
        exit /b 1
    )
    call npm run build
    if errorlevel 1 (
        echo  ERROR: npm run build failed.
        popd >nul
        echo.
        pause
        exit /b 1
    )
    echo  Build complete.
    echo.
) else (
    echo  dist\daemon.js found - skipping build.
)
popd >nul

set "RUN_SCRIPT=%ROOT_DIR%\scripts\run-uab-daemon.bat"
if not exist "%RUN_SCRIPT%" (
    echo  ERROR: launcher script not found.
    echo  Expected at: %RUN_SCRIPT%
    echo.
    pause
    exit /b 1
)

echo  UAB directory:  %UAB_DIR%
echo  Launcher:       %RUN_SCRIPT%
echo  Host:           127.0.0.1
echo  Port:           7900
echo.

schtasks /Delete /TN "LancelotUABDaemon" /F >nul 2>&1

echo  Creating scheduled task "LancelotUABDaemon"...
schtasks /Create /TN "LancelotUABDaemon" /TR "\"%RUN_SCRIPT%\"" /SC ONLOGON /RL HIGHEST /F >nul 2>&1
if errorlevel 1 (
    echo.
    echo  ERROR: Failed to create scheduled task.
    echo  Try running this script as Administrator.
    echo.
    pause
    exit /b 1
)

echo  Scheduled task created successfully.
echo.

echo  Stopping any existing UAB daemon...
for /f "tokens=2" %%a in ('tasklist /FI "IMAGENAME eq node.exe" /FO LIST ^| findstr "PID:"') do (
    wmic process where "ProcessId=%%a" get CommandLine 2>nul | findstr /C:"daemon.js" | findstr /C:"7900" >nul 2>&1
    if not errorlevel 1 taskkill /PID %%a /F >nul 2>&1
)

echo  Starting UAB daemon...
start "" /B "%RUN_SCRIPT%"

timeout /t 3 /nobreak >nul
curl -s -X POST -H "Content-Type: application/json" -d "{\"jsonrpc\":\"2.0\",\"method\":\"getStatus\",\"params\":{},\"id\":1}" http://127.0.0.1:7900 >nul 2>&1
if errorlevel 1 (
    echo  WARNING: UAB daemon may not have started. Check if port 7900 is available.
) else (
    echo  UAB daemon is running on http://127.0.0.1:7900
)

echo.
echo  ========================================
echo   Installation Complete
echo  ========================================
echo.
echo  The UAB daemon will now:
echo  - Run silently in the background
echo  - Auto-start when you log in to Windows
echo  - Listen on 127.0.0.1:7900 by default
echo  - Use UAB_DAEMON_HOST only when an explicit trusted bridge is required
echo.
echo  To uninstall: run scripts\uninstall-uab.bat
echo  To check:     schtasks /Query /TN "LancelotUABDaemon"
echo.
pause
