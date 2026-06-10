@echo off
rem Instala las dependencias de AnyMendo (ejecutar una sola vez por equipo).
set PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe
if not exist "%PY%" set PY=python
"%PY%" -m pip install -r "%~dp0requirements.txt"
echo.
echo Listo. Ejecuta AnyMendo.bat para abrir la aplicacion.
pause
