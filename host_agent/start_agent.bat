@echo off
title Lancelot Host Agent
echo.
echo  ========================================
echo   Lancelot Host Agent
echo  ========================================
echo.
echo  This agent bridges Lancelot (Docker) to
echo  your host operating system.
echo.
echo  Keep this window open while using
echo  the Host Bridge feature in Lancelot.
echo.
echo  Press Ctrl+C to stop.
echo  ========================================
echo.

if not defined HOST_AGENT_TOKEN (
    echo  ERROR: HOST_AGENT_TOKEN is not set.
    echo  Set a strong shared token before starting the host agent.
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

python "%~dp0agent.py" --token "%HOST_AGENT_TOKEN%"

if errorlevel 1 (
    echo.
    echo  ERROR: Failed to start host agent.
    echo  Make sure Python is installed and in your PATH.
    echo.
    pause
)
