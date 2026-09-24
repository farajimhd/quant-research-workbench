# =============================================================================
# File Overview
# =============================================================================
# This is the operator-facing stop command for the repository-managed
# ClickHouse WSL deployment. It delegates to the graceful server shutdown and
# WSL shutdown implementation after preventing systemd from restarting the
# database process.
# =============================================================================

# Leave parameters unbound so @args forwards named arguments to the implementation.
# Binding ValueFromRemainingArguments here consumes them before @args can forward them.

$ErrorActionPreference = "Stop"
$ImplementationScript = Join-Path $PSScriptRoot "stop_clickhouse_wsl.ps1"

Write-Host "Stopping repository-managed ClickHouse and its WSL instance."
& $ImplementationScript @args
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
