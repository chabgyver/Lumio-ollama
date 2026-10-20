@echo off
rem ============================================================
rem  Lumio - lanceur unique
rem    1. Ollama      (obligatoire)
rem    2. PrivateGPT  (optionnel : ignore s'il est absent)
rem    3. Lumio
rem
rem  Les chemins passes a pythonw restent en UNC (%~dp0) : un lecteur
rem  temporaire cree par pushd serait demonte avant que l'appli demarre.
rem ============================================================
title Lumio
setlocal EnableExtensions

set "APP=%~dp0gui_tool.py"
set "OLLAMA_EXE=%LOCALAPPDATA%\Programs\Ollama\ollama.exe"
rem PrivateGPT est optionnel : surchargez le dossier avec LUMIO_PGPT_DIR.
if not defined LUMIO_PGPT_DIR set "LUMIO_PGPT_DIR=D:\private-gpt-main"
set "PGPT_DIR=%LUMIO_PGPT_DIR%"
set "PGPT_EXE=%PGPT_DIR%\.venv\Scripts\private-gpt.exe"

if not exist "%APP%" (
    echo Lumio est introuvable :
    echo   %APP%
    pause
    exit /b 1
)

rem ---- Un seul interpreteur : python et pythonw du meme dossier ----
set "PY="
for /f "delims=" %%I in ('where python 2^>nul') do if not defined PY set "PY=%%I"
if not defined PY (
    echo Python est introuvable dans le PATH.
    echo Installez Python 3 depuis https://www.python.org puis relancez.
    pause
    exit /b 1
)
for %%A in ("%PY%") do set "PYDIR=%%~dpA"
set "PYW=%PY%"
if exist "%PYDIR%pythonw.exe" set "PYW=%PYDIR%pythonw.exe"
echo Python : "%PY%"

echo.
echo [1/3] Ollama...
call :ensure_http "http://127.0.0.1:11434/api/tags"
if errorlevel 1 (
    if exist "%OLLAMA_EXE%" (
        start "" /min "%OLLAMA_EXE%" serve
    ) else (
        where ollama >nul 2>&1
        if errorlevel 1 (
            echo       Ollama est introuvable. Installez-le, puis relancez.
            pause
            exit /b 1
        )
        start "" /min ollama serve
    )
    call :wait_http "http://127.0.0.1:11434/api/tags" 30 "Ollama"
    if errorlevel 1 (
        echo       Ollama ne repond pas. Ouvrez-le manuellement, puis relancez.
        pause
        exit /b 1
    )
)
echo       pret.

echo [2/3] PrivateGPT ^(optionnel^)...
if not exist "%PGPT_EXE%" (
    echo       absent - ignore.
    goto pgpt_done
)
call :ensure_http "http://127.0.0.1:8080/health"
if not errorlevel 1 (
    echo       pret.
    goto pgpt_done
)
start "PrivateGPT" /min /d "%PGPT_DIR%" "%PGPT_EXE%" serve
call :wait_http "http://127.0.0.1:8080/health" 20 "PrivateGPT"
if errorlevel 1 (
    echo       ne repond pas - Lumio demarre quand meme ^(moteur Ollama^).
) else (
    echo       pret.
)
:pgpt_done

echo [3/3] Lumio...
"%PY%" -c "import customtkinter" 1>nul 2>nul
if errorlevel 1 (
    echo       installation de customtkinter...
    "%PY%" -m pip install customtkinter
    if errorlevel 1 (
        echo       Echec de l'installation de customtkinter.
        pause
        exit /b 1
    )
)
"%PY%" -c "import tkinterdnd2" 1>nul 2>nul
if errorlevel 1 "%PY%" -m pip install tkinterdnd2

start "" "%PYW%" "%APP%"
exit /b 0

rem ============================================================
rem  Sous-programmes
rem ============================================================
:ensure_http
"%PY%" -c "import urllib.request,sys; urllib.request.urlopen(sys.argv[1], timeout=2)" "%~1" 1>nul 2>nul
exit /b %errorlevel%

:wait_http
set "_URL=%~1"
set "_MAX=%~2"
set "_NAME=%~3"
set /a "_N=0"
:wait_http_loop
"%PY%" -c "import urllib.request,sys; urllib.request.urlopen(sys.argv[1], timeout=2)" "%_URL%" 1>nul 2>nul
if not errorlevel 1 exit /b 0
set /a "_N+=1"
if %_N% geq %_MAX% exit /b 1
echo       attente %_NAME% ^(%_N%/%_MAX%^)...
ping -n 3 127.0.0.1 >nul 2>&1
goto wait_http_loop
