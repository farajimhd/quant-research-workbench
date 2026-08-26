param(
    [string]$HostName = "127.0.0.1",
    [int]$Port = 8000,
    [string]$PythonExe = "",
    [switch]$NoReload
)

$ErrorActionPreference = "Stop"
$env:PYTHONDONTWRITEBYTECODE = "1"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptDir
Set-Location $RepoRoot

function Resolve-PythonExecutable {
    param([string]$Requested)

    if ($Requested.Trim()) {
        $requestedCommand = Get-Command $Requested.Trim() -ErrorAction SilentlyContinue
        if ($requestedCommand) {
            return $requestedCommand.Source
        }
        if (Test-Path -LiteralPath $Requested.Trim() -PathType Leaf) {
            return (Resolve-Path -LiteralPath $Requested.Trim()).Path
        }
        throw "The requested Python executable does not exist: $Requested"
    }

    if ($env:CONDA_PREFIX) {
        $activeCondaPython = Join-Path $env:CONDA_PREFIX "python.exe"
        if (Test-Path -LiteralPath $activeCondaPython -PathType Leaf) {
            return $activeCondaPython
        }
    }

    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCommand) {
        return $pythonCommand.Source
    }

    $userCandidates = @(
        (Join-Path $env:USERPROFILE "miniconda3\python.exe"),
        (Join-Path $env:USERPROFILE "anaconda3\python.exe")
    )
    foreach ($candidate in $userCandidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return $candidate
        }
    }

    throw "Python was not found. Activate the intended environment or pass -PythonExe <path-to-python.exe>."
}

$resolvedPython = Resolve-PythonExecutable -Requested $PythonExe

$backendArgs = @(
    "-B", (Join-Path $PSScriptRoot "run_backend.py"),
    "--host", $HostName,
    "--port", "$Port"
)

if (-not $NoReload) {
    $backendArgs += "--reload"
}

Write-Host "Starting backend API at http://$HostName`:$Port"
Write-Host "Uvicorn lifespan is enabled for owned background runtimes and graceful shutdown."
& $resolvedPython @backendArgs
