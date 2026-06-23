param(
    [string]$PythonExe = "python",
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
    [switch]$Daemon
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

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
if ($Daemon) {
    $argsList += "--daemon"
}

& $PythonExe @argsList
exit $LASTEXITCODE
