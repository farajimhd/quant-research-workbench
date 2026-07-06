param(
    [int[]]$Years = @(2019, 2020),
    [string[]]$Kinds = @("quotes_v1", "trades_v1"),
    [string]$SourceRoot = "G:\market-data\flatfiles\us_stocks_sip",
    [string]$DestinationRoot = "D:\market-data\flatfiles\us_stocks_sip",
    [string]$LogRoot = "D:\market-data\prepared\flatfile_copy_logs",
    [int]$Threads = 16,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

function Get-TreeStats {
    param([string]$Path)
    if (!(Test-Path -LiteralPath $Path)) {
        return [pscustomobject]@{ Files = 0; Bytes = [int64]0 }
    }
    $summary = Get-ChildItem -LiteralPath $Path -Recurse -File -ErrorAction Stop |
        Measure-Object -Property Length -Sum
    $bytes = if ($null -eq $summary.Sum) { [int64]0 } else { [int64]$summary.Sum }
    return [pscustomobject]@{
        Files = [int64]$summary.Count
        Bytes = $bytes
    }
}

function Format-Bytes {
    param([int64]$Bytes)
    if ($Bytes -ge 1TB) { return "{0:n2} TiB" -f ($Bytes / 1TB) }
    if ($Bytes -ge 1GB) { return "{0:n2} GiB" -f ($Bytes / 1GB) }
    if ($Bytes -ge 1MB) { return "{0:n2} MiB" -f ($Bytes / 1MB) }
    return "$Bytes B"
}

if (!(Test-Path -LiteralPath $SourceRoot)) {
    throw "Source root not found: $SourceRoot"
}
if (!(Test-Path -LiteralPath $DestinationRoot)) {
    New-Item -ItemType Directory -Path $DestinationRoot -Force | Out-Null
}
if (!(Test-Path -LiteralPath $LogRoot)) {
    New-Item -ItemType Directory -Path $LogRoot -Force | Out-Null
}

$runId = Get-Date -Format "yyyyMMdd_HHmmss"
$summaryPath = Join-Path $LogRoot "copy_flatfiles_g_to_d_$runId.jsonl"
$startedAt = Get-Date

Write-Host "FLATFILE COPY G -> D run_id=$runId"
Write-Host "source=$SourceRoot"
Write-Host "dest=$DestinationRoot"
Write-Host "years=$($Years -join ',') kinds=$($Kinds -join ',') threads=$Threads dry_run=$DryRun"
Write-Host "summary=$summaryPath"

foreach ($year in $Years) {
    foreach ($kind in $Kinds) {
        $source = Join-Path $SourceRoot (Join-Path $kind ([string]$year))
        $dest = Join-Path $DestinationRoot (Join-Path $kind ([string]$year))
        $logPath = Join-Path $LogRoot "robocopy_${kind}_${year}_$runId.log"

        $record = [ordered]@{
            type = "copy_start"
            utc = (Get-Date).ToUniversalTime().ToString("o")
            year = $year
            kind = $kind
            source = $source
            destination = $dest
            log = $logPath
        }
        $record | ConvertTo-Json -Compress | Add-Content -LiteralPath $summaryPath

        if (!(Test-Path -LiteralPath $source)) {
            $skip = [ordered]@{
                type = "copy_done"
                utc = (Get-Date).ToUniversalTime().ToString("o")
                year = $year
                kind = $kind
                status = "missing_source"
                source = $source
            }
            $skip | ConvertTo-Json -Compress | Add-Content -LiteralPath $summaryPath
            Write-Host "SKIP $kind $year missing source: $source" -ForegroundColor Yellow
            continue
        }
        if (!(Test-Path -LiteralPath $dest)) {
            New-Item -ItemType Directory -Path $dest -Force | Out-Null
        }

        $srcStatsBefore = Get-TreeStats -Path $source
        $dstStatsBefore = Get-TreeStats -Path $dest
        Write-Host ("START {0} {1}: source files={2:n0} bytes={3}; dest files={4:n0} bytes={5}" -f `
            $kind, $year, $srcStatsBefore.Files, (Format-Bytes $srcStatsBefore.Bytes), `
            $dstStatsBefore.Files, (Format-Bytes $dstStatsBefore.Bytes))

        $args = @(
            "`"$source`"",
            "`"$dest`"",
            "/E",
            "/Z",
            "/J",
            "/MT:$Threads",
            "/R:2",
            "/W:5",
            "/XO",
            "/FFT",
            "/NP",
            "/TEE",
            "/LOG+:`"$logPath`""
        )
        if ($DryRun) {
            $args += "/L"
        }

        $copyStart = Get-Date
        $process = Start-Process -FilePath "robocopy.exe" -ArgumentList $args -NoNewWindow -Wait -PassThru
        $copyEnd = Get-Date
        $exitCode = [int]$process.ExitCode
        $status = if ($exitCode -le 7) { "ok" } else { "failed" }

        $srcStatsAfter = Get-TreeStats -Path $source
        $dstStatsAfter = Get-TreeStats -Path $dest
        $checksMatch = ($srcStatsAfter.Files -eq $dstStatsAfter.Files) -and ($srcStatsAfter.Bytes -eq $dstStatsAfter.Bytes)
        if ($DryRun) {
            $checksMatch = $true
        }
        if ($status -eq "ok" -and -not $checksMatch) {
            $status = "mismatch"
        }

        $done = [ordered]@{
            type = "copy_done"
            utc = (Get-Date).ToUniversalTime().ToString("o")
            year = $year
            kind = $kind
            status = $status
            robocopy_exit_code = $exitCode
            seconds = [math]::Round(($copyEnd - $copyStart).TotalSeconds, 3)
            source_files = $srcStatsAfter.Files
            source_bytes = $srcStatsAfter.Bytes
            destination_files = $dstStatsAfter.Files
            destination_bytes = $dstStatsAfter.Bytes
            checks_match = $checksMatch
            source = $source
            destination = $dest
            log = $logPath
        }
        $done | ConvertTo-Json -Compress | Add-Content -LiteralPath $summaryPath

        if ($status -ne "ok") {
            Write-Host "FAILED $kind $year status=$status robocopy_exit=$exitCode log=$logPath" -ForegroundColor Red
            throw "Copy failed for $kind $year; status=$status robocopy_exit=$exitCode"
        }
        Write-Host ("OK {0} {1}: files={2:n0} bytes={3} seconds={4:n1}" -f `
            $kind, $year, $dstStatsAfter.Files, (Format-Bytes $dstStatsAfter.Bytes), ($copyEnd - $copyStart).TotalSeconds) `
            -ForegroundColor Green
    }
}

$elapsed = (Get-Date) - $startedAt
Write-Host ("DONE run_id={0} elapsed={1:n1}s summary={2}" -f $runId, $elapsed.TotalSeconds, $summaryPath) -ForegroundColor Green
