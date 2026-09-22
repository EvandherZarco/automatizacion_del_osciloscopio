@echo off
rem Abre el sistema de medicion con el Python del venv; si termina con error, deja la ventana abierta.
cd /d "%~dp0"
"venv\Scripts\python.exe" main.py
if errorlevel 1 pause
