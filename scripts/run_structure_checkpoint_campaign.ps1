[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string[]]$TickerFile,
    [Parameter(Mandatory)]
    [datetime]$StartDate,
    [Parameter(Mandatory)]
    [datetime]$EndDate,
    [ValidatePattern('^[A-Za-z0-9._-]{1,80}$')]
    [string]$CampaignId = 'structure-v15-campaign',
    [string]$QmdLiveUrl = 'http://127.0.0.1:8795',
    [string]$QmdHistoryUrl = 'http://127.0.0.1:8801',
    [string]$RuntimeRoot = 'D:\TradingML\runtimes\qmd_gateway',
    [ValidateRange(2, 3650)]
    [int]$LookbackDays = 180,
    [ValidateRange(1, 32)]
    [int]$Workers = 4,
    [ValidateRange(1, 250000000)]
    [int]$EventBudget = 3500000,
    [ValidateRange(1, 250000000)]
    [int]$EventLimit = 50000000,
    [ValidateRange(60, 7200)]
    [int]$TimeoutSeconds = 1800,
    [ValidateRange(0, 10)]
    [int]$MaxRetries = 3,
    [ValidateRange(0.1, 60)]
    [double]$RetryDelaySeconds = 2.0,
    [switch]$ContinueOnError,
    [string]$PythonExe = 'C:\Users\g835l\miniconda3\envs\ml4t\python.exe'
)

$ErrorActionPreference = 'Stop'
$env:PYTHONDONTWRITEBYTECODE = '1'

if ($StartDate.Date -gt $EndDate.Date) {
    throw '-StartDate must be on or before -EndDate.'
}
if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
    throw "Python executable is unavailable: $PythonExe"
}
foreach ($path in $TickerFile) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Ticker universe file is unavailable: $path"
    }
}

$repositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$campaignRoot = Join-Path ([IO.Path]::GetFullPath($RuntimeRoot)) ("structure-checkpoint-campaign-v2\{0}" -f $CampaignId)
$planRoot = Join-Path $campaignRoot 'plan'
New-Item -ItemType Directory -Force -Path $planRoot | Out-Null

$planningStart = $StartDate.Date.AddDays(-$LookbackDays)
$plannerArguments = @(
    (Join-Path $repositoryRoot 'scripts\plan_structure_checkpoint_batches.py'),
    '--start-date', $planningStart.ToString('yyyy-MM-dd'),
    '--end-date', $EndDate.Date.AddDays(1).ToString('yyyy-MM-dd'),
    '--output-dir', $planRoot,
    '--estimate-url', ($QmdHistoryUrl.TrimEnd('/') + '/estimate/generic-structure-event-counts'),
    '--event-budget', [string]$EventBudget
)
foreach ($path in $TickerFile) {
    $plannerArguments += @('--ticker-file', [IO.Path]::GetFullPath($path))
}

Write-Host "Planning Campaign v2 from the continuity index: $CampaignId"
& $PythonExe @plannerArguments
if ($LASTEXITCODE -ne 0) {
    throw "Structural checkpoint planner failed with exit code $LASTEXITCODE."
}

$planPath = Join-Path $planRoot 'plan.json'
$plan = Get-Content -LiteralPath $planPath -Raw | ConvertFrom-Json
if ([int]$plan.schema_version -ne 2) {
    throw "Unsupported structural checkpoint plan schema: $($plan.schema_version)"
}

$groups = $plan.group_files.PSObject.Properties | Sort-Object { [int]$_.Name }
foreach ($group in $groups) {
    $bootstrapDays = [int]$group.Name
    $groupFile = [string]$group.Value
    $reportPath = Join-Path $campaignRoot ("status-bootstrap-{0}.json" -f $bootstrapDays)
    Write-Host ("Running bootstrap group {0} days from {1}" -f $bootstrapDays, $groupFile)
    $builderArguments = @{
        TickerFile = @($groupFile)
        StartDate = $StartDate.Date
        EndDate = $EndDate.Date
        QmdLiveUrl = $QmdLiveUrl
        RuntimeRoot = $RuntimeRoot
        EventLimit = $EventLimit
        TimeoutSeconds = $TimeoutSeconds
        Workers = $Workers
        BootstrapDays = $bootstrapDays
        LookbackDays = $LookbackDays
        MaxRetries = $MaxRetries
        RetryDelaySeconds = $RetryDelaySeconds
        ReportPath = $reportPath
        PythonExe = $PythonExe
    }
    if ($ContinueOnError) {
        $builderArguments.ContinueOnError = $true
    }
    & (Join-Path $repositoryRoot 'scripts\build_structure_level_checkpoints.ps1') @builderArguments
}

Write-Host "Campaign v2 completed. Runtime evidence: $campaignRoot"
