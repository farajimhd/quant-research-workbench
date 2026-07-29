[CmdletBinding()]
param(
    [ValidateRange(1, 65535)]
    [int]$QmdHistoryPort = 8801,
    [ValidateRange(1, 65535)]
    [int]$BackendPort = 8000,
    [ValidateRange(1, 65535)]
    [int]$FrontendPort = 5173,
    [ValidateRange(1, 60)]
    [int]$GracefulTimeoutSeconds = 8,
    [string]$PythonExe = "",
    [switch]$ListOnly
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

Import-Module CimCmdlets -ErrorAction Stop

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
        $activeCondaPython = Join-Path $env:CONDA_PREFIX "python.exe"
        if (Test-Path -LiteralPath $activeCondaPython -PathType Leaf) {
            return $activeCondaPython
        }
    }

    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCommand) {
        return $pythonCommand.Source
    }

    foreach ($candidate in @(
        (Join-Path $env:USERPROFILE "miniconda3\python.exe"),
        (Join-Path $env:USERPROFILE "anaconda3\python.exe")
    )) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return $candidate
        }
    }

    return ""
}

function Get-ProcessSnapshot {
    $snapshot = @{}
    foreach ($process in Get-CimInstance Win32_Process) {
        $snapshot[[int]$process.ProcessId] = $process
    }
    return $snapshot
}

function Test-WorkspaceServiceProcess {
    param($Process)

    $name = ([string]$Process.Name).ToLowerInvariant()
    $commandLine = ([string]$Process.CommandLine).ToLowerInvariant()

    if ($name -eq "qmd-history-gateway.exe" -or
        $commandLine.Contains("run_qmd_history_gateway.ps1") -or
        $commandLine.Contains("services\qmd_history_gateway")) {
        return $true
    }
    if ($commandLine.Contains("run_backend.ps1") -or
        ($commandLine.Contains("uvicorn") -and $commandLine.Contains("src.backend.app:app"))) {
        return $true
    }
    if ($commandLine.Contains("run_frontend.py") -or
        ($commandLine.Contains("vite") -and $commandLine.Contains("quant-research-workbench"))) {
        return $true
    }
    return $false
}

function Get-PortOwnerIds {
    param([int[]]$Ports)

    $ownerIds = [Collections.Generic.HashSet[int]]::new()
    foreach ($port in $Ports) {
        foreach ($connection in @(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue)) {
            if ($connection.OwningProcess -gt 0) {
                [void]$ownerIds.Add([int]$connection.OwningProcess)
            }
        }
    }
    return ,$ownerIds
}

function Get-TargetProcessIds {
    param(
        [hashtable]$Snapshot,
        [int[]]$Ports
    )

    $targetIds = Get-PortOwnerIds -Ports $Ports
    foreach ($entry in $Snapshot.GetEnumerator()) {
        if (Test-WorkspaceServiceProcess -Process $entry.Value) {
            [void]$targetIds.Add([int]$entry.Key)
        }
    }

    # Include children so reloaders, npm/cmd wrappers, Cargo, and service
    # binaries are stopped as one bounded service tree.
    $changed = $true
    while ($changed) {
        $changed = $false
        foreach ($entry in $Snapshot.GetEnumerator()) {
            $candidateProcessId = [int]$entry.Key
            $parentPid = [int]$entry.Value.ParentProcessId
            if (-not $targetIds.Contains($candidateProcessId) -and $targetIds.Contains($parentPid)) {
                [void]$targetIds.Add($candidateProcessId)
                $changed = $true
            }
        }
    }

    # Include only service-identifiable ancestors. This closes an existing
    # launcher console without walking into an unrelated terminal or IDE.
    foreach ($seedPid in @($targetIds)) {
        $currentPid = $seedPid
        while ($Snapshot.ContainsKey($currentPid)) {
            $parentPid = [int]$Snapshot[$currentPid].ParentProcessId
            if (-not $Snapshot.ContainsKey($parentPid)) {
                break
            }
            $parent = $Snapshot[$parentPid]
            if (-not (Test-WorkspaceServiceProcess -Process $parent)) {
                break
            }
            [void]$targetIds.Add($parentPid)
            $currentPid = $parentPid
        }
    }

    [void]$targetIds.Remove([int]$PID)
    return ,$targetIds
}

function Test-ProcessAlive {
    param([int]$ProcessId)

    return $null -ne (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)
}

