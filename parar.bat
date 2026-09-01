@echo off
title Papagaio Transcritor - Desligar
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\papagaio.ps1" -Acao parar
exit /b
