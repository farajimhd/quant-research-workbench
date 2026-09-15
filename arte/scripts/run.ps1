[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][ValidateSet('laptop','workstation')][string]$Device,
    [ValidateSet('Plan','Start')][string]$Action='Plan',
    [Parameter(Mandatory=$true)][string]$ReleasePath
)
$ErrorActionPreference='Stop'
$root=[IO.Path]::GetFullPath($ReleasePath)
$release=Get-Content -LiteralPath (Join-Path $root 'release.json') -Raw | ConvertFrom-Json
if ($release.schema_version -ne 1) { throw 'Unsupported release manifest.' }
foreach ($entry in $release.files) {
    $path=[IO.Path]::GetFullPath((Join-Path $root $entry.path))
    if (-not $path.StartsWith($root + [IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)) { throw 'Release member leaves its root.' }
    if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $entry.sha256) { throw 'Release hash mismatch.' }
}
Write-Host "ARTE $($release.release_id) | device $Device | action $Action"
Write-Host 'Blocked: service orchestration, approved hardware budgets and connected acceptance are incomplete.'
if ($Action -eq 'Start') { throw 'Service startup is not implemented or enabled. No process has been started.' }
