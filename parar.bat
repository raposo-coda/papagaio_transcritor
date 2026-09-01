@echo off
title Papagaio Transcritor - Parar
cd /d "%~dp0"

echo.
echo  Desligando o Papagaio Transcritor...
echo.
docker compose down
echo.
echo  Desligado. Seus relatorios continuam salvos na pasta "output".
echo.
timeout /t 6 >nul
exit /b
