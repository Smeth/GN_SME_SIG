@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion

REM Construit la version portable dans dist\SME_Gendarmerie\
REM Prérequis : Python 3.10+ avec pip (même machine que le développement).

cd /d "%~dp0.."
set "ROOT=%CD%"

echo.
echo === SME Gendarmerie — build portable (PyInstaller) ===
echo Racine : %ROOT%
echo.

python --version >nul 2>&1
if errorlevel 1 (
  echo ERREUR : Python introuvable dans le PATH.
  exit /b 1
)

python -m pip install -q -e "%ROOT%"
if errorlevel 1 (
  echo ERREUR : installation du package sme-gend en mode editable.
  exit /b 1
)

python -m pip install -q "pyinstaller>=6.0"
if errorlevel 1 (
  echo ERREUR : installation de PyInstaller.
  exit /b 1
)

echo Lancement de PyInstaller (cela peut prendre plusieurs minutes^)...
python -m PyInstaller --noconfirm "%ROOT%\build\sme_gendarmerie_portable.spec"
if errorlevel 1 (
  echo ERREUR : PyInstaller a echoue.
  exit /b 1
)

set "OUT=%ROOT%\dist\SME_Gendarmerie"
if not exist "%OUT%\SME_Gendarmerie.exe" (
  echo ERREUR : executable introuvable dans %OUT%
  exit /b 1
)

REM Fichiers d'aide à côté de l'exécutable
copy /Y "%ROOT%\scripts\PORTABLE_LISEZMOI.txt" "%OUT%\PORTABLE_LISEZMOI.txt" >nul
copy /Y "%ROOT%\scripts\lancer_SME_Gendarmerie.cmd" "%OUT%\lancer_SME_Gendarmerie.cmd" >nul

echo.
echo Termine.
echo   Executable : %OUT%\SME_Gendarmerie.exe
echo   Lanceur    : %OUT%\lancer_SME_Gendarmerie.cmd
echo.
echo Copiez tout le dossier dist\SME_Gendarmerie sur une cle USB ou un autre PC.
echo Placez smegend.mdb dans le sous-dossier data\ (voir PORTABLE_LISEZMOI.txt).
echo.
pause
exit /b 0
