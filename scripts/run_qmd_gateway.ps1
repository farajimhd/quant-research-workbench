param(
    [string]$Bind = "",
    [string]$CondaEnv = "ml4t",
    [string]$PythonExe = "",
    [string]$TerminalWatch = "AAPL,NVDA,TSLA",
    [double]$TerminalRefreshSeconds = 1.0,
    [int]$TerminalEventLimit = 6,
    [string]$CargoTargetDir = "",
    [string]$RuntimeRoot = "",
    [switch]$CheckOnly,
    [switch]$DebugBuild,
    [switch]$NoTerminal,
    [switch]$TerminalNoScreen
)

$ErrorActionPreference = "Stop"
$env:PYTHONDONTWRITEBYTECODE = "1"
$repoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$manifest = Join-Path $repoRoot "services\qmd-gateway\Cargo.toml"
$serviceDir = Split-Path -Parent $manifest
$targetRoot = if ($CargoTargetDir.Trim()) {
    $CargoTargetDir.Trim()
} elseif ($env:QMD_CARGO_TARGET_DIR) {
    $env:QMD_CARGO_TARGET_DIR.Trim()
} elseif ($env:CARGO_TARGET_DIR) {
    $env:CARGO_TARGET_DIR.Trim()
} else {
    "D:\TradingML\runtimes\qmd_gateway\cargo-target"
}
$targetRoot = [IO.Path]::GetFullPath($targetRoot).TrimEnd('\')
$resolvedRepo = [IO.Path]::GetFullPath($repoRoot).TrimEnd('\')
if ($targetRoot.Equals($resolvedRepo, [StringComparison]::OrdinalIgnoreCase) -or
    $targetRoot.StartsWith($resolvedRepo + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw "QMD Cargo output must be outside the repository: $targetRoot"
}
$env:CARGO_TARGET_DIR = $targetRoot
$targetProfile = if ($DebugBuild) { "debug" } else { "release" }
$gatewayExe = Join-Path $targetRoot "$targetProfile\qmd-gateway.exe"
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
    if ($DebugBuild) {
        cargo check --manifest-path $manifest
    } else {
        cargo check --release --manifest-path $manifest
    }
    exit $LASTEXITCODE
}

if ($NoTerminal) {
    if ($DebugBuild) {
        cargo run --manifest-path $manifest
    } else {
        cargo run --release --manifest-path $manifest
    }
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
$resolvedRuntimeRoot = if ($RuntimeRoot.Trim()) {
    $RuntimeRoot.Trim()
} elseif ($env:QMD_RUNTIME_ROOT) {
    $env:QMD_RUNTIME_ROOT.Trim()
} else {
    "D:\TradingML\runtimes\qmd_gateway"
}
$resolvedRuntimeRoot = [IO.Path]::GetFullPath($resolvedRuntimeRoot).TrimEnd('\')
if ($resolvedRuntimeRoot.Equals($resolvedRepo, [StringComparison]::OrdinalIgnoreCase) -or
    $resolvedRuntimeRoot.StartsWith($resolvedRepo + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw "QMD runtime output must be outside the repository: $resolvedRuntimeRoot"
}
$logRoot = Join-Path $resolvedRuntimeRoot "logs"
New-Item -ItemType Directory -Force -Path $logRoot | Out-Null
$runStamp = Get-Date -Format "yyyyMMdd_HHmmss"
$stdoutLog = Join-Path $logRoot "qmd_gateway_$runStamp.out.log"
$stderrLog = Join-Path $logRoot "qmd_gateway_$runStamp.err.log"
$shutdownToken = [Guid]::NewGuid().ToString("N")
$operatorTokenPath = Join-Path $resolvedRuntimeRoot "operator_token.dpapi"

Write-Host "Building qmd-gateway ($targetProfile)..."
if ($DebugBuild) {
    cargo build --manifest-path $manifest
} else {
    cargo build --release --manifest-path $manifest
}
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
if (-not (Test-Path $gatewayExe)) {
    throw "Built qmd-gateway executable was not found: $gatewayExe"
}
$env:QMD_SHUTDOWN_TOKEN = $shutdownToken
$env:QMD_OPERATOR_TOKEN = $shutdownToken
Add-Type -AssemblyName System.Security
$operatorTokenBytes = [Text.Encoding]::UTF8.GetBytes($shutdownToken)
$protectedOperatorTokenBytes = [Security.Cryptography.ProtectedData]::Protect(
    $operatorTokenBytes,
    $null,
    [Security.Cryptography.DataProtectionScope]::LocalMachine
)
[IO.File]::WriteAllText(
    $operatorTokenPath,
    [Convert]::ToBase64String($protectedOperatorTokenBytes),
    [Text.Encoding]::ASCII
)
$operatorTokenIdentity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
& "$env:SystemRoot\System32\icacls.exe" `
    $operatorTokenPath `
    '/inheritance:r' `
    '/grant:r' `
    "${operatorTokenIdentity}:(R)" | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Failed to restrict the QMD operator token ACL for $operatorTokenIdentity."
}

Write-Host "Starting qmd-gateway at $baseUrl"
Write-Host "Gateway logs:"
Write-Host "  stdout: $stdoutLog"
Write-Host "  stderr: $stderrLog"
$terminalExitCode = 1
$gatewayProcess = $null

try {
    $gatewayProcess = Start-Process `
        -FilePath $gatewayExe `
        -WorkingDirectory $repoRoot `
        -RedirectStandardOutput $stdoutLog `
        -RedirectStandardError $stderrLog `
        -WindowStyle Hidden `
        -PassThru
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
    if ($terminalExitCode -ne 130 -and -not $gatewayProcess.HasExited) {
        Write-Warning (
            "The QMD terminal monitor exited unexpectedly with code $terminalExitCode. " +
            "The healthy gateway will remain supervised; press Ctrl+C or use the managed stop script to shut it down."
        )
        while (-not $gatewayProcess.WaitForExit(1000)) {
            # A presentation failure must not terminate live market-data authority.
            # The bounded wait keeps Ctrl+C responsive and leaves shutdown in finally.
        }
    }
}
finally {
    if ($gatewayProcess -and -not $gatewayProcess.HasExited) {
        Write-Host "Requesting graceful qmd-gateway shutdown for process $($gatewayProcess.Id)..."
        try {
            Invoke-RestMethod `
                -Uri "$baseUrl/admin/shutdown" `
                -Method Post `
                -Headers @{ "X-QMD-Shutdown-Token" = $shutdownToken } `
                -TimeoutSec 3 | Out-Null
            $gatewayProcess.WaitForExit(20000) | Out-Null
        }
        catch {
            Write-Warning "Graceful qmd-gateway shutdown request failed: $($_.Exception.Message)"
        }
    }
    if ($gatewayProcess -and -not $gatewayProcess.HasExited) {
        Write-Warning "qmd-gateway did not drain within 20 seconds; forcing process termination."
        Stop-Process -Id $gatewayProcess.Id -Force -ErrorAction SilentlyContinue
        $gatewayProcess.WaitForExit(5000) | Out-Null
    }
    if ($gatewayProcess -and $gatewayProcess.HasExited -and $gatewayProcess.ExitCode -ne 0) {
        Write-Warning "qmd-gateway exited with code $($gatewayProcess.ExitCode); inspect $stderrLog for a startup or writer-drain failure."
        $terminalExitCode = $gatewayProcess.ExitCode
    }
    Remove-Item Env:QMD_SHUTDOWN_TOKEN -ErrorAction SilentlyContinue
    Remove-Item Env:QMD_OPERATOR_TOKEN -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $operatorTokenPath -Force -ErrorAction SilentlyContinue
}

exit $terminalExitCode
