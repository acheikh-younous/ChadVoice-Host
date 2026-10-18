@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (echo Lancez INSTALLER_WINDOWS.bat auparavant & pause & exit /b 1)
if exist "EMAIL_SETTINGS.bat" call "EMAIL_SETTINGS.bat"
echo Ouvrez http://127.0.0.1:8000 dans votre navigateur.
echo NE FERMEZ PAS CETTE FENETRE pendant votre session de travail.
.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8000
pause
