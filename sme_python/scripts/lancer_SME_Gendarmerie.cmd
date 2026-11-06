@echo off
chcp 65001 >nul
REM Lance SME_Gendarmerie.exe depuis ce dossier (version portable).
cd /d "%~dp0"
if exist "_internal\PySide6\plugins" (
  set "QT_PLUGIN_PATH=%CD%\_internal\PySide6\plugins"
  set "QT_QPA_PLATFORM_PLUGIN_PATH=%CD%\_internal\PySide6\plugins\platforms"
)
if exist "SME_Gendarmerie.exe" (
  start "" "SME_Gendarmerie.exe"
) else (
  echo Fichier SME_Gendarmerie.exe introuvable dans ce dossier.
  pause
  exit /b 1
)
