param([string]$Bind = "127.0.0.1:8803", [string]$PythonExe = "", [switch]$CheckOnly)

$ErrorActionPreference = "Stop"
$env:PYTHONDONTWRITEBYTECODE = "1"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot
$resolvedPython = if ($PythonExe.Trim()) { $PythonExe.Trim() } elseif ($env:CONDA_PREFIX -and (Test-Path -LiteralPath (Join-Path $env:CONDA_PREFIX "python.exe"))) { Join-Path $env:CONDA_PREFIX "python.exe" } elseif (Test-Path -LiteralPath (Join-Path $env:USERPROFILE "miniconda3\python.exe")) { Join-Path $env:USERPROFILE "miniconda3\python.exe" } else { (Get-Command python -ErrorAction Stop).Source }
if (-not (Test-Path -LiteralPath $resolvedPython -PathType Leaf)) { throw "Python executable does not exist: $resolvedPython" }
$env:NEWS_HYPOTHESIS_BIND = $Bind
if ($CheckOnly) {
    & $resolvedPython -B -c "import sys; sys.path.insert(0, r'services\news-hypothesis\src'); from news_hypothesis.contextual import HYPOTHESIS_SCHEMA; print('$Bind', len(HYPOTHESIS_SCHEMA['required']))"
    exit $LASTEXITCODE
}
& $resolvedPython -B services\news-hypothesis\run_service.py
