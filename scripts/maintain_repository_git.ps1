[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$RuntimeRoot = "",
    [switch]$Compact
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent $PSScriptRoot
$gitRoot = [IO.Path]::GetFullPath((Join-Path $repoRoot ".git")).TrimEnd('\')

function Resolve-MaintenanceRuntimeRoot {
    param([string]$Requested)

    $candidate = if ($Requested.Trim()) {
        $Requested.Trim()
    }
    elseif ($env:QW_REPOSITORY_MAINTENANCE_ROOT) {
        $env:QW_REPOSITORY_MAINTENANCE_ROOT.Trim()
    }
    else {
        "D:\TradingML\runtimes\repository_maintenance\quant-research-workbench"
    }
    $resolved = [IO.Path]::GetFullPath($candidate).TrimEnd('\')
    $resolvedRepo = [IO.Path]::GetFullPath($repoRoot).TrimEnd('\')
    if ($resolved.Equals($resolvedRepo, [StringComparison]::OrdinalIgnoreCase) -or
        $resolved.StartsWith($resolvedRepo + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "Repository maintenance output must be outside the repository: $resolved"
    }
    if ([IO.Path]::GetPathRoot($resolved) -ne [IO.Path]::GetPathRoot($gitRoot)) {
        throw "The recovery backup must share the repository volume so Git objects can be hard-linked: $resolved"
    }
    return $resolved
}

function Get-DirectoryMeasure {
    param([string]$Path)

    $measure = Get-ChildItem -LiteralPath $Path -Recurse -File -Force -ErrorAction Stop |
        Measure-Object Length -Sum
    return [pscustomobject]@{
        Files = [int64]$measure.Count
        Bytes = [int64]$measure.Sum
    }
}

function Invoke-CheckedGit {
    param([string[]]$Arguments)

    & git @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "git $($Arguments -join ' ') failed with exit code $LASTEXITCODE"
    }
}

if (-not (Test-Path -LiteralPath $gitRoot -PathType Container)) {
    throw "The repository Git directory is missing: $gitRoot"
}

$runtimeRoot = Resolve-MaintenanceRuntimeRoot -Requested $RuntimeRoot
$before = Get-DirectoryMeasure -Path $gitRoot
$reachableDiskBytes = [int64](& git rev-list --disk-usage --objects --all)
if ($LASTEXITCODE -ne 0) {
    throw "Unable to measure reachable Git objects."
}

Write-Host "Repository:          $repoRoot"
Write-Host "Git directory:       $gitRoot"
Write-Host ("Git physical size:  {0:N2} GiB ({1:N0} files)" -f ($before.Bytes / 1GB), $before.Files)
Write-Host ("Reachable disk size: {0:N2} MiB" -f ($reachableDiskBytes / 1MB))

if (-not $Compact) {
    Write-Host "Audit only; pass -Compact to create an external recovery backup and prune unreachable Git data."
    return
}

$lockFiles = @(Get-ChildItem -LiteralPath $gitRoot -Recurse -File -Force -Filter "*.lock" -ErrorAction SilentlyContinue)
if ($lockFiles.Count -gt 0) {
    throw "Git maintenance refuses to run while lock files exist: $($lockFiles.FullName -join ', ')"
}

$timestamp = [DateTime]::UtcNow.ToString("yyyyMMdd_HHmmss")
$backupRoot = Join-Path $runtimeRoot "git_recovery_$timestamp"
$backupGitRoot = Join-Path $backupRoot ".git"
if (-not $PSCmdlet.ShouldProcess(
    $gitRoot,
    "Hard-link a recovery copy to '$backupGitRoot', expire unreachable reflogs, and run git gc --prune=now"
)) {
    return
}

[IO.Directory]::CreateDirectory($backupGitRoot) | Out-Null
$objectsRoot = Join-Path $gitRoot "objects"
$backupObjectsRoot = Join-Path $backupGitRoot "objects"
$excludedRecoveryPrefixes = @("refs\codex", "logs\refs\codex")

function Test-ExcludedRecoveryPath {
    param([string]$RelativePath)

    foreach ($prefix in $excludedRecoveryPrefixes) {
        if ($RelativePath.Equals($prefix, [StringComparison]::OrdinalIgnoreCase) -or
            $RelativePath.StartsWith($prefix + '\', [StringComparison]::OrdinalIgnoreCase)) {
            return $true
        }
    }
    return $false
}

foreach ($directory in Get-ChildItem -LiteralPath $gitRoot -Recurse -Directory -Force -ErrorAction Stop) {
    $relative = $directory.FullName.Substring($gitRoot.Length).TrimStart('\')
    if (Test-ExcludedRecoveryPath -RelativePath $relative) {
        continue
    }
    [IO.Directory]::CreateDirectory((Join-Path $backupGitRoot $relative)) | Out-Null
}

foreach ($file in Get-ChildItem -LiteralPath $gitRoot -Recurse -File -Force -ErrorAction Stop) {
    $relative = $file.FullName.Substring($gitRoot.Length).TrimStart('\')
    if (Test-ExcludedRecoveryPath -RelativePath $relative) {
        continue
    }
    $destination = Join-Path $backupGitRoot $relative
    if ($file.FullName.StartsWith($objectsRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
        New-Item -ItemType HardLink -Path $destination -Target $file.FullName -ErrorAction Stop | Out-Null
    }
    else {
        Copy-Item -LiteralPath $file.FullName -Destination $destination -Force
    }
}

$sourceObjects = Get-DirectoryMeasure -Path $objectsRoot
$backupObjects = Get-DirectoryMeasure -Path $backupObjectsRoot
if ($sourceObjects.Files -ne $backupObjects.Files -or $sourceObjects.Bytes -ne $backupObjects.Bytes) {
    throw (
        "Recovery object verification failed: source=$($sourceObjects.Files)/$($sourceObjects.Bytes), " +
        "backup=$($backupObjects.Files)/$($backupObjects.Bytes)"
    )
}

$manifest = [ordered]@{
    schema_version = 1
    created_at_utc = [DateTime]::UtcNow.ToString("o")
    repository_root = $repoRoot
    source_git_root = $gitRoot
    backup_git_root = $backupGitRoot
    head = [string](& git rev-parse HEAD)
    branch = [string](& git branch --show-current)
    source_files = $before.Files
    source_bytes = $before.Bytes
    source_object_files = $sourceObjects.Files
    source_object_bytes = $sourceObjects.Bytes
    excluded_volatile_refs = $excludedRecoveryPrefixes
    recovery_note = "Copy this .git directory back only after stopping all Git and Codex processes; volatile refs/codex turn-diff checkpoints are intentionally excluded."
}
$manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $backupRoot "manifest.json") -Encoding UTF8

Invoke-CheckedGit -Arguments @("fsck", "--connectivity-only")
Invoke-CheckedGit -Arguments @("reflog", "expire", "--expire-unreachable=now", "--all")
Invoke-CheckedGit -Arguments @("config", "gc.auto", "1000")
Invoke-CheckedGit -Arguments @("config", "gc.autoPackLimit", "10")
Invoke-CheckedGit -Arguments @("config", "gc.pruneExpire", "7.days.ago")
Invoke-CheckedGit -Arguments @("gc", "--prune=now")
Invoke-CheckedGit -Arguments @("fsck", "--connectivity-only")

$after = Get-DirectoryMeasure -Path $gitRoot
$savedBytes = $before.Bytes - $after.Bytes
Write-Host "Recovery backup:     $backupRoot"
Write-Host ("Compacted Git size: {0:N2} MiB ({1:N0} files)" -f ($after.Bytes / 1MB), $after.Files)
Write-Host ("Repository reduction: {0:N2} GiB" -f ($savedBytes / 1GB))
