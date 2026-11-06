@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0.."
set "ROOT=%CD%"
set "DEST=%ROOT%\modeles"
if not exist "%DEST%" mkdir "%DEST%"

echo Migration des gabarits vers %DEST%
echo.

set "REPO=%ROOT%\.."
for %%F in (
  resultat.xlsx
  numerique.xlsx
  numérique.xlsx
  DETACHES.xlsx
  limite_age.docx
  soit_transmis.docx
  smegend.mdb
  ordre_general.txt
) do (
  if exist "%REPO%\%%F" (
    copy /Y "%REPO%\%%F" "%DEST%\%%F" >nul && echo [OK] %%F ^<- depot Impression
  )
  if exist "%ROOT%\data\templates\%%F" (
    copy /Y "%ROOT%\data\templates\%%F" "%DEST%\%%F" >nul && echo [OK] %%F ^<- data\templates
  )
  if exist "%ROOT%\data\%%F" (
    copy /Y "%ROOT%\data\%%F" "%DEST%\%%F" >nul && echo [OK] %%F ^<- data
  )
)

echo.
echo Termine. Verifiez %DEST%
pause
