param(
    [ValidateSet("qmd", "synthetic")]
    [string]$Source = "qmd",
    [string]$QmdUrl = "ws://127.0.0.1:8795/stream/compact-events",
    [int]$MaxEvents = 0,
    [int]$EventsPerChunk = 128,
    [int]$ChunkStrideEvents = 1,
    [int]$EncoderBatchSize = 8192,
    [int]$TemporalBatchSize = 4096,
    [int]$EmbeddingDim = 32,
    [double]$TerminalRefreshSeconds = 0.5,
    [string]$CondaEnv = "ml4t",
    [string]$PythonExe = "",
    [switch]$NoRich,
    [switch]$NoScreen,
    [switch]$SmokeTest,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ExtraArgs
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$serviceScript = Join-Path $repoRoot "services\market-ai\run_service.py"

if (-not (Test-Path $serviceScript)) {
    throw "Market AI service launcher was not found: $serviceScript"
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

function Invoke-MarketAIPython {
    param([string[]]$ScriptArgs)

    Push-Location $repoRoot
    try {
        if ($PythonExe.Trim()) {
            & $PythonExe $serviceScript @ScriptArgs
            exit $LASTEXITCODE
        }

        if ($env:CONDA_DEFAULT_ENV -and $env:CONDA_DEFAULT_ENV.Trim().ToLowerInvariant() -eq $CondaEnv.Trim().ToLowerInvariant()) {
            python $serviceScript @ScriptArgs
            exit $LASTEXITCODE
        }

        if (-not (Get-Command conda -ErrorAction SilentlyContinue)) {
            throw "conda was not found. Activate the $CondaEnv environment first or pass -PythonExe <path-to-python>."
        }

        $envPython = Resolve-CondaEnvPython -EnvName $CondaEnv
        if ($envPython) {
            & $envPython $serviceScript @ScriptArgs
            exit $LASTEXITCODE
        }

        conda run --no-capture-output -n $CondaEnv python $serviceScript @ScriptArgs
        exit $LASTEXITCODE
    }
    finally {
        Pop-Location
    }
}

$argsList = @(
    "--source", $Source,
    "--qmd-url", $QmdUrl,
    "--events-per-chunk", [string]$EventsPerChunk,
    "--chunk-stride-events", [string]$ChunkStrideEvents,
    "--encoder-batch-size", [string]$EncoderBatchSize,
    "--temporal-batch-size", [string]$TemporalBatchSize,
    "--embedding-dim", [string]$EmbeddingDim,
    "--terminal-refresh-seconds", [string]$TerminalRefreshSeconds
)

if ($MaxEvents -gt 0) {
    $argsList += @("--max-events", [string]$MaxEvents)
}
if ($NoRich) {
    $argsList += "--no-rich"
}
if ($NoScreen) {
    $argsList += "--no-screen"
}
if ($SmokeTest) {
    $argsList = @(
        "--source", "synthetic",
        "--max-events", "1000",
        "--events-per-chunk", "16",
        "--encoder-batch-size", "32",
        "--temporal-batch-size", "16",
        "--older-context-embeddings", "0",
        "--recent-context-embeddings", "2",
        "--no-rich"
    )
}
if ($ExtraArgs) {
    $argsList += $ExtraArgs
}

Write-Host "Starting Market AI service..."
Write-Host "Repo: $repoRoot"
Write-Host "Script: $serviceScript"
Write-Host "Args: $($argsList -join ' ')"

Invoke-MarketAIPython -ScriptArgs $argsList
