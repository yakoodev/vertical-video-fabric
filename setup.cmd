@echo off
REM Vertical Video Fabric — установка в один двойной клик.
REM Запускает интерактивный установщик (заполняет .env, собирает и открывает сервис).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup-windows.ps1"
echo.
pause
