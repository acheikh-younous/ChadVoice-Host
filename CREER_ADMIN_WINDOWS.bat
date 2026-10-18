@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (echo Lancez INSTALLER_WINDOWS.bat auparavant & pause & exit /b 1)
.venv\Scripts\python.exe manage.py init
pause
