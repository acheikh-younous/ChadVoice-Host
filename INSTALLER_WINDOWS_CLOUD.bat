@echo off
cd /d "%~dp0"
python -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
if errorlevel 1 (echo ERREUR INSTALLATION & pause & exit /b 1)
echo Installation OK.
pause
