[CmdletBinding()]
param(
    # Launcher.
    [string]$PythonExe = "python",

    # Operator mode.
    [ValidateSet("Prod", "Temp")]
    [string]$Mode = "Prod",

    # Process lifetime. Empty means mode default: Prod=Daemon, Temp=Once.
    [ValidateSet("", "Daemon", "Once")]
    [string]$Run = "",

    # Integrity behavior.
    [ValidateSet("Strict", "ReportOnly")]
    [string]$Integrity = "Strict",

    # Maintenance behavior. Empty means Auto.
    [ValidateSet("", "Auto", "Skip", "Force")]
    [string]$Maintenance = "",
    [string]$MaintenanceReason = "",

    # Diagnostics.
    [ValidateSet("None", "Rules", "TableGroups", "Config")]
    [string]$Diagnostics = "None"
)

$ErrorActionPreference = "Stop"
$env:PYTHONDONTWRITEBYTECODE = "1"

$invokedRepoRoot = Split-Path -Parent $PSScriptRoot
$authorityHelper = Join-Path $PSScriptRoot "repository_code_authority.ps1"
if (-not (Test-Path -LiteralPath $authorityHelper -PathType Leaf)) {
    throw "Required repository authority helper is missing: $authorityHelper"
}
. $authorityHelper

$codeAuthority = Resolve-RepositoryCodeAuthority -InvokedRepositoryRoot $invokedRepoRoot
$RepoRoot = $codeAuthority.Root
$authoritativeLauncher = Join-Path $RepoRoot "scripts\run_reference_gateway.ps1"
if (-not (Test-Path -LiteralPath $authoritativeLauncher -PathType Leaf)) {
    throw "The Reference Gateway launcher is missing from the $($codeAuthority.Kind) authority: $authoritativeLauncher"
}
if ($codeAuthority.Redirected) {
    Write-Host "Reference Gateway code authority: $($codeAuthority.Kind) at $RepoRoot"
    Write-Host "Redirecting launcher from stale/non-authoritative checkout: $invokedRepoRoot"
    & $authoritativeLauncher @PSBoundParameters
    exit $LASTEXITCODE
}

Write-Host "Reference Gateway code authority: $($codeAuthority.Kind) at $RepoRoot"
Set-Location $RepoRoot

$argsList = @("-m", "services.reference_gateway.main")

if (-not $Run) {
    $Run = if ($Mode -eq "Prod") { "Daemon" } else { "Once" }
}
if (-not $Maintenance) {
    $Maintenance = "Auto"
}
if ($Maintenance -eq "Force" -and -not $MaintenanceReason.Trim()) {
    if ($Mode -eq "Temp") {
        $MaintenanceReason = "reference gateway temp maintenance force"
    }
    else {
        throw "-MaintenanceReason is required when -Maintenance Force is set."
    }
}

$argsList += @("--mode", $Mode.ToLowerInvariant())
$argsList += @("--run", $Run.ToLowerInvariant())
$argsList += @("--integrity", $Integrity.ToLowerInvariant().Replace("reportonly", "report-only"))
$argsList += @("--maintenance", $Maintenance.ToLowerInvariant())
$argsList += @("--diagnostics", $Diagnostics.ToLowerInvariant().Replace("tablegroups", "table-groups"))
if ($MaintenanceReason.Trim()) {
    $argsList += @("--maintenance-reason", $MaintenanceReason)
}

& $PythonExe @argsList
exit $LASTEXITCODE
