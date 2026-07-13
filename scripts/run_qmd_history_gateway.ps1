param(
    [switch]$BuildOnly
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$manifest = Join-Path $repoRoot "services\qmd_history_gateway\Cargo.toml"

if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
    throw "Cargo is required. Run scripts\install_rust_windows.ps1 first."
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
    Write-Host "Starting qmd-history-gateway (default http://127.0.0.1:8801)..."
    cargo run --offline --manifest-path $manifest
    if ($LASTEXITCODE -ne 0) {
        throw "qmd-history-gateway exited with code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}
