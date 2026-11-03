@echo off
rem One click launcher: both directions (mic to English on CABLE Input, system audio to Spanish subtitles).
rem Extra arguments pass through, e.g.  Interprete.bat --record-cable
title Interprete ES-EN
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONIOENCODING=utf-8

where uv >nul 2>nul
if errorlevel 1 (
    echo uv is not on the PATH. Install it from https://docs.astral.sh/uv/
    goto end
)
if not exist ".env" (
    echo MODEL_API_KEY is not set: copy .env.example to .env and put your key there. Get one at https://dev.meta.ai
    goto end
)

echo Interprete ES-EN    F6 Spanish voice on/off    F7 AI notice    F8 mute    F9 raw voice    F10 physical mic    Ctrl-C quit
echo In the call, pick "CABLE Output" as the microphone: only English reaches the other side.
echo With the dub (RTT_DUB=1), also pick "OBS Virtual Camera" as the camera, and keep OBS closed.
echo Loading takes about 20 s. Nothing leaves this PC until you click the round button, top right.
echo Click it again, or right-click it, to stop everything and give the microphone back.
echo.
uv run --locked python -m interprete.app %*

:end
echo.
pause
