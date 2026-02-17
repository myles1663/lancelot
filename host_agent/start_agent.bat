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

:: Use HOST_AGENT_TOKEN from environment if set, otherwise use default
if not defined HOST_AGENT_TOKEN set HOST_AGENT_TOKEN=lancelot-host-agent

python "%~dp0agent.py" --token "%HOST_AGENT_TOKEN%"

if errorlevel 1 (
    echo.
    echo  ERROR: Failed to start host agent.
    echo  Make sure Python is installed and in your PATH.
    echo.
    pause
)
