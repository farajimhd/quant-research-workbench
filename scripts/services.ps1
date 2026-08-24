[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Arguments
)

$ErrorActionPreference = "Stop"
$env:PYTHONDONTWRITEBYTECODE = "1"
$repoRoot = Split-Path -Parent $PSScriptRoot
$python = if ($env:CONDA_PREFIX -and (Test-Path -LiteralPath (Join-Path $env:CONDA_PREFIX "python.exe"))) {
    Join-Path $env:CONDA_PREFIX "python.exe"
}
elseif (Test-Path -LiteralPath (Join-Path $env:USERPROFILE "miniconda3\envs\ml4t\python.exe")) {
    Join-Path $env:USERPROFILE "miniconda3\envs\ml4t\python.exe"
}
elseif (Test-Path -LiteralPath (Join-Path $env:USERPROFILE "miniconda3\python.exe")) {
    Join-Path $env:USERPROFILE "miniconda3\python.exe"
}
else {
    (Get-Command python -ErrorAction Stop).Source
}

& $python -B (Join-Path $PSScriptRoot "service_manager.py") @Arguments
exit $LASTEXITCODE
