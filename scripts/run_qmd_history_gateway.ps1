param(
    [switch]$BuildOnly
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$manifest = Join-Path $repoRoot "services\qmd_history_gateway\Cargo.toml"

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
    param($Endpoint)

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

    Write-Host "qmd-history-gateway is already running and ready at $($Endpoint.BaseUrl)."
    Write-Host "No second process was started. Stop the existing gateway first only when a restart is required."
    return $true
}

if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
    throw "Cargo is required. Run scripts\install_rust_windows.ps1 first."
}

$endpoint = Resolve-HistoryEndpoint -Bind (Resolve-HistoryBind)

if (-not $BuildOnly -and (Test-ExistingHistoryGateway -Endpoint $endpoint)) {
    return
}

Push-Location $repoRoot
try {
    Write-Host "Building qmd-history-gateway from shared qmd_core..."
    cargo build --offline --manifest-path $manifest
    if ($LASTEXITCODE -ne 0) {
        throw "qmd-history-gateway build failed with exit code $LASTEXITCODE"
    }
    if ($BuildOnly) {
        return
    }
    if (Test-ExistingHistoryGateway -Endpoint $endpoint) {
        return
    }
    Write-Host "Starting qmd-history-gateway at $($endpoint.BaseUrl)..."
    cargo run --offline --manifest-path $manifest
    if ($LASTEXITCODE -ne 0) {
        throw "qmd-history-gateway exited with code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}
