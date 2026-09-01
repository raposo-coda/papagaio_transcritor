@echo off
setlocal EnableDelayedExpansion
title Papagaio Transcritor - Instalador

REM ============================================================
REM  Papagaio Transcritor - instalador de 1 clique (Windows)
REM  Instala o Docker Desktop (se faltar), monta a aplicacao
REM  e abre o navegador. Nao precisa saber nada de terminal.
REM ============================================================

cd /d "%~dp0"

echo.
echo  ============================================
echo    PAPAGAIO TRANSCRITOR - INSTALACAO
echo  ============================================
echo.

REM ---------- 1. Precisa de administrador para instalar o Docker ----------
net session >nul 2>&1
if errorlevel 1 (
    echo  [1/5] Pedindo permissao de administrador...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs" >nul 2>&1
    if errorlevel 1 (
        echo.
        echo  [X] Nao foi possivel obter permissao de administrador.
        echo      Clique com o botao direito neste arquivo e escolha
        echo      "Executar como administrador".
        echo.
        pause
    )
    exit /b
)

echo  [1/5] Permissao de administrador: OK
echo.

REM ---------- 2. Docker instalado? ----------
echo  [2/5] Procurando o Docker...
docker --version >nul 2>&1
if not errorlevel 1 goto docker_ok

echo        Docker nao encontrado. Instalando automaticamente.
echo        Isso pode demorar de 5 a 15 minutos. Nao feche esta janela.
echo.

where winget >nul 2>&1
if errorlevel 1 goto no_winget

winget install --exact --id Docker.DockerDesktop --accept-source-agreements --accept-package-agreements --silent
if errorlevel 1 goto winget_failed

echo.
echo        Docker Desktop instalado.
echo        O Windows PRECISA ser reiniciado antes do primeiro uso.
echo.
echo        1) Reinicie o computador agora.
echo        2) Abra o Docker Desktop e aceite os termos.
echo        3) Rode este instalador de novo (duplo clique).
echo.
pause
exit /b

:no_winget
echo.
echo  [X] O instalador automatico (winget) nao existe nesta versao do Windows.
echo      Baixe o Docker Desktop manualmente em:
echo         https://www.docker.com/products/docker-desktop/
echo      Instale, reinicie o PC e rode este arquivo de novo.
echo.
start "" "https://www.docker.com/products/docker-desktop/"
pause
exit /b

:winget_failed
echo.
echo  [X] A instalacao automatica do Docker falhou.
echo      Baixe e instale manualmente em:
echo         https://www.docker.com/products/docker-desktop/
echo.
start "" "https://www.docker.com/products/docker-desktop/"
pause
exit /b

:docker_ok
echo        Docker encontrado: OK
echo.

REM ---------- 3. Motor do Docker ligado? ----------
echo  [3/5] Verificando se o Docker esta ligado...
docker info >nul 2>&1
if not errorlevel 1 goto engine_ok

echo        Docker esta instalado mas desligado. Abrindo o Docker Desktop...
start "" "%ProgramFiles%\Docker\Docker\Docker Desktop.exe" >nul 2>&1

set /a _tries=0
:wait_engine
set /a _tries+=1
if !_tries! GTR 60 goto engine_timeout
powershell -NoProfile -Command "Start-Sleep -Seconds 5" >nul 2>&1
docker info >nul 2>&1
if errorlevel 1 (
    echo        Aguardando o Docker iniciar... !_tries! de 60
    goto wait_engine
)

:engine_ok
echo        Docker ligado: OK
echo.
goto build

:engine_timeout
echo.
echo  [X] O Docker nao terminou de iniciar em 5 minutos.
echo      Abra o Docker Desktop na mao, espere aparecer "Engine running"
echo      no canto inferior esquerdo e rode este instalador de novo.
echo.
pause
exit /b

REM ---------- 4. Montar e subir a aplicacao ----------
:build
echo  [4/5] Montando o Papagaio Transcritor...
echo        Na primeira vez isso demora bastante: sao baixados Python, ffmpeg
echo        e o motor de transcricao local. Va tomar um cafe.
echo.
docker compose up -d --build
if errorlevel 1 goto build_failed
echo.
echo        Aplicacao no ar: OK
echo.

REM ---------- 5. Esperar o servidor responder e abrir o navegador ----------
echo  [5/5] Abrindo o navegador...
set /a _tries=0
:wait_http
set /a _tries+=1
if !_tries! GTR 40 goto http_timeout
powershell -NoProfile -Command "try { $r = Invoke-WebRequest -Uri 'http://localhost:8000/api/meta' -UseBasicParsing -TimeoutSec 3; if ($r.StatusCode -eq 200) { exit 0 } else { exit 1 } } catch { exit 1 }" >nul 2>&1
if errorlevel 1 (
    powershell -NoProfile -Command "Start-Sleep -Seconds 3" >nul 2>&1
    goto wait_http
)

start "" "http://localhost:8000"
echo.
echo  ============================================
echo    PRONTO! O Papagaio esta rodando.
echo  ============================================
echo.
echo    Endereco: http://localhost:8000
echo.
echo    PROXIMO PASSO: escolha o modo no passo 1 da tela.
echo.
echo    - MODO LOCAL: transcreve dentro do seu computador. Nada sai
echo      da maquina. Nao precisa de conta nem de chave. Ja separa
echo      os falantes pela voz, tambem offline. E mais lento que a
echo      nuvem e nao escreve um resumo interpretativo.
echo.
echo    - MODO NUVEM: envia os arquivos ao Google Gemini. Mais rapido,
echo      chama os falantes pelos nomes ditos na conversa e escreve um
echo      resumo. Precisa de uma chave gratuita: pegue em
echo      https://aistudio.google.com/apikey e cole no campo "API key"
echo      dentro do aplicativo.
echo.
echo    Os relatorios ficam salvos na pasta "output" aqui do lado.
echo.
echo    Para ligar de novo depois: duplo clique em "iniciar.bat"
echo    Para desligar:             duplo clique em "parar.bat"
echo.
pause
exit /b

:http_timeout
echo.
echo  [!] A aplicacao subiu mas nao respondeu a tempo.
echo      Tente abrir http://localhost:8000 no navegador.
echo      Se nao funcionar, veja o erro com: docker compose logs
echo.
start "" "http://localhost:8000"
pause
exit /b

:build_failed
echo.
echo  [X] Falha ao montar a aplicacao.
echo      Copie a mensagem de erro acima ao pedir ajuda.
echo.
pause
exit /b
