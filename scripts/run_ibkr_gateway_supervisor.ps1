[CmdletBinding()]
param(
    [string]$PythonExe = "python",
    [string]$Account = "paper",
    [switch]$CheckOnly,
    [switch]$LoginOnce,
    [switch]$NoLaunch,
    [switch]$Headless
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$argsList = @("-m", "services.ibkr_gateway_supervisor.main", "--account", $Account)
if ($CheckOnly) {
    $argsList += "--check-only"
}
if ($LoginOnce) {
    $argsList += "--login-once"
}
if ($NoLaunch) {
    $argsList += "--no-launch"
}
if ($Headless) {
    $argsList += "--headless"
}

& $PythonExe @argsList
exit $LASTEXITCODE
