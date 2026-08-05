<#
    Production launcher for the Meeting Summarizer web server (SQLite + embedded
    python topology). Runs uvicorn via the server venv with the repo root as CWD
    so uploads/, transcripts/ and config/ resolve consistently.

    Usage:
        server\start_server.ps1                 # 0.0.0.0:8000
        server\start_server.ps1 -Port 9000
        $env:JWT_SECRET_KEY = "..."; server\start_server.ps1   # production secret
#>
param(
    [int]$Port = $(if ($env:PORT) { [int]$env:PORT } else { 8000 }),
    [string]$Bind = $(if ($env:HOST) { $env:HOST } else { "0.0.0.0" })
)
$ErrorActionPreference = "Stop"

$serverDir = $PSScriptRoot
$root = Split-Path -Parent $serverDir
$venvPy = Join-Path $serverDir ".venv\Scripts\python.exe"

if (-not (Test-Path $venvPy)) {
    throw "Server venv not found: $venvPy`nCreate it: py -m venv server\.venv; server\.venv\Scripts\pip install -r server\requirements.txt"
}

$env:SERVER_MODE = "true"
$env:PORT = "$Port"
$env:HOST = "$Bind"
if (-not $env:JWT_SECRET_KEY) {
    Write-Warning "JWT_SECRET_KEY not set - a persisted random secret (config/.jwt_secret) will be used. Set JWT_SECRET_KEY for production."
}

Set-Location $root
Write-Host "Starting Meeting Summarizer server on http://${Bind}:${Port} (CWD: $root)"
& $venvPy (Join-Path $serverDir "run_server.py")
