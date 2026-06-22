param(
    [string]$PythonExe = "python",
    [switch]$PrintRules
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$argsList = @("-m", "services.reference_gateway.main")
if ($PrintRules) {
    $argsList += "--print-rules"
}

& $PythonExe @argsList
