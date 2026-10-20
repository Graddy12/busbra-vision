$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonPath = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw "Environnement virtuel introuvable : $PythonPath"
}

$env:KERAS_BACKEND = "torch"
$env:FLASK_HOST = "127.0.0.1"
$env:FLASK_PORT = "5001"
Set-Location -LiteralPath $ProjectRoot
Write-Host "Chargement de BUSBRA Vision. Attendez le message de demarrage du serveur."
& $PythonPath app.py --port 5001 --open-browser
if ($LASTEXITCODE -ne 0) {
    throw "Le serveur s'est arrete. Consultez le message d'erreur ci-dessus."
}
