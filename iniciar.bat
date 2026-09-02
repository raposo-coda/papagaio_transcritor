@echo off
title Papagaio Transcritor
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\papagaio.ps1" -Acao iniciar
exit /b
