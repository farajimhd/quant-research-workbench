param(
    [string]$Bind = "",
    [string]$CondaEnv = "ml4t",
    [string]$PythonExe = "",
    [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"
$env:PYTHONDONTWRITEBYTECODE = "1"
$repoRoot = Split-Path -Parent $PSScriptRoot
$serviceDir = Join-Path $repoRoot "services\text-intelligence"
$env:PYTHONPATH = if ($env:PYTHONPATH) { "$repoRoot;$env:PYTHONPATH" } else { $repoRoot }

function Resolve-PythonExecutable {
    param(
        [string]$Requested,
        [string]$EnvironmentName
    )

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

    if ($env:CONDA_PREFIX -and
        (Split-Path -Leaf $env:CONDA_PREFIX).Trim().ToLowerInvariant() -eq $EnvironmentName.Trim().ToLowerInvariant()) {
        $activeCondaPython = Join-Path $env:CONDA_PREFIX "python.exe"
        if (Test-Path -LiteralPath $activeCondaPython -PathType Leaf) {
            return $activeCondaPython
        }
    }

    foreach ($candidate in @(
        (Join-Path $env:USERPROFILE "miniconda3\envs\$EnvironmentName\python.exe"),
        (Join-Path $env:USERPROFILE "anaconda3\envs\$EnvironmentName\python.exe")
    )) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return $candidate
        }
    }

    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCommand) {
        return $pythonCommand.Source
    }
    throw "Python was not found. Activate the $EnvironmentName environment or pass -PythonExe <path-to-python.exe>."
}

$resolvedPython = Resolve-PythonExecutable -Requested $PythonExe -EnvironmentName $CondaEnv

if ($Bind.Trim()) {
    $env:TEXT_INTELLIGENCE_BIND = $Bind.Trim()
}

if ($CheckOnly) {
    & $resolvedPython -c "import ast,pathlib; root=pathlib.Path(r'$serviceDir'); files=list((root/'text_intelligence').glob('*.py'))+list((root/'scripts').glob('*.py')); [ast.parse(p.read_text(encoding='utf-8')) for p in files]; print(f'AST OK {len(files)} files')"
    exit $LASTEXITCODE
}

Push-Location $serviceDir
try {
    & $resolvedPython -m text_intelligence.main
}
finally {
    Pop-Location
}
