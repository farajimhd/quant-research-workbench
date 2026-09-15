[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$RuntimeRoot, [string]$PythonExecutable)
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$outputRoot = [IO.Path]::GetFullPath($RuntimeRoot)
if ($outputRoot -eq $projectRoot -or $outputRoot.StartsWith($projectRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'RuntimeRoot must be outside ARTE source.' }
if (-not (Test-Path -LiteralPath $outputRoot -PathType Container)) { throw 'RuntimeRoot must already exist. No fallback is permitted.' }
$env:CARGO_TARGET_DIR = Join-Path $outputRoot 'cargo-target'
$env:PYTHONDONTWRITEBYTECODE = '1'
Write-Host 'ARTE offline validation: no servers, gateways, containers or network tests.'
& cargo fmt --manifest-path (Join-Path $projectRoot 'Cargo.toml') --all -- --check
if ($LASTEXITCODE -ne 0) { throw 'Rust format validation failed.' }
& cargo test --manifest-path (Join-Path $projectRoot 'Cargo.toml') --locked --offline --workspace
if ($LASTEXITCODE -ne 0) { throw 'In-process unit tests failed.' }
& cargo clippy --manifest-path (Join-Path $projectRoot 'Cargo.toml') --locked --offline --workspace --all-targets -- -D warnings
if ($LASTEXITCODE -ne 0) { throw 'Rust static checks failed.' }
$manifest = Get-Content -LiteralPath (Join-Path $projectRoot 'tests/reference/origin.json') -Raw | ConvertFrom-Json
foreach ($entry in $manifest.files) {
    $destination = [IO.Path]::GetFullPath((Join-Path $projectRoot $entry.destination))
    if (-not $destination.StartsWith($projectRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'Reference path leaves project root.' }
    if ((Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash.ToLowerInvariant() -ne $entry.sha256) { throw "Reference snapshot changed: $($entry.destination)" }
}
Write-Host 'Passed: formatting, unit tests, static checks and copied-source hashes.'
if ($PythonExecutable) {
    if (-not (Test-Path -LiteralPath $PythonExecutable -PathType Leaf)) { throw 'PythonExecutable does not exist.' }
    & cargo build --manifest-path (Join-Path $projectRoot 'Cargo.toml') --locked --offline -p arte-core --example extraction_parity
    if ($LASTEXITCODE -ne 0) { throw 'Offline parity bridge build failed.' }
    $bridgeName = if ([Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT) { 'extraction_parity.exe' } else { 'extraction_parity' }
    $bridgePath = Join-Path $env:CARGO_TARGET_DIR "debug/examples/$bridgeName"
    & $PythonExecutable -I -B (Join-Path $projectRoot 'tests/reference/check_extraction_parity.py') --rust-executable $bridgePath
    if ($LASTEXITCODE -ne 0) { throw 'Frozen-source extraction parity failed.' }
} else {
    Write-Host 'Source parity not run: supply -PythonExecutable with the offline test dependencies installed.'
}
Write-Host 'Not tested: service startup, network APIs, database writes, broker execution and browser UI.'
