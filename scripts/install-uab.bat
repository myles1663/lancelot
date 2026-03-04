@echo off
title Lancelot UAB Daemon — Installer
echo.
echo  ========================================
echo   Lancelot UAB Daemon — Install Service
echo  ========================================
echo.

:: Check for Node.js
where node >nul 2>&1
if errorlevel 1 (
    echo  ERROR: node.exe not found in PATH.
    echo  Please install Node.js 18+ and ensure it's in your PATH.
    echo.
    pause
    exit /b 1
)

:: Check Node.js version >= 18
for /f "tokens=1 delims=v." %%v in ('node -v') do set NODE_MAJOR=%%v
:: node -v returns "v18.x.y" — strip the 'v' prefix
for /f "tokens=1 delims=." %%v in ('node -v') do set NODE_VER_RAW=%%v
set NODE_MAJOR=%NODE_VER_RAW:v=%
if %NODE_MAJOR% LSS 18 (
    echo  ERROR: Node.js 18+ required. Found: v%NODE_MAJOR%
    echo  Please upgrade Node.js from https://nodejs.org/
    echo.
    pause
    exit /b 1
)

:: Get the UAB package directory (relative to this script)
set UAB_DIR=%~dp0..\packages\uab
cd /d "%UAB_DIR%"
if errorlevel 1 (
    echo  ERROR: packages\uab directory not found.
    echo  Expected at: %UAB_DIR%
    echo.
    pause
    exit /b 1
)

:: Resolve to absolute path
set UAB_DIR=%CD%

:: Build if dist\daemon.js is missing
if not exist "dist\daemon.js" (
    echo  dist\daemon.js not found — building UAB...
    call npm install
    if errorlevel 1 (
        echo  ERROR: npm install failed.
        echo.
        pause
        exit /b 1
    )
    call npm run build
    if errorlevel 1 (
        echo  ERROR: npm run build failed.
        echo.
        pause
        exit /b 1
    )
    echo  Build complete.
    echo.
) else (
    echo  dist\daemon.js found — skipping build.
)

:: Find node.exe full path
for /f "tokens=*" %%i in ('where node') do set NODE_PATH=%%i

echo  UAB directory:  %UAB_DIR%
echo  Node.js:        %NODE_PATH%
echo  Port:           7900
echo.

:: Remove existing scheduled task if it exists (ignore errors)
schtasks /Delete /TN "LancelotUABDaemon" /F >nul 2>&1

:: Create scheduled task that runs at user logon
echo  Creating scheduled task "LancelotUABDaemon"...
schtasks /Create /TN "LancelotUABDaemon" /TR "cmd /c cd /d \"%UAB_DIR%\" && \"%NODE_PATH%\" dist\daemon.js --port 7900" /SC ONLOGON /RL HIGHEST /F >nul 2>&1

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

:: Kill any existing UAB daemon processes
echo  Stopping any existing UAB daemon...
for /f "tokens=2" %%a in ('tasklist /FI "IMAGENAME eq node.exe" /FO LIST ^| findstr "PID:"') do (
    wmic process where "ProcessId=%%a" get CommandLine 2>nul | findstr /C:"daemon.js" | findstr /C:"7900" >nul 2>&1
    if not errorlevel 1 taskkill /PID %%a /F >nul 2>&1
)

:: Start the daemon immediately
echo  Starting UAB daemon...
start "" /B "%NODE_PATH%" dist\daemon.js --port 7900

:: Wait and verify
timeout /t 3 /nobreak >nul
curl -s http://127.0.0.1:7900 >nul 2>&1
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
echo  - Accept commands from Lancelot container on port 7900
echo.
echo  To uninstall: run scripts\uninstall-uab.bat
echo  To check:     schtasks /Query /TN "LancelotUABDaemon"
echo.
pause
