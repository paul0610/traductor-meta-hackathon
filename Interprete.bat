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

echo Interprete ES-EN    F7 aviso    F8 mudo    F9 tu voz cruda on/off    F10 mic fisico    Ctrl-C salir
echo En la videollamada elige "CABLE Output" como microfono: al otro lado solo llega ingles.
echo Con el doblaje (RTT_DUB=1) elige tambien "OBS Virtual Camera" como camara y deja OBS cerrado.
echo Tarda unos 20 s en cargar. Nada sale de esta PC hasta que pulses el boton redondo, arriba a la derecha.
echo Otro clic, o clic derecho, apaga todo y restaura el microfono.
echo.
uv run --locked python -m interprete.app %*

:end
echo.
pause
