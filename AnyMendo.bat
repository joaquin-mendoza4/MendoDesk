@echo off
rem Abre AnyMendo (sin ventana de consola).
set PYW=%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe
if not exist "%PYW%" set PYW=pythonw
start "" "%PYW%" "%~dp0host.py"
