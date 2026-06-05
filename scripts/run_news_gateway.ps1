param(
    [string]$Bind = "",
    [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$manifest = Join-Path $repoRoot "services\news-gateway\Cargo.toml"

if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
    throw "cargo was not found. Run scripts\install_rust_windows.ps1, then open a new PowerShell window."
}

if ($Bind.Trim()) {
    $env:NEWS_GATEWAY_BIND = $Bind.Trim()
}

if ($CheckOnly) {
    cargo check --manifest-path $manifest
    exit $LASTEXITCODE
}

cargo run --manifest-path $manifest
