param(
    [string]$Bind = "",
    [string]$DataRoot = "",
    [string]$CondaEnv = "ml4t",
    [string]$PythonExe = "",
    [switch]$CheckOnly,
    [switch]$LoadModelCheck,
    [switch]$NoBackground,
    [switch]$NoLocalFilesOnly
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

if ($Bind.Trim()) {
    $env:TEXT_EMBED_GATEWAY_BIND = $Bind.Trim()
}
if ($DataRoot.Trim()) {
    $env:TEXT_EMBED_GATEWAY_DATA_ROOT_WIN = $DataRoot.Trim()
}
if ($NoLocalFilesOnly) {
    $env:TEXT_EMBED_LOCAL_FILES_ONLY = "false"
}

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

function Invoke-TextEmbedGatewayPython {
    param([string[]]$ModuleArgs)

    Push-Location $repoRoot
    try {
        if ($PythonExe.Trim()) {
            & $PythonExe -m services.text_embed_gateway.main @ModuleArgs
            exit $LASTEXITCODE
        }

        if ($env:CONDA_DEFAULT_ENV -and $env:CONDA_DEFAULT_ENV.Trim().ToLowerInvariant() -eq $CondaEnv.Trim().ToLowerInvariant()) {
            python -m services.text_embed_gateway.main @ModuleArgs
            exit $LASTEXITCODE
        }

        if (-not (Get-Command conda -ErrorAction SilentlyContinue)) {
            throw "conda was not found. Activate the $CondaEnv environment first or pass -PythonExe <path-to-python>."
        }

        $envPython = Resolve-CondaEnvPython -EnvName $CondaEnv
        if ($envPython) {
            & $envPython -m services.text_embed_gateway.main @ModuleArgs
            exit $LASTEXITCODE
        }

        conda run --no-capture-output -n $CondaEnv python -m services.text_embed_gateway.main @ModuleArgs
        exit $LASTEXITCODE
    }
    finally {
        Pop-Location
    }
}

$argsList = @()
if ($CheckOnly) {
    $argsList += "--check-only"
}
if ($LoadModelCheck) {
    $argsList += "--load-model-check"
}
if ($NoBackground) {
    $argsList += "--no-background"
}
if ($NoLocalFilesOnly) {
    $argsList += "--no-local-files-only"
}

Invoke-TextEmbedGatewayPython -ModuleArgs $argsList
