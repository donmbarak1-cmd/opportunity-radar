@echo off
chcp 65001 >nul
title Opportunity Radar
cd /d "%~dp0"
echo Starting Opportunity Radar... (Ctrl+C to stop)
python radar.py
if errorlevel 1 (
  echo.
  echo Python command failed, trying py launcher...
  py radar.py
)
pause
