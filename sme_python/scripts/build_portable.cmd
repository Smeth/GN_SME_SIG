@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0.."
python packaging\build_portable.py %*
if errorlevel 1 exit /b 1
exit /b 0
