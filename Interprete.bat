@echo off
rem One click launcher: outgoing interpreter (mic -> ASR -> Spark -> TTS -> CABLE Input).
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

echo Interprete ES-EN    F9 corta y vuelve a tu voz    F8 mudo    Ctrl-C salir
echo En la videollamada elige "CABLE Output" como microfono.
echo.
uv run --locked python -m interprete.app %*

:end
echo.
pause
