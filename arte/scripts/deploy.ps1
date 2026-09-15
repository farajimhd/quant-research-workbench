[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$RuntimeRoot,
    [Parameter(Mandatory=$true)][string]$InstallRoot,
    [Parameter(Mandatory=$true)][ValidatePattern('^[a-zA-Z0-9][a-zA-Z0-9._-]*$')][string]$ReleaseId
)
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$runtimePath = [IO.Path]::GetFullPath($RuntimeRoot)
$installPath = [IO.Path]::GetFullPath($InstallRoot)
foreach ($path in @($runtimePath,$installPath)) {
    if ($path -eq $projectRoot -or $path.StartsWith($projectRoot + [IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)) { throw 'Output roots must be outside source.' }
    if (-not (Test-Path -LiteralPath $path -PathType Container)) { throw 'Explicit existing runtime and install roots are required.' }
}
$releasePath = Join-Path $installPath $ReleaseId
if (Test-Path -LiteralPath $releasePath) { throw 'Immutable release already exists. Choose a new release ID.' }
$env:CARGO_TARGET_DIR = Join-Path $runtimePath 'cargo-target'
& cargo build --manifest-path (Join-Path $projectRoot 'Cargo.toml') --locked --offline --release --package arte-cli
if ($LASTEXITCODE -ne 0) { throw 'Release build failed.' }
$binary = Join-Path $env:CARGO_TARGET_DIR 'release/arte.exe'
if (-not (Test-Path -LiteralPath $binary)) { throw 'Windows ARTE binary missing.' }
New-Item -ItemType Directory -Path $releasePath | Out-Null
Copy-Item -LiteralPath $binary -Destination (Join-Path $releasePath 'arte.exe')
$release = [ordered]@{schema_version=1;release_id=$ReleaseId;service_capable=$false;files=@([ordered]@{path='arte.exe';sha256=(Get-FileHash -LiteralPath (Join-Path $releasePath 'arte.exe') -Algorithm SHA256).Hash.ToLowerInvariant()})}
$release | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $releasePath 'release.json') -Encoding utf8
Write-Host "Installed offline ARTE tools: $releasePath"
Write-Host 'No service started. No current-service pointer changed. This release is not live-capable.'
