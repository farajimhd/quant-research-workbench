param(
    [string]$Bind = "",
    [string]$CondaEnv = "ml4t",
    [string]$PythonExe = "",
    [string]$TerminalWatch = "AAPL,NVDA,TSLA",
    [double]$TerminalRefreshSeconds = 1.0,
    [int]$TerminalEventLimit = 6,
    [switch]$CheckOnly,
    [switch]$NoTerminal,
    [switch]$TerminalNoScreen
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$manifest = Join-Path $repoRoot "services\qmd-gateway\Cargo.toml"
$serviceDir = Split-Path -Parent $manifest
$gatewayExe = Join-Path $serviceDir "target\debug\qmd-gateway.exe"
$terminalScript = Join-Path $serviceDir "tools\qmd_terminal.py"

function Import-DotEnvFile {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        return $false
    }
    foreach ($line in Get-Content -LiteralPath $Path) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#") -or -not $trimmed.Contains("=")) {
            continue
        }
        $parts = $trimmed.Split("=", 2)
        $key = $parts[0].Trim()
        if (-not $key -or [Environment]::GetEnvironmentVariable($key, "Process")) {
            continue
        }
        $value = $parts[1].Trim().Trim('"').Trim("'")
        [Environment]::SetEnvironmentVariable($key, $value, "Process")
    }
    return $true
}

$loadedEnvFiles = @()
$repoEnv = Join-Path $repoRoot ".env"
if (Import-DotEnvFile -Path $repoEnv) {
    $loadedEnvFiles += $repoEnv
}
if ($loadedEnvFiles.Count -gt 0) {
    Write-Host ("Loaded .env files: " + ($loadedEnvFiles -join "; "))
}

if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
    throw "cargo was not found. Run scripts\install_rust_windows.ps1, then open a new PowerShell window."
}

if ($Bind.Trim()) {
    $env:QMD_GATEWAY_BIND = $Bind.Trim()
}

if ($CheckOnly) {
    cargo check --manifest-path $manifest
    exit $LASTEXITCODE
}

if ($NoTerminal) {
    cargo run --manifest-path $manifest
    exit $LASTEXITCODE
}

function Resolve-CondaEnvPython {
    param(
        [string]$EnvName
    )

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

function Resolve-QmdTerminalPython {
    if ($PythonExe.Trim()) {
        return $PythonExe.Trim()
    }

    if ($env:CONDA_DEFAULT_ENV -and $env:CONDA_DEFAULT_ENV.Trim().ToLowerInvariant() -eq $CondaEnv.Trim().ToLowerInvariant()) {
        return "python"
    }

    if (Get-Command conda -ErrorAction SilentlyContinue) {
        $envPython = Resolve-CondaEnvPython -EnvName $CondaEnv
        if ($envPython) {
            return $envPython
        }
    }

    if (Get-Command python -ErrorAction SilentlyContinue) {
        return "python"
    }

    throw "python was not found. Activate the $CondaEnv environment first or pass -PythonExe <path-to-python>."
}

function Get-QmdBaseUrl {
    $bindValue = if ($env:QMD_GATEWAY_BIND) { $env:QMD_GATEWAY_BIND.Trim() } else { "127.0.0.1:8795" }
    if ($bindValue.StartsWith("http://") -or $bindValue.StartsWith("https://")) {
        return $bindValue.TrimEnd("/")
    }

    $parts = $bindValue.Split(":")
    $port = $parts[-1]
    $hostPart = ($parts[0..($parts.Length - 2)] -join ":").Trim()
    if (-not $hostPart -or $hostPart -eq "0.0.0.0" -or $hostPart -eq "::" -or $hostPart -eq "[::]") {
        $hostPart = "127.0.0.1"
    }
    return "http://$hostPart`:$port"
}

function Wait-QmdGatewayHealth {
    param(
        [string]$BaseUrl,
        [int]$TimeoutSeconds = 90
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            Invoke-RestMethod -Uri "$BaseUrl/health" -TimeoutSec 2 | Out-Null
            return
        }
        catch {
            Start-Sleep -Milliseconds 500
        }
    }
    throw "qmd-gateway did not respond at $BaseUrl/health within $TimeoutSeconds seconds."
}

$baseUrl = Get-QmdBaseUrl
$python = Resolve-QmdTerminalPython
$logRoot = Join-Path $repoRoot ".tmp\qmd-gateway"
New-Item -ItemType Directory -Force -Path $logRoot | Out-Null
$runStamp = Get-Date -Format "yyyyMMdd_HHmmss"
$stdoutLog = Join-Path $logRoot "qmd_gateway_$runStamp.out.log"
$stderrLog = Join-Path $logRoot "qmd_gateway_$runStamp.err.log"

Write-Host "Building qmd-gateway..."
cargo build --manifest-path $manifest
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
if (-not (Test-Path $gatewayExe)) {
    throw "Built qmd-gateway executable was not found: $gatewayExe"
}

Write-Host "Starting qmd-gateway at $baseUrl"
Write-Host "Gateway logs:"
Write-Host "  stdout: $stdoutLog"
Write-Host "  stderr: $stderrLog"
$terminalExitCode = 1
$gatewayProcess = Start-Process `
    -FilePath $gatewayExe `
    -WorkingDirectory $repoRoot `
    -RedirectStandardOutput $stdoutLog `
    -RedirectStandardError $stderrLog `
    -WindowStyle Hidden `
    -PassThru

try {
    Wait-QmdGatewayHealth -BaseUrl $baseUrl
    Write-Host "qmd-gateway is healthy. Starting Rich terminal monitor..."

    $terminalArgs = @(
        $terminalScript,
        "--base-url", $baseUrl,
        "--watch", $TerminalWatch,
        "--event-limit", "$TerminalEventLimit",
        "--refresh-seconds", "$TerminalRefreshSeconds"
    )
    if ($TerminalNoScreen) {
        $terminalArgs += "--no-screen"
    }

    & $python @terminalArgs
    $terminalExitCode = $LASTEXITCODE
}
finally {
    if ($gatewayProcess -and -not $gatewayProcess.HasExited) {
        Write-Host "Stopping qmd-gateway process $($gatewayProcess.Id)..."
        $gatewayProcess.CloseMainWindow() | Out-Null
        Start-Sleep -Milliseconds 500
    }
    if ($gatewayProcess -and -not $gatewayProcess.HasExited) {
        Stop-Process -Id $gatewayProcess.Id -Force -ErrorAction SilentlyContinue
        $gatewayProcess.WaitForExit(5000) | Out-Null
    }
}

exit $terminalExitCode
