@echo off
setlocal

set "ROOT_DIR=%~dp0.."
set "UAB_DIR=%ROOT_DIR%\packages\uab"

pushd "%UAB_DIR%" >nul 2>&1
if errorlevel 1 (
    echo ERROR: packages\uab directory not found at "%UAB_DIR%".
    exit /b 1
)

if not exist "dist\daemon.js" (
    echo ERROR: dist\daemon.js not found. Run scripts\install-uab.bat first.
    popd >nul
    exit /b 1
)

for /f "delims=" %%i in ('where node') do set "NODE_PATH=%%i"
if not defined NODE_PATH (
    echo ERROR: node.exe not found in PATH.
    popd >nul
    exit /b 1
)

"%NODE_PATH%" dist\daemon.js --port 7900
set "EXIT_CODE=%ERRORLEVEL%"
popd >nul
exit /b %EXIT_CODE%