function Send-GracefulConsoleInterrupt {
    param(
        [string]$Python,
        [int]$ProcessId
    )

    if (-not $Python) {
        return $false
    }

    $helper = @'
import ctypes
import sys
import time

kernel32 = ctypes.windll.kernel32
kernel32.FreeConsole()
if not kernel32.AttachConsole(int(sys.argv[1])):
    raise SystemExit(2)
kernel32.SetConsoleCtrlHandler(None, True)
sent = kernel32.GenerateConsoleCtrlEvent(0, 0)
time.sleep(0.25)
kernel32.FreeConsole()
raise SystemExit(0 if sent else 3)
'@

    & $Python -c $helper "$ProcessId"
    return $LASTEXITCODE -eq 0
}

function Wait-ForTargetsToExit {
    param(
        [Collections.Generic.HashSet[int]]$TargetIds,
        [int]$TimeoutSeconds
    )

    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        $remaining = @($TargetIds | Where-Object { Test-ProcessAlive -ProcessId $_ })
        if ($remaining.Count -eq 0) {
            return @()
        }
        Start-Sleep -Milliseconds 250
    } while ([DateTime]::UtcNow -lt $deadline)

    return @($TargetIds | Where-Object { Test-ProcessAlive -ProcessId $_ })
}

$ports = @($QmdHistoryPort, $BackendPort, $FrontendPort) | Select-Object -Unique
$snapshot = Get-ProcessSnapshot
$targetIds = Get-TargetProcessIds -Snapshot $snapshot -Ports $ports

if ($targetIds.Count -eq 0) {
    Write-Host "No QMD History, backend, frontend, or configured-port processes are running."
    return
}

Write-Host "Matched service processes:"
foreach ($targetPid in ($targetIds | Sort-Object)) {
    if ($snapshot.ContainsKey($targetPid)) {
        $process = $snapshot[$targetPid]
        Write-Host ("  PID {0,-7} {1}  {2}" -f $targetPid, $process.Name, $process.CommandLine)
    }
    else {
        Write-Host ("  PID {0}" -f $targetPid)
    }
}

if ($ListOnly) {
    Write-Host "List-only mode; no process was stopped."
    return
}

$python = Resolve-PythonExecutable -Requested $PythonExe
$gracefulSignalSent = $false

# Service launchers started by start_workspace_services.ps1 own independent
# consoles. Sending Ctrl+C to the oldest matching processes gives their
# launchers and children the normal shutdown signal.
foreach ($targetPid in ($targetIds | Sort-Object)) {
    if (-not (Test-ProcessAlive -ProcessId $targetPid)) {
        continue
    }
    if (Send-GracefulConsoleInterrupt -Python $python -ProcessId $targetPid) {
        Write-Host "Sent Ctrl+C to the console containing PID $targetPid."
        $gracefulSignalSent = $true
        Start-Sleep -Milliseconds 300
    }
}

if (-not $gracefulSignalSent) {
    Write-Warning "No matching console accepted Ctrl+C; proceeding to the bounded forced fallback."
}

$remainingIds = @(Wait-ForTargetsToExit -TargetIds $targetIds -TimeoutSeconds $GracefulTimeoutSeconds)
if ($remainingIds.Count -gt 0) {
    Write-Warning ("Force-stopping {0} process(es) that did not exit after Ctrl+C: {1}" -f $remainingIds.Count, ($remainingIds -join ", "))
    foreach ($targetPid in ($remainingIds | Sort-Object -Descending)) {
        Stop-Process -Id $targetPid -Force -ErrorAction SilentlyContinue
    }
}

# Reloaders can replace a child PID during shutdown. Rescan once and remove any
# remaining matching identity or exact configured-port owner.
Start-Sleep -Milliseconds 400
$finalSnapshot = Get-ProcessSnapshot
$finalTargetIds = Get-TargetProcessIds -Snapshot $finalSnapshot -Ports $ports
if ($finalTargetIds.Count -gt 0) {
    Write-Warning ("Cleaning up {0} replacement or remaining process(es): {1}" -f $finalTargetIds.Count, (($finalTargetIds | Sort-Object) -join ", "))
    foreach ($targetPid in ($finalTargetIds | Sort-Object -Descending)) {
        Stop-Process -Id $targetPid -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Milliseconds 400
}

$busyPorts = @()
foreach ($port in $ports) {
    if (@(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue).Count -gt 0) {
        $busyPorts += $port
    }
}
$remainingMatches = Get-TargetProcessIds -Snapshot (Get-ProcessSnapshot) -Ports $ports
if ($busyPorts.Count -gt 0 -or $remainingMatches.Count -gt 0) {
    throw "Shutdown verification failed. Busy ports: $($busyPorts -join ', '); remaining matching PIDs: $(($remainingMatches | Sort-Object) -join ', ')."
}

Write-Host "Stopped all matching QMD History, backend, and frontend instances; configured ports are free."
