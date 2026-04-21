@echo off
title Lancelot Host Agent — Installer
echo.
echo  ========================================
echo   Lancelot Host Agent — Install Service
echo  ========================================
echo.

:: Check for Python
where pythonw >nul 2>&1
if errorlevel 1 (
    echo  ERROR: pythonw.exe not found in PATH.
    echo  Please install Python and ensure it's in your PATH.
    echo.
    pause
    exit /b 1
)

:: Get the directory where this script lives
set AGENT_DIR=%~dp0
set AGENT_SCRIPT=%AGENT_DIR%agent.py

:: Check agent.py exists
if not exist "%AGENT_SCRIPT%" (
    echo  ERROR: agent.py not found at %AGENT_SCRIPT%
    echo.
    pause
    exit /b 1
)

set "LANCELOT_ENV=%AGENT_DIR%..\.env"
if not defined HOST_AGENT_TOKEN if exist "%LANCELOT_ENV%" (
    for /f "usebackq tokens=1,* delims==" %%A in (`findstr /B /C:"HOST_AGENT_TOKEN=" "%LANCELOT_ENV%"`) do (
        set "HOST_AGENT_TOKEN=%%B"
    )
)

if not defined HOST_AGENT_TOKEN (
    echo  ERROR: HOST_AGENT_TOKEN is not set.
    echo  Add HOST_AGENT_TOKEN to the repo .env file or export it before installing the service.
    echo  Set a strong shared token before installing the host agent service.
    echo.
    pause
    exit /b 1
)

if "%HOST_AGENT_TOKEN%"=="lancelot-host-agent" (
    echo  ERROR: HOST_AGENT_TOKEN is still using the legacy default value.
    echo  Set a unique token on both the host agent and Lancelot container first.
    echo.
    pause
    exit /b 1
)

:: Find pythonw.exe full path
for /f "tokens=*" %%i in ('where pythonw') do set PYTHONW_PATH=%%i

echo  Agent script:  %AGENT_SCRIPT%
echo  Python:        %PYTHONW_PATH%
echo  Token:         %HOST_AGENT_TOKEN:~0,4%...
echo.

:: Remove existing task if it exists (ignore errors)
schtasks /Delete /TN "LancelotHostAgent" /F >nul 2>&1

:: Create scheduled task that runs at user logon (hidden, no window)
echo  Creating scheduled task "LancelotHostAgent"...
schtasks /Create /TN "LancelotHostAgent" /TR "\"%PYTHONW_PATH%\" \"%AGENT_SCRIPT%\" --token \"%HOST_AGENT_TOKEN%\"" /SC ONLOGON /RL HIGHEST /F >nul 2>&1

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

:: Start the agent immediately (kill any existing instance first)
echo  Starting agent now...
taskkill /F /FI "WINDOWTITLE eq Lancelot Host Agent*" >nul 2>&1

:: Kill any existing pythonw agent instances
for /f "tokens=2" %%a in ('tasklist /FI "IMAGENAME eq pythonw.exe" /FO LIST ^| findstr "PID:"') do (
    wmic process where "ProcessId=%%a" get CommandLine 2>nul | findstr /C:"agent.py" >nul 2>&1
    if not errorlevel 1 taskkill /PID %%a /F >nul 2>&1
)

:: Launch via pythonw (no console window)
start "" /B "%PYTHONW_PATH%" "%AGENT_SCRIPT%" --token "%HOST_AGENT_TOKEN%"

:: Wait a moment and verify
timeout /t 3 /nobreak >nul
curl -s http://127.0.0.1:9111/health >nul 2>&1
if errorlevel 1 (
    echo  WARNING: Agent may not have started. Check if port 9111 is available.
) else (
    echo  Agent is running on http://127.0.0.1:9111
)

echo.
echo  ========================================
echo   Installation Complete
echo  ========================================
echo.
echo  The host agent will now:
echo  - Run silently in the background (no window)
echo  - Auto-start when you log in to Windows
echo  - Accept commands from Lancelot container
echo.
echo  To uninstall: run uninstall_service.bat
echo.
pause
