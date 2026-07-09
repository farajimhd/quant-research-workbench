param(
    [string]$HostName = "127.0.0.1",
    [int]$Port = 8000,
    [switch]$NoReload
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptDir
Set-Location $RepoRoot

$uvicornArgs = @(
    "-m", "uvicorn",
    "src.backend.app:app",
    "--host", $HostName,
    "--port", "$Port",
    "--lifespan", "off"
)

if (-not $NoReload) {
    $uvicornArgs += @("--reload", "--reload-dir", "src")
}

Write-Host "Starting backend API at http://$HostName`:$Port"
Write-Host "Uvicorn lifespan is disabled for this backend because src.backend.app has no startup/shutdown lifespan work."
python @uvicornArgs
