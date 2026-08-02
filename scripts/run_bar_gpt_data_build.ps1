[CmdletBinding()]
param(
    [switch]$Execute,
    [switch]$CommandOnly,
    [string]$StartDate = "2019-01-01",
    [string]$EndDate = "auto",
    [string]$AdjustmentAsOfDate = "auto",
    [ValidateSet("auto", "rich", "text")]
    [string]$ProgressLayout = "auto",
    [string]$PythonExe = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Resolve-PythonExecutable {
    param([string]$Requested)

    if ($Requested) {
        if (-not (Test-Path -LiteralPath $Requested -PathType Leaf)) {
            throw "Python executable does not exist: $Requested"
        }
        return (Resolve-Path -LiteralPath $Requested).Path
    }

    if ($env:CONDA_PREFIX) {
        $condaPython = Join-Path $env:CONDA_PREFIX "python.exe"
        if (Test-Path -LiteralPath $condaPython -PathType Leaf) {
            return $condaPython
        }
    }

    $command = Get-Command python -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }

    throw "Python was not found. Activate the ml4t environment or pass -PythonExe."
}

function Format-Command {
    param([string]$Executable, [string[]]$Arguments)

    $items = @($Executable) + $Arguments
    return ($items | ForEach-Object {
        if ($_ -match '[\s"]') { '"' + ($_ -replace '"', '\"') + '"' } else { $_ }
    }) -join " "
}

function Invoke-BuildStage {
    param(
        [int]$Number,
        [string]$Name,
        [string]$Module,
        [string[]]$Arguments
    )

    $prefix = "[$Number/3]"
    Write-Host ""
    Write-Host "$prefix $Name" -ForegroundColor Cyan
    $commandText = Format-Command -Executable $script:ResolvedPython -Arguments (@("-B", "-m", $Module) + $Arguments)

    if ($CommandOnly) {
        Write-Host "      planned  $commandText"
        return
    }

    Write-Host "      running"
    $startedAt = [datetime]::UtcNow
    & $script:ResolvedPython "-B" "-m" $Module @Arguments
    $exitCode = $LASTEXITCODE
    $elapsed = [datetime]::UtcNow - $startedAt

    if ($exitCode -ne 0) {
        Write-Host "      stopped  exit=$exitCode elapsed=$($elapsed.ToString('hh\:mm\:ss'))" -ForegroundColor Red
        Write-Host "      resume   wait for the child to exit, then rerun this same command" -ForegroundColor Yellow
        exit $exitCode
    }

    Write-Host "      complete elapsed=$($elapsed.ToString('hh\:mm\:ss'))" -ForegroundColor Green
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location -LiteralPath $repoRoot
$script:ResolvedPython = Resolve-PythonExecutable -Requested $PythonExe

if ($AdjustmentAsOfDate -eq "auto") {
    $AdjustmentAsOfDate = [TimeZoneInfo]::ConvertTimeBySystemTimeZoneId(
        [datetime]::UtcNow,
        "Eastern Standard Time"
    ).ToString("yyyy-MM-dd")
}

$common = @(
    "--start-date", $StartDate,
    "--end-date", $EndDate,
    "--progress-layout", $ProgressLayout
)
if ($Execute) {
    $common += "--execute"
}

$mode = if ($CommandOnly) { "command-only" } elseif ($Execute) { "execute" } else { "preview" }
Write-Host "BarGPT data authority build" -ForegroundColor White
Write-Host "mode=$mode  range=[$StartDate, $EndDate)  adjustment_asof=$AdjustmentAsOfDate"
Write-Host "resume=completed certified units are skipped; an incomplete unit is retried"

Invoke-BuildStage -Number 1 -Name "Raw SIP daily sessions (Canvas / Replay / QMD)" `
    -Module "pipelines.market_sip.events.run_build_daily_session_bars" -Arguments $common

$adjustedArgs = @($common) + @("--adjustment-asof-date", $AdjustmentAsOfDate)
Invoke-BuildStage -Number 2 -Name "Split-adjusted 1-second authority (BarGPT)" `
    -Module "research.bar_gpt.v1.run_build_adjusted_1s" -Arguments $adjustedArgs

Invoke-BuildStage -Number 3 -Name "Split-adjusted daily sessions (BarGPT)" `
    -Module "research.bar_gpt.v1.run_build_daily_sessions_from_adjusted_1s" -Arguments $common

Write-Host ""
if ($CommandOnly) {
    Write-Host "All three commands resolved; nothing was executed." -ForegroundColor Green
} else {
    Write-Host "BarGPT data authority build completed." -ForegroundColor Green
}
