@echo off
setlocal EnableDelayedExpansion
title Papagaio Transcritor
cd /d "%~dp0"

echo.
echo  Ligando o Papagaio Transcritor...
echo.

docker --version >nul 2>&1
if errorlevel 1 goto sem_docker

docker info >nul 2>&1
if not errorlevel 1 goto subir

echo  Abrindo o Docker Desktop, aguarde...
start "" "%ProgramFiles%\Docker\Docker\Docker Desktop.exe" >nul 2>&1

set /a _tries=0
:wait_engine
set /a _tries+=1
if !_tries! GTR 60 goto engine_timeout
powershell -NoProfile -Command "Start-Sleep -Seconds 5" >nul 2>&1
docker info >nul 2>&1
if errorlevel 1 goto wait_engine

:subir
docker compose up -d
if errorlevel 1 goto falhou

set /a _tries=0
:wait_http
set /a _tries+=1
if !_tries! GTR 40 goto abrir
powershell -NoProfile -Command "try { $r = Invoke-WebRequest -Uri 'http://localhost:8000/api/meta' -UseBasicParsing -TimeoutSec 3; if ($r.StatusCode -eq 200) { exit 0 } else { exit 1 } } catch { exit 1 }" >nul 2>&1
if not errorlevel 1 goto abrir
powershell -NoProfile -Command "Start-Sleep -Seconds 3" >nul 2>&1
goto wait_http

:abrir
start "" "http://localhost:8000"
echo.
echo  Pronto. O Papagaio esta em http://localhost:8000
echo  Pode fechar esta janela - ele continua rodando.
echo.
timeout /t 8 >nul
exit /b

:sem_docker
echo  [X] O Docker nao esta instalado.
echo      Rode "instalar-windows.bat" como administrador primeiro.
echo.
pause
exit /b

:engine_timeout
echo.
echo  [X] O Docker nao terminou de iniciar.
echo      Abra o Docker Desktop na mao, espere aparecer "Engine running"
echo      e tente de novo.
echo.
pause
exit /b

:falhou
echo.
echo  [X] Nao foi possivel iniciar a aplicacao.
echo      Rode "instalar-windows.bat" como administrador primeiro.
echo      Para ver o erro completo: docker compose logs
echo.
pause
exit /b
