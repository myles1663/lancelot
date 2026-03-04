@echo off
REM ── start-uab.bat ──────────────────────────────────────────────────
REM Starts the Universal App Bridge (UAB) daemon on Windows.
REM UAB is licensed under MIT (see packages\uab\LICENSE).
REM ────────────────────────────────────────────────────────────────────

echo ================================================
echo   Universal App Bridge (UAB) v0.5.0 — Daemon
echo   Licensed under MIT
echo ================================================
echo.

cd /d "%~dp0..\packages\uab"

REM Check if daemon is built
if not exist "dist\daemon.js" (
    echo dist\daemon.js not found — building UAB...
    call npm install
    call npm run build
    echo.
)

echo TIP: For auto-start on login, run scripts\install-uab.bat instead.
echo.
echo Starting UAB daemon on port 7900 (foreground)...
node dist\daemon.js --port 7900
