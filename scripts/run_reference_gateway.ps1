param(
    [string]$PythonExe = "python",
    [ValidateSet("Custom", "Prod", "Temp")]
    [string]$Mode = "Custom",
    [string]$ReadDatabase = "",
    [string]$WriteDatabase = "",
    [string]$TestWriteDatabase = "",
    [switch]$Execute,
    [switch]$PrintRules,
    [switch]$ActiveTickerCheck,
    [switch]$EnsureMarketPublicationSchema,
    [switch]$MarketHoursWriteOverride,
    [string]$MarketHoursWriteReason = "",
    [switch]$NoWriteDiscoveredIssues,
    [switch]$NoWriteCanonicalGraph,
    [switch]$NoResolveStaleIssues,
    [switch]$NoRebuildTradable,
    [switch]$RebuildTradableInTestMode,
    [switch]$NoMarketPublicationGapFill,
    [switch]$NoPreflight,
    [switch]$NoIbkrResolution,
    [switch]$NoIbkrRequired,
    [switch]$NoImmediateTradabilityBlock,
    [switch]$Daemon,
    [switch]$NoDaemon
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

if ($Mode -eq "Prod") {
    if ($TestWriteDatabase) {
        throw "-Mode Prod cannot be combined with -TestWriteDatabase."
    }
    if (-not $ReadDatabase) {
        $ReadDatabase = "q_live"
    }
    if (-not $WriteDatabase) {
        $WriteDatabase = "q_live"
    }
    $Execute = $true
    $ActiveTickerCheck = $true
    $Daemon = $true
}
elseif ($Mode -eq "Temp") {
    if ($WriteDatabase) {
        throw "-Mode Temp writes through -TestWriteDatabase; do not pass -WriteDatabase."
    }
    if (-not $ReadDatabase) {
        $ReadDatabase = "q_live"
    }
    if (-not $TestWriteDatabase) {
        $TestWriteDatabase = "q_reference_tmp"
    }
    if (-not $MarketHoursWriteReason.Trim()) {
        $MarketHoursWriteReason = "reference gateway temp mode"
    }
    $Execute = $true
    $ActiveTickerCheck = $true
    $EnsureMarketPublicationSchema = $true
    $MarketHoursWriteOverride = $true
}
if ($NoDaemon) {
    $Daemon = $false
}

$argsList = @("-m", "services.reference_gateway.main")
if ($ReadDatabase) {
    $argsList += @("--read-database", $ReadDatabase)
}
if ($WriteDatabase) {
    $argsList += @("--write-database", $WriteDatabase)
}
if ($TestWriteDatabase) {
    $argsList += @("--test-write-database", $TestWriteDatabase)
}
if ($Execute) {
    $argsList += "--execute"
}
if ($MarketHoursWriteOverride) {
    $argsList += "--market-hours-write-override"
    if (-not $MarketHoursWriteReason.Trim()) {
        throw "-MarketHoursWriteReason is required when -MarketHoursWriteOverride is set."
    }
}
if ($MarketHoursWriteReason.Trim()) {
    $argsList += @("--market-hours-write-reason", $MarketHoursWriteReason)
}
if ($PrintRules) {
    $argsList += "--print-rules"
}
if ($ActiveTickerCheck) {
    $argsList += "--active-ticker-check"
}
if ($EnsureMarketPublicationSchema) {
    $argsList += "--ensure-market-publication-schema"
}
if ($NoWriteDiscoveredIssues) {
    $argsList += "--no-write-discovered-issues"
}
if ($NoWriteCanonicalGraph) {
    $argsList += "--no-write-canonical-graph"
}
if ($NoResolveStaleIssues) {
    $argsList += "--no-resolve-stale-issues"
}
if ($NoRebuildTradable) {
    $argsList += "--no-rebuild-tradable"
}
if ($RebuildTradableInTestMode) {
    $argsList += "--rebuild-tradable-in-test-mode"
}
if ($NoMarketPublicationGapFill) {
    $argsList += "--no-market-publication-gap-fill"
}
if ($NoPreflight) {
    $argsList += "--no-preflight"
}
if ($NoIbkrResolution) {
    $argsList += "--no-ibkr-resolution"
}
if ($NoIbkrRequired) {
    $argsList += "--no-ibkr-required"
}
if ($NoImmediateTradabilityBlock) {
    $argsList += "--no-immediate-tradability-block"
}
if ($Daemon) {
    $argsList += "--daemon"
}

& $PythonExe @argsList
exit $LASTEXITCODE
