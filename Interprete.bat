@echo off
rem One click launcher: both directions (mic to English on CABLE Input, system audio to Spanish subtitles).
rem Extra arguments pass through, e.g.  Interprete.bat --record-cable
title Interprete ES-EN
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONIOENCODING=utf-8

where uv >nul 2>nul
if errorlevel 1 (
    echo No encuentro uv en el PATH. Instalalo desde https://docs.astral.sh/uv/
    goto end
)
if not exist ".env" (
    echo Falta .env con MODEL_API_KEY. Copia .env.example a .env y pon tu clave.
    goto end
)

echo Interprete ES-EN    F7 aviso    F8 mudo    F9 tu voz cruda on/off    F10 mic fisico    Ctrl-C salir
echo En la videollamada elige "CABLE Output" como microfono: al otro lado solo llega ingles.
echo Mientras esta ventana siga abierta, lo que oye el microfono va a la API de Meta. Cierrala al terminar.
echo.
uv run --locked python -m interprete.app %*

:end
echo.
pause
