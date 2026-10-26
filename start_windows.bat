@echo off
setlocal EnableExtensions
chcp 65001 >nul 2>&1
title Quillon LeadLoop - Inicializacao
cd /d "%~dp0"
if errorlevel 1 (
    echo ERRO: Nao foi possivel localizar a pasta da aplicacao.
    pause
    exit /b 1
)

where powershell.exe >nul 2>&1
if errorlevel 1 (
    echo ERRO: Windows PowerShell nao foi encontrado.
    echo Este instalador precisa do PowerShell presente no Windows 10/11.
    pause
    exit /b 1
)

echo.
echo ================================================
echo QUILLON LEADLOOP - INSTALACAO E INICIALIZACAO
echo ================================================
echo.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0bootstrap_windows.ps1"
set "CODE=%ERRORLEVEL%"
echo.
if not "%CODE%"=="0" (
    echo FALHA: o processo terminou com codigo %CODE%.
    echo Confira logs\startup_last.log e compartilhe o erro se necessario.
) else (
    echo Aplicacao encerrada normalmente.
)
echo.
echo A janela ficara aberta ate voce confirmar.
pause
exit /b %CODE%
