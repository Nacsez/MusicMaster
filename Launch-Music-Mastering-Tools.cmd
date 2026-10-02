@echo off
setlocal EnableExtensions

rem Always launch relative to this file, even when Explorer or another process
rem supplies a different working directory.
cd /d "%~dp0"

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\Launch-Workbench.ps1" %*
set "MMT_LAUNCH_EXIT_CODE=%ERRORLEVEL%"

exit /b %MMT_LAUNCH_EXIT_CODE%
