[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidatePattern('^[A-Za-z0-9._-]{1,80}$')]
    [string]$CheckpointSetId,
    [Parameter(Mandatory)]
    [string]$RuntimeDir,
    [string]$ResumeFromRuntime,
    [Nullable[datetime]]$StartDate,
    [Nullable[datetime]]$EndDate,
    [ValidateRange(1, 80)]
    [int]$Workers = 80,
    [string]$Binary,
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$SourceCommit,
    [switch]$NoBuild,
    [switch]$Rebuild,
    [switch]$ForegroundSupervisor,
    [switch]$MonitorExisting,
    [ValidateSet('graceful', 'fast')]
    [string]$StopExisting,
    [string]$PythonExe = '',
    [Parameter(ValueFromRemainingArguments)]
    [string[]]$CampaignArguments
)

$ErrorActionPreference = 'Stop'
$env:PYTHONDONTWRITEBYTECODE = '1'
$repoRoot = Split-Path -Parent $PSScriptRoot

if (-not $env:DOTENV_PATHS) {
    $repoParent = Split-Path -Parent $repoRoot
    $tradingRoot = Split-Path -Parent $repoParent
    $canonicalSecretEnv = Join-Path $tradingRoot 'secrets\.env'
    if (Test-Path -LiteralPath $canonicalSecretEnv -PathType Leaf) {
        $env:DOTENV_PATHS = $canonicalSecretEnv
    }
}

function Resolve-PythonExecutable {
    param([string]$Requested)

    if ($Requested.Trim()) {
        $requestedCommand = Get-Command $Requested.Trim() -ErrorAction SilentlyContinue
        if ($requestedCommand) {
            return $requestedCommand.Source
        }
        if (Test-Path -LiteralPath $Requested.Trim() -PathType Leaf) {
            return (Resolve-Path -LiteralPath $Requested.Trim()).Path
        }
        throw "The requested Python executable does not exist: $Requested"
    }

    if ($env:CONDA_PREFIX) {
        $activeCondaPython = Join-Path $env:CONDA_PREFIX 'python.exe'
        if (Test-Path -LiteralPath $activeCondaPython -PathType Leaf) {
            return $activeCondaPython
        }
    }

    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCommand) {
        return $pythonCommand.Source
    }

    throw 'Python was not found. Activate the intended environment or pass -PythonExe <path-to-python.exe>.'
}
$resolvedPython = Resolve-PythonExecutable -Requested $PythonExe
if ($StartDate.HasValue -xor $EndDate.HasValue) {
    throw '-StartDate and -EndDate must be supplied together.'
}
if ($StartDate.HasValue -and $StartDate.Value.Date -gt $EndDate.Value.Date) {
    throw '-StartDate must be on or before -EndDate.'
}
if (-not $ResumeFromRuntime -and -not $StartDate.HasValue -and -not $MonitorExisting -and -not $StopExisting) {
    throw 'A fresh campaign requires -StartDate/-EndDate; a successor recovery requires -ResumeFromRuntime.'
}
if ($NoBuild -and $Rebuild) {
    throw '-NoBuild and -Rebuild are mutually exclusive.'
}

$launcher = Join-Path $PSScriptRoot 'run_structure_checkpoint_campaign.py'
$launcherArguments = @(
    $launcher,
    '--checkpoint-set-id', $CheckpointSetId,
    '--runtime-dir', [IO.Path]::GetFullPath($RuntimeDir),
    '--workers', [string]$Workers,
    '--process-workers', [string]$Workers
)
if ($ResumeFromRuntime) {
    $launcherArguments += @('--resume-from-runtime', [IO.Path]::GetFullPath($ResumeFromRuntime))
}
if ($StartDate.HasValue) {
    $launcherArguments += @(
        '--start-date', $StartDate.Value.ToString('yyyy-MM-dd'),
        '--end-date', $EndDate.Value.ToString('yyyy-MM-dd')
    )
}
if ($Binary) {
    $launcherArguments += @('--binary', [IO.Path]::GetFullPath($Binary))
}
if ($SourceCommit) { $launcherArguments += @('--source-commit', $SourceCommit.ToLowerInvariant()) }
if ($NoBuild) { $launcherArguments += '--no-build' }
if ($Rebuild) { $launcherArguments += '--rebuild' }
if ($ForegroundSupervisor) { $launcherArguments += '--foreground-supervisor' }
if ($MonitorExisting) { $launcherArguments += '--monitor-existing' }
if ($StopExisting) { $launcherArguments += @('--stop-existing', $StopExisting) }
if ($CampaignArguments) { $launcherArguments += $CampaignArguments }

Write-Host 'Structural Checkpoint Campaign v8' -ForegroundColor Cyan
if ($ResumeFromRuntime) {
    Write-Host "Recovery source remains immutable: $ResumeFromRuntime" -ForegroundColor DarkGray
    Write-Host "Successor target: $CheckpointSetId" -ForegroundColor Cyan
}
& $resolvedPython @launcherArguments
exit $LASTEXITCODE
