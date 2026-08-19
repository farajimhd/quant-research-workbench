param(
    [string]$Bind = "127.0.0.1:8805",
    [string]$V2Checkpoint = "",
    [string]$V3Checkpoint = "",
    [string]$Device = "auto",
    [string]$PythonExe = "",
    [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"
$env:PYTHONDONTWRITEBYTECODE = "1"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot
$resolvedPython = if ($PythonExe.Trim()) { $PythonExe.Trim() } elseif ($env:CONDA_PREFIX -and (Test-Path -LiteralPath (Join-Path $env:CONDA_PREFIX "python.exe"))) { Join-Path $env:CONDA_PREFIX "python.exe" } elseif (Test-Path -LiteralPath (Join-Path $env:USERPROFILE "miniconda3\python.exe")) { Join-Path $env:USERPROFILE "miniconda3\python.exe" } else { (Get-Command python -ErrorAction Stop).Source }
if (-not (Test-Path -LiteralPath $resolvedPython -PathType Leaf)) { throw "Python executable does not exist: $resolvedPython" }
$env:BAR_GPT_BIND = $Bind
$env:BAR_GPT_DEVICE = $Device
if ($V2Checkpoint.Trim()) { $env:BAR_GPT_V2_CHECKPOINT = [IO.Path]::GetFullPath($V2Checkpoint) }
if ($V3Checkpoint.Trim()) { $env:BAR_GPT_V3_CHECKPOINT = [IO.Path]::GetFullPath($V3Checkpoint) }
if ($CheckOnly) {
    & $resolvedPython -B -c "import sys; sys.path.insert(0, r'services\bar-gpt\src'); from bar_gpt_service.config import ServiceConfig; c=ServiceConfig.from_env(); print(c.bind, c.device, len(c.releases), c.runtime_root)"
    exit $LASTEXITCODE
}
& $resolvedPython -B services\bar-gpt\run_service.py
