[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$RuntimeDir,
    [Parameter(Mandatory)][string]$CheckpointSetId,
    [ValidateRange(0, 120)][int]$GraceSeconds = 60
)
$ErrorActionPreference = 'Stop'
$env:PYTHONDONTWRITEBYTECODE = '1'
$runtime = (Resolve-Path -LiteralPath $RuntimeDir).Path.TrimEnd('\')
if ($runtime.StartsWith('\\')) { throw 'Run this stop helper locally on the campaign host, using its local runtime path.' }
$manifest = Get-Content -LiteralPath (Join-Path $runtime 'campaign-manifest.json') -Raw | ConvertFrom-Json
$identityPath = Join-Path $runtime 'supervisor\supervisor.json'
$identity = Get-Content -LiteralPath $identityPath -Raw | ConvertFrom-Json
if ($manifest.checkpoint_set_id -ne $CheckpointSetId -or $identity.checkpoint_set_id -ne $CheckpointSetId) {
    throw 'Campaign identity mismatch; nothing stopped.'
}
$setPattern = '(?i)(?:^|[\s"])' + [regex]::Escape($CheckpointSetId) + '(?=$|[\s"])'
$runtimePattern = '(?i)' + [regex]::Escape($runtime) + '(?=$|[\\\s"])'
function Get-OwnedCampaignProcesses {
    $candidates = @(Get-CimInstance Win32_Process -Filter "Name='structure_checkpoint_campaign_v18.exe' OR Name='python.exe' OR Name='pythonw.exe'")
    foreach ($process in $candidates) {
        # CIM enumeration races with cooperative shutdown. Re-query incomplete
        # rows by PID; an exited process is not a permissions failure.
        if (($process.Name -eq 'structure_checkpoint_campaign_v18.exe' -or $process.ProcessId -eq $identity.pid) -and (-not $process.ExecutablePath -or -not $process.CommandLine)) {
            $fresh = @(Get-CimInstance Win32_Process -Filter "ProcessId = $($process.ProcessId)" | Where-Object { $_.ProcessId -eq $process.ProcessId })
            if (-not $fresh.Count) { continue }
            if ($fresh[0].CreationDate -ne $process.CreationDate) { continue }
            $process = $fresh[0]
        }
        # An inspectable command line or executable can rule out another run
        # without requiring access to every unrelated process on the host.
        if ($process.CommandLine -and ($process.CommandLine -notmatch $setPattern -or $process.CommandLine -notmatch $runtimePattern)) { continue }
        if ($process.Name -eq 'structure_checkpoint_campaign_v18.exe' -and $process.ExecutablePath -and $process.ExecutablePath -ine $identity.executable_path) { continue }
        if ($process.ProcessId -eq $identity.pid -and -not $process.CommandLine) { throw "Cannot inspect live supervisor PID $($process.ProcessId). Rerun from PowerShell opened as Administrator." }
        if ($process.Name -eq 'structure_checkpoint_campaign_v18.exe' -and (-not $process.ExecutablePath -or -not $process.CommandLine)) {
            throw "Cannot inspect live campaign PID $($process.ProcessId) after rechecking. Rerun from PowerShell opened as Administrator. No unverified process was terminated."
        }
        if ($process.CommandLine -notmatch $setPattern -or $process.CommandLine -notmatch $runtimePattern) { continue }
        if ($process.ExecutablePath -ieq $identity.executable_path) { $process; continue }
        if ($process.ProcessId -eq $identity.pid -and $process.CommandLine -match 'run_structure_checkpoint_campaign\.py') { $process }
    }
}
function Stop-VerifiedProcess($candidate) {
    # Re-query identity and creation time to guard against PID reuse.
    $current = @(Get-OwnedCampaignProcesses | Where-Object { $_.ProcessId -eq $candidate.ProcessId -and $_.CreationDate -eq $candidate.CreationDate })
    if ($current.Count -eq 1) {
        Write-Host "Stopping verified campaign process $($candidate.ProcessId) ($($candidate.Name))"
        Stop-Process -Id $candidate.ProcessId -Force -ErrorAction Stop
    }
}
& (Join-Path $PSScriptRoot 'run_structure_checkpoint_campaign.ps1') -CheckpointSetId $CheckpointSetId -RuntimeDir $runtime -StopExisting fast
if ($LASTEXITCODE -ne 0) { throw 'Campaign stop request failed.' }
$deadline = [DateTime]::UtcNow.AddSeconds($GraceSeconds)
do {
    $owned = @(Get-OwnedCampaignProcesses)
    if (-not $owned.Count) { break }
    if ([DateTime]::UtcNow -ge $deadline) { break }
    Write-Host "Stopping campaign: $($owned.Count) verified processes remain; deadline $($deadline.ToString('HH:mm:ss')) UTC"
    Start-Sleep -Seconds 2
} while ($true)
# Stop children first, then allow the supervisor to finalize its registry/status.
foreach ($process in @(Get-OwnedCampaignProcesses | Where-Object { $_.ExecutablePath -ieq $identity.executable_path })) {
    Stop-VerifiedProcess $process
}
$deadline = [DateTime]::UtcNow.AddSeconds(10)
while (@(Get-OwnedCampaignProcesses).Count -and [DateTime]::UtcNow -lt $deadline) { Start-Sleep -Seconds 1 }
foreach ($process in @(Get-OwnedCampaignProcesses)) { Stop-VerifiedProcess $process }
$deadline = [DateTime]::UtcNow.AddSeconds(10)
while (@(Get-OwnedCampaignProcesses).Count -and [DateTime]::UtcNow -lt $deadline) { Start-Sleep -Seconds 1 }
if (@(Get-OwnedCampaignProcesses).Count) { throw 'Verified campaign processes remain; restart is blocked.' }
# Preserve the previous evidence before reconciling a supervisor that died
# without publishing its final state. This does not certify or alter checkpoints.
$statusPath = Join-Path $runtime 'campaign-status.json'
$status = Get-Content -LiteralPath $statusPath -Raw | ConvertFrom-Json
if ($status.checkpoint_set_id -ne $CheckpointSetId) { throw 'Status identity mismatch.' }
if ($status.status -in @('running','degraded','stale','stopping')) {
    $audit = Join-Path $runtime ('stop-evidence-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffffffZ'))
    New-Item -ItemType Directory -Path $audit | Out-Null
    Copy-Item -LiteralPath $statusPath,$identityPath -Destination $audit
    $status.status = 'interrupted'
    $status.updated_at = [DateTime]::UtcNow.ToString('o')
    $status | Add-Member -NotePropertyName stop_evidence -NotePropertyValue 'Verified no matching local campaign processes; previous status archived.' -Force
    $temporary = "$statusPath.$([Guid]::NewGuid().ToString('N')).tmp"
    [IO.File]::WriteAllText($temporary, ($status | ConvertTo-Json -Depth 20), [Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $temporary -Destination $statusPath -Force
}
Write-Host 'Campaign stopped. Certified checkpoints preserved; successor may now recover them.'
