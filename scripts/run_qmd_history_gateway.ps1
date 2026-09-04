param(
    [switch]$BuildOnly,
    [switch]$NoBuild,
    [string]$BinaryPath = "",
    [string]$CargoTargetDir = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$manifest = Join-Path $repoRoot "services\qmd_history_gateway\Cargo.toml"

function Resolve-CargoTargetDirectory {
    param([string]$Requested)

    $candidate = if ($Requested.Trim()) {
        $Requested.Trim()
    }
    elseif ($env:QMD_HISTORY_CARGO_TARGET_DIR) {
        $env:QMD_HISTORY_CARGO_TARGET_DIR.Trim()
    }
    else {
        "D:\TradingML\runtimes\qmd_history_gateway\cargo-target"
    }
    $resolved = [IO.Path]::GetFullPath($candidate).TrimEnd('\')
    $resolvedRepo = [IO.Path]::GetFullPath($repoRoot).TrimEnd('\')
    if ($resolved.Equals($resolvedRepo, [StringComparison]::OrdinalIgnoreCase) -or
        $resolved.StartsWith($resolvedRepo + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "QMD History Cargo output must be outside the repository: $resolved"
    }
    return $resolved
}

function Resolve-HistoryBind {
    if ($env:QMD_HISTORY_BIND) {
        return $env:QMD_HISTORY_BIND.Trim()
    }

    $envFiles = @()
    if ($env:DOTENV_PATHS) {
        $envFiles += $env:DOTENV_PATHS -split [IO.Path]::PathSeparator
    }
    $envFiles += Join-Path $repoRoot ".env"
    foreach ($envFile in ($envFiles | Select-Object -Unique)) {
        if (-not $envFile -or -not (Test-Path -LiteralPath $envFile)) {
            continue
        }
        foreach ($line in Get-Content -LiteralPath $envFile) {
            if ($line -match '^\s*QMD_HISTORY_BIND\s*=\s*(.+?)\s*$') {
                return $Matches[1].Trim().Trim('"').Trim("'")
            }
        }
    }
    return "127.0.0.1:8801"
}

function Resolve-HistoryEndpoint {
    param([string]$Bind)

    $raw = $Bind.Trim()
    $separator = $raw.LastIndexOf(':')
    if ($separator -lt 1) {
        throw "QMD_HISTORY_BIND must include a host and port, received '$raw'."
    }
    $hostName = $raw.Substring(0, $separator).Trim().TrimStart('[').TrimEnd(']')
    $port = 0
    if (-not [int]::TryParse($raw.Substring($separator + 1), [ref]$port)) {
        throw "QMD_HISTORY_BIND has an invalid port: '$raw'."
    }
    $connectHost = if ($hostName -in @("0.0.0.0", "::", "[::]")) { "127.0.0.1" } else { $hostName }
    $urlHost = if ($connectHost.Contains(':')) { "[$connectHost]" } else { $connectHost }
    return [pscustomobject]@{ BaseUrl = "http://$urlHost`:$port"; Host = $connectHost; Port = $port }
}

function Test-HistoryPortOpen {
    param([string]$HostName, [int]$Port)

    $client = [Net.Sockets.TcpClient]::new()
    try {
        $connection = $client.ConnectAsync($HostName, $Port)
        return $connection.Wait(800) -and $client.Connected
    }
    catch {
        return $false
    }
    finally {
        $client.Dispose()
    }
}

function Test-ExistingHistoryGateway {
    param(
        $Endpoint,
        [string]$ExpectedCalculationRevision,
        [string]$ExpectedCheckpointTable,
        [string]$ExpectedCheckpointSet
    )

    $health = $null
    try {
        $health = Invoke-RestMethod -Uri "$($Endpoint.BaseUrl)/health" -TimeoutSec 3
    }
    catch {
        if (Test-HistoryPortOpen -HostName $Endpoint.Host -Port $Endpoint.Port) {
            throw "Address $($Endpoint.BaseUrl) is already in use, but /health is not a ready QMD History gateway. Stop the process using port $($Endpoint.Port) or set QMD_HISTORY_BIND to another address."
        }
        return $false
    }

    if ($health.service -ne "qmd_history_gateway" -or $health.host_role -ne "historical") {
        throw "Address $($Endpoint.BaseUrl) is already used by another HTTP service. Expected service=qmd_history_gateway and host_role=historical."
    }
    if ($health.status -ne "ready" -or $health.running -ne $true) {
        throw "QMD History is already bound at $($Endpoint.BaseUrl), but it is not ready (status=$($health.status)). Inspect that process instead of starting a duplicate."
    }
    if ($health.calculation_revision -ne $ExpectedCalculationRevision -or
        $health.structure_algorithm_version -ne 16 -or
        $health.config.structure_daily_checkpoint_table -ne $ExpectedCheckpointTable -or
        $health.config.structure_checkpoint_set_id -ne $ExpectedCheckpointSet) {
        throw (
            "QMD History is running with incompatible structural authority: " +
            "calculation_revision=$($health.calculation_revision), " +
            "algorithm=$($health.structure_algorithm_version), " +
            "table=$($health.config.structure_daily_checkpoint_table), " +
            "set=$($health.config.structure_checkpoint_set_id). Expected calculation_revision=" +
            "$ExpectedCalculationRevision, algorithm=16, " +
            "table=$ExpectedCheckpointTable, set=$ExpectedCheckpointSet. Stop and restart this service."
        )
    }

    Write-Host "qmd-history-gateway is already running and ready at $($Endpoint.BaseUrl)."
    Write-Host "No second process was started. Stop the existing gateway first only when a restart is required."
    return $true
}

if ($BuildOnly -and $NoBuild) {
    throw "-BuildOnly and -NoBuild cannot be combined."
}
if (-not $NoBuild -and -not (Get-Command cargo -ErrorAction SilentlyContinue)) {
    throw "Cargo is required. Run scripts\install_rust_windows.ps1 first."
}

$endpoint = Resolve-HistoryEndpoint -Bind (Resolve-HistoryBind)
$resolvedCargoTargetDir = Resolve-CargoTargetDirectory -Requested $CargoTargetDir
$expectedCheckpointTable = if ($env:QMD_HISTORY_STRUCTURE_DAILY_CHECKPOINT_TABLE) {
    $env:QMD_HISTORY_STRUCTURE_DAILY_CHECKPOINT_TABLE.Trim()
} else {
    "qmd_structure_daily_checkpoint_v2"
}
$expectedCheckpointSet = if ($env:QMD_STRUCTURE_CHECKPOINT_SET_ID) {
    $env:QMD_STRUCTURE_CHECKPOINT_SET_ID.Trim()
} else {
    "strategy-dev-sugp-juns-20260814-20260821-v16-clock-v2"
}
$expectedCalculationRevision = "qmd-derived-v57"

if (-not $BuildOnly -and (Test-ExistingHistoryGateway -Endpoint $endpoint -ExpectedCalculationRevision $expectedCalculationRevision -ExpectedCheckpointTable $expectedCheckpointTable -ExpectedCheckpointSet $expectedCheckpointSet)) {
    return
}

Push-Location $repoRoot
try {
    if (-not $NoBuild) {
        Write-Host "Building qmd-history-gateway from shared qmd_core..."
        Write-Host "Cargo target: $resolvedCargoTargetDir"
        cargo build --offline --locked --release --manifest-path $manifest --target-dir $resolvedCargoTargetDir
        if ($LASTEXITCODE -ne 0) {
            throw "qmd-history-gateway build failed with exit code $LASTEXITCODE"
        }
    }
    if ($BuildOnly) {
        return
    }
    if (Test-ExistingHistoryGateway -Endpoint $endpoint -ExpectedCalculationRevision $expectedCalculationRevision -ExpectedCheckpointTable $expectedCheckpointTable -ExpectedCheckpointSet $expectedCheckpointSet) {
        return
    }
    $gatewayExecutable = if ($BinaryPath.Trim()) {
        [IO.Path]::GetFullPath($BinaryPath.Trim())
    } else {
        Join-Path $resolvedCargoTargetDir "release\qmd-history-gateway.exe"
    }
    if (-not (Test-Path -LiteralPath $gatewayExecutable -PathType Leaf)) {
        throw "QMD History build succeeded but the executable is missing: $gatewayExecutable"
    }
    Write-Host "Starting qmd-history-gateway at $($endpoint.BaseUrl)..."
    & $gatewayExecutable
    if ($LASTEXITCODE -ne 0) {
        throw "qmd-history-gateway exited with code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}
