param(
    [string]$Services = "qmd,news,sec",
    [string]$CondaEnv = "ml4t",
    [string]$PythonExe = "",
    [switch]$Execute,
    [switch]$AutoRun
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

function Resolve-CondaEnvPython {
    param([string]$EnvName)
    try {
        $infoText = conda info --envs --json
        $info = $infoText | ConvertFrom-Json
        foreach ($envPath in $info.envs) {
            $leaf = Split-Path -Leaf $envPath
            if ($leaf.Trim().ToLowerInvariant() -eq $EnvName.Trim().ToLowerInvariant()) {
                $candidate = Join-Path $envPath "python.exe"
                if (Test-Path $candidate) {
                    return $candidate
                }
            }
        }
    }
    catch {
        return ""
    }
    return ""
}

$moduleArgs = @("-m", "services.maintenance.runner", "--services", $Services)
if ($Execute) {
    $moduleArgs += "--execute"
}
if ($AutoRun) {
    $moduleArgs += "--auto-run"
}

Push-Location $repoRoot
try {
    if ($PythonExe.Trim()) {
        & $PythonExe @moduleArgs
        exit $LASTEXITCODE
    }

    if ($env:CONDA_DEFAULT_ENV -and $env:CONDA_DEFAULT_ENV.Trim().ToLowerInvariant() -eq $CondaEnv.Trim().ToLowerInvariant()) {
        python @moduleArgs
        exit $LASTEXITCODE
    }

    if (-not (Get-Command conda -ErrorAction SilentlyContinue)) {
        throw "conda was not found. Activate $CondaEnv first or pass -PythonExe <path-to-python>."
    }

    $envPython = Resolve-CondaEnvPython -EnvName $CondaEnv
    if ($envPython) {
        & $envPython @moduleArgs
        exit $LASTEXITCODE
    }

    conda run --no-capture-output -n $CondaEnv python @moduleArgs
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
