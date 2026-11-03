@echo off
rem One-click setup on Windows 11: uv, Python 3.13 and the locked packages, then the setup assistant,
rem which Muse Spark drives with a fixed set of tools. Extra arguments reach it, e.g.  Install.bat --no-spark
title Interprete ES-EN setup
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install.ps1" %*
echo.
pause
