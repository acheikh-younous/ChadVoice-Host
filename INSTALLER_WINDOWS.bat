@echo off
cd /d "%~dp0"
where py >nul 2>&1
if %errorlevel%==0 (py -3 -m venv .venv) else (python -m venv .venv)
if errorlevel 1 (echo ERREUR : installez Python 3.10+ depuis python.org & pause & exit /b 1)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
if errorlevel 1 (echo ERREUR : installation inachevee & pause & exit /b 1)
echo.
echo Installe. ETAPE SUIVANTE : double-cliquez sur CREER_ADMIN_WINDOWS.bat
pause
