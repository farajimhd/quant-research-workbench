param(
    [string]$PythonExe = "python",
    [switch]$PrintRules,
    [switch]$ActiveTickerCheck
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

& $PythonExe @argsList
