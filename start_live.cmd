@echo off
rem Dauerpublisher (P1F) starten - enthaelt keine Geheimnisse.
rem   start_live.cmd <Event-Slug>   Dauerbetrieb fuer die in .env konfigurierten Arbeitsmappen (Ctrl+C beendet)
rem   start_live.cmd --check        beide Arbeitsmappen einmal pruefen, ohne Netzwerk
rem Konfiguration: .env im Projektstamm (Vorlage .env.example). Keine Aktivierung einer venv noetig.
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONPATH=%~dp0src"
set "PYTHONDONTWRITEBYTECODE=1"
set "PYTHONIOENCODING=utf-8"

set "PY="
if exist "%~dp0.venv\Scripts\python.exe" set "PY="%~dp0.venv\Scripts\python.exe""
if not defined PY (
    where python >nul 2>nul && set "PY=python"
)
if not defined PY (
    where py >nul 2>nul && set "PY=py -3"
)
if not defined PY (
    echo FEHLER: Python wurde nicht gefunden. Erwartet wird .venv\Scripts\python.exe im Projektordner
    echo oder Python im Suchpfad. Danach einmalig: python -m pip install -r requirements.txt
    exit /b 9
)

if "%~1"=="" (
    echo Aufruf: start_live.cmd ^<Event-Slug^>     Dauerbetrieb, Beenden mit Ctrl+C
    echo         start_live.cmd --check           Arbeitsmappen pruefen, ohne Netzwerk
    exit /b 2
)
if /i "%~1"=="--check" (
    %PY% -B -m testtag_public.live_runner --check
) else (
    %PY% -B -m testtag_public.live_runner --confirm-slug "%~1"
)
exit /b %ERRORLEVEL%
