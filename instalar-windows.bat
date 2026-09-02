@echo off
title Papagaio Transcritor - Instalacao
cd /d "%~dp0"

REM ============================================================
REM  Instalacao de 1 clique. Precisa de administrador para
REM  habilitar o WSL, que e o backend do Docker no Windows.
REM  A logica fica em scripts\papagaio.ps1.
REM ============================================================

net session >nul 2>&1
if not errorlevel 1 goto rodar

echo.
echo  Pedindo permissao de administrador...
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

:rodar
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\papagaio.ps1" -Acao setup
exit /b
