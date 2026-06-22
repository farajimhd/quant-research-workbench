param(
    [string]$PythonExe = "python",
    [switch]$PrintRules,
    [switch]$ActiveTickerCheck,
    [switch]$EnsureMarketPublicationSchema
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$argsList = @("-m", "services.reference_gateway.main")
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
