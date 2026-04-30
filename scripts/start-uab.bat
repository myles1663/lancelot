@echo off
setlocal

echo ================================================
echo   Universal App Bridge (UAB) v1.3.0 - Daemon
echo   JSON-RPC compatibility bridge for Lancelot
echo ================================================
echo.

pushd "%~dp0..\packages\uab" >nul 2>&1
if errorlevel 1 (
    echo ERROR: packages\uab directory not found.
    exit /b 1
)

if not exist "dist\daemon.js" (
    echo dist\daemon.js not found - building UAB...
    call npm install
    if errorlevel 1 (
        popd >nul
        exit /b 1
    )
    call npm run build
    if errorlevel 1 (
        popd >nul
        exit /b 1
    )
    echo.
)
popd >nul

echo TIP: For auto-start on login, run scripts\install-uab.bat instead.
echo.
echo Starting UAB daemon on port 7900 (foreground)...
call "%~dp0run-uab-daemon.bat"
