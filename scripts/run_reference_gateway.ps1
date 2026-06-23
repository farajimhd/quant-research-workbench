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
    [string]$MarketHoursWriteReason = ""
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

& $PythonExe @argsList
exit $LASTEXITCODE
