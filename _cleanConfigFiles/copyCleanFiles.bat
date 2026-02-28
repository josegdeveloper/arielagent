@echo off
setlocal

REM Ruta base = carpeta donde está este .bat
set "SRC=%~dp0"
REM Destino = carpeta padre (un nivel arriba)
set "DST=%SRC%.."

REM Asegura carpeta destino logs y borra todos los archivos (solo archivos) antes de copiar
if not exist "%DST%\logs\" mkdir "%DST%\logs" >nul 2>&1
del /q /f "%DST%\logs\*.*" >nul 2>&1

if not exist "%DST%\logs\" mkdir "%DST%\tmp" >nul 2>&1
del /q /f "%DST%\tmp\*.*" >nul 2>&1

if not exist "%DST%\logs\" mkdir "%DST%\uploads" >nul 2>&1
del /q /f "%DST%\uploads\*.*" >nul 2>&1


REM Lista de carpetas a copiar
for %%D in (logs memory profiles settings) do (
  echo.
  echo Copiando "%%D"...
  if not exist "%DST%\%%D\" (
    mkdir "%DST%\%%D" >nul 2>&1
  )

  REM Copia/substituye archivos y subcarpetas
  robocopy "%SRC%%%D" "%DST%\%%D" /E /COPY:DAT /R:1 /W:1 >nul

  REM Robocopy devuelve codigos >=8 si hay errores
  if errorlevel 8 (
    echo ERROR copiando %%D
    exit /b 1
  ) else (
    echo OK %%D
  )
)

echo.
echo Hecho.
exit /b 0