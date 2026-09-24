# =============================================================================
# File Overview
# =============================================================================
# This is the operator-facing start command for the repository-managed
# ClickHouse WSL deployment. It delegates to the established disk attachment,
# UUID validation, mount, overlay, and server bootstrap implementation.
#
# Use this explicit name instead of guessing whether a generic ClickHouse start
# command understands the external HDD and SSD mount requirements.
# =============================================================================

# Leave parameters unbound so @args forwards named arguments to the implementation.
# Binding ValueFromRemainingArguments here consumes them before @args can forward them.

$ErrorActionPreference = "Stop"
$ImplementationScript = Join-Path $PSScriptRoot "start_clickhouse_on_mounted_disk_wsl.ps1"

Write-Host "Starting repository-managed ClickHouse with validated external disks."
& $ImplementationScript @args
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
