param(
    [string]$Distro = "Ubuntu",
    [ValidateRange(1, 3600)]
    [int]$GracefulStopTimeoutSeconds = 90
)

$ErrorActionPreference = "Stop"

Write-Host "==== Step 1: Gracefully stop ClickHouse ===="

$StopScript = @'
CLICKHOUSE_PROCESS_PATTERN="clickhouse-watchdog|ClickHouseWatchdog|clickhouse-server|clickhouse server"

# The repository startup requires external disks that are unavailable during
# systemd's early WSL boot. Disable the package unit before signalling the
# managed process so Restart=always cannot launch a competing server.
if [[ -d /run/systemd/system ]] && command -v systemctl >/dev/null; then
    echo "Disabling the competing ClickHouse systemd unit..."
    systemctl disable clickhouse-server.service >/dev/null 2>&1 || true
    systemctl stop clickhouse-server.service >/dev/null 2>&1 || true
    systemctl reset-failed clickhouse-server.service >/dev/null 2>&1 || true
fi

if pgrep -u clickhouse -f "$CLICKHOUSE_PROCESS_PATTERN" >/dev/null; then
    echo "Stopping ClickHouse..."
    pkill -TERM -u clickhouse -f "$CLICKHOUSE_PROCESS_PATTERN"

    # Wait until it fully exits
    for i in $(seq 1 "$GRACEFUL_STOP_TIMEOUT_SECONDS"); do
        if ! pgrep -u clickhouse -f "$CLICKHOUSE_PROCESS_PATTERN" >/dev/null; then
            echo "ClickHouse stopped."
            exit 0
        fi
        sleep 1
    done

    echo "ClickHouse did not stop cleanly in time."
    pgrep -a -u clickhouse -f "$CLICKHOUSE_PROCESS_PATTERN" || true
    exit 1
else
    echo "ClickHouse not running."
fi
'@

$NormalizedStopScript = ($StopScript -replace "`r`n?", "`n") + "`n"
$EncodedStopScript = [Convert]::ToBase64String(
    [System.Text.Encoding]::UTF8.GetBytes($NormalizedStopScript)
)
wsl -d $Distro -u root -- env "GRACEFUL_STOP_TIMEOUT_SECONDS=$GracefulStopTimeoutSeconds" bash -lc "printf '%s' '$EncodedStopScript' | base64 -d | bash"
if ($LASTEXITCODE -ne 0) {
    throw "ClickHouse stop command failed in WSL distro '$Distro' with exit code $LASTEXITCODE."
}

Write-Host "==== Step 2: Shutdown WSL ===="
wsl --shutdown

Write-Host "==== Done ===="
