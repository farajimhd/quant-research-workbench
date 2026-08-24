param([string]$Bind = "127.0.0.1:8802", [string]$PythonExe = "", [switch]$CheckOnly)

$ErrorActionPreference = "Stop"
$env:PYTHONDONTWRITEBYTECODE = "1"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot
$env:PYTHONDONTWRITEBYTECODE = "1"
$env:MODEL_GATEWAY_BIND = $Bind
$resolvedPython = if ($PythonExe.Trim()) { $PythonExe.Trim() } elseif ($env:CONDA_PREFIX -and (Test-Path -LiteralPath (Join-Path $env:CONDA_PREFIX "python.exe"))) { Join-Path $env:CONDA_PREFIX "python.exe" } elseif (Test-Path -LiteralPath (Join-Path $env:USERPROFILE "miniconda3\envs\ml4t\python.exe")) { Join-Path $env:USERPROFILE "miniconda3\envs\ml4t\python.exe" } else { (Get-Command python -ErrorAction Stop).Source }
if (-not (Test-Path -LiteralPath $resolvedPython -PathType Leaf)) { throw "Python executable does not exist: $resolvedPython" }
if ($CheckOnly) {
    & $resolvedPython -c "from services.model_gateway.config import GatewayConfig; c=GatewayConfig.from_env(); print(c.bind, sorted(c.routes))"
    exit $LASTEXITCODE
}
& $resolvedPython -m services.model_gateway.main
