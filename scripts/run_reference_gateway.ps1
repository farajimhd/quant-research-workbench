param(
    [string]$PythonExe = "python",
    [string]$ReadDatabase = "",
    [string]$WriteDatabase = "",
    [string]$TestWriteDatabase = "",
    [switch]$Execute,
    [switch]$PrintRules,
    [switch]$ActiveTickerCheck,
    [switch]$EnsureMarketPublicationSchema
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
