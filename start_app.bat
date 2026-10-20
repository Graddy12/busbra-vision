@echo off
setlocal
cd /d "%~dp0"

set "KERAS_BACKEND=torch"
set "FLASK_HOST=127.0.0.1"
set "FLASK_PORT=5001"
set "APP_PYTHON=%CD%\.venv\Scripts\python.exe"

if not exist "%APP_PYTHON%" (
  echo [ERREUR] Environnement virtuel introuvable : %APP_PYTHON%
  echo Creez-le puis installez les dependances avec :
  echo   python -m venv .venv
  echo   .venv\Scripts\python.exe -m pip install -r requirements.txt
  pause
  exit /b 1
)

echo Chargement de BUSBRA Vision. Attendez le message de demarrage du serveur.
echo Laissez cette fenetre ouverte pendant l'utilisation.
"%APP_PYTHON%" app.py --port 5001 --open-browser

if errorlevel 1 (
  echo.
  echo [ERREUR] Le serveur s'est arrete. Consultez le message ci-dessus.
  pause
)
