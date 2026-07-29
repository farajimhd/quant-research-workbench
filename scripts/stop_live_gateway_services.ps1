[CmdletBinding()]
param(
    [ValidateRange(1, 65535)]
    [int]$NewsPort = 8796,
    [ValidateRange(1, 65535)]
    [int]$SecPort = 8797,
    [ValidateRange(1, 65535)]
    [int]$ReferencePort = 8799,
    [ValidateRange(1, 65535)]
    [int]$IbkrSupervisorPort = 8800,
    [ValidateRange(1, 900)]
    [int]$GracefulTimeoutSeconds = 330,
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

    foreach ($candidate in @(
        (Join-Path $env:USERPROFILE "miniconda3\envs\ml4t\python.exe"),
        (Join-Path $env:USERPROFILE "anaconda3\envs\ml4t\python.exe"),
        (Join-Path $env:USERPROFILE "miniconda3\python.exe"),
        (Join-Path $env:USERPROFILE "anaconda3\python.exe")
    )) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return $candidate
        }
    }

    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCommand) {
        return $pythonCommand.Source
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

function Test-LiveGatewayProcess {
    param($Process)

    $commandLine = ([string]$Process.CommandLine).ToLowerInvariant()

    if ($commandLine.Contains("run_news_gateway.ps1") -or
        $commandLine.Contains("-m services.news_gateway.main")) {
        return $true
    }
    if ($commandLine.Contains("run_sec_gateway.ps1") -or
        $commandLine.Contains("-m services.sec_gateway.main")) {
        return $true
    }
    if ($commandLine.Contains("run_reference_gateway.ps1") -or
        $commandLine.Contains("-m services.reference_gateway.main")) {
        return $true
    }
    if ($commandLine.Contains("run_ibkr_gateway_supervisor.ps1") -or
        $commandLine.Contains("-m services.ibkr_gateway_supervisor.main") -or
        $commandLine.Contains("clientportal.gw") -or
        ($commandLine.Contains("bin\run.bat") -and $commandLine.Contains("root\conf.yaml"))) {
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
        if (Test-LiveGatewayProcess -Process $entry.Value) {
            [void]$targetIds.Add([int]$entry.Key)
        }
    }

    $changed = $true
    while ($changed) {
        $changed = $false
        foreach ($entry in $Snapshot.GetEnumerator()) {
            $candidateProcessId = [int]$entry.Key
            $parentProcessId = [int]$entry.Value.ParentProcessId
            if (-not $targetIds.Contains($candidateProcessId) -and $targetIds.Contains($parentProcessId)) {
                [void]$targetIds.Add($candidateProcessId)
                $changed = $true
            }
        }
    }

    foreach ($seedProcessId in @($targetIds)) {
        $currentProcessId = $seedProcessId
        while ($Snapshot.ContainsKey($currentProcessId)) {
            $parentProcessId = [int]$Snapshot[$currentProcessId].ParentProcessId
            if (-not $Snapshot.ContainsKey($parentProcessId)) {
                break
            }
            $parent = $Snapshot[$parentProcessId]
            if (-not (Test-LiveGatewayProcess -Process $parent)) {
                break
            }
            [void]$targetIds.Add($parentProcessId)
            $currentProcessId = $parentProcessId
        }
    }

    [void]$targetIds.Remove([int]$PID)
    return ,$targetIds
}

function Get-TargetRootIds {
    param(
        [Collections.Generic.HashSet[int]]$TargetIds,
        [hashtable]$Snapshot
    )

    $rootIds = [Collections.Generic.HashSet[int]]::new()
    foreach ($targetProcessId in $TargetIds) {
        if (-not $Snapshot.ContainsKey($targetProcessId)) {
            [void]$rootIds.Add($targetProcessId)
            continue
        }
        $parentProcessId = [int]$Snapshot[$targetProcessId].ParentProcessId
        if (-not $TargetIds.Contains($parentProcessId)) {
            [void]$rootIds.Add($targetProcessId)
        }
    }
    return ,$rootIds
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
    $nextProgress = [DateTime]::UtcNow
    do {
        $remaining = @($TargetIds | Where-Object { Test-ProcessAlive -ProcessId $_ })
        if ($remaining.Count -eq 0) {
            return @()
        }
        if ([DateTime]::UtcNow -ge $nextProgress) {
            $secondsRemaining = [Math]::Max(0, [int][Math]::Ceiling(($deadline - [DateTime]::UtcNow).TotalSeconds))
            Write-Host ("Waiting for graceful shutdown: {0} process(es) remain; up to {1}s left." -f $remaining.Count, $secondsRemaining)
            $nextProgress = [DateTime]::UtcNow.AddSeconds(5)
        }
        Start-Sleep -Milliseconds 250
    } while ([DateTime]::UtcNow -lt $deadline)

    return @($TargetIds | Where-Object { Test-ProcessAlive -ProcessId $_ })
}

$ports = @(
    $NewsPort,
    $SecPort,
    $ReferencePort,
    $IbkrSupervisorPort
) | Select-Object -Unique
$snapshot = Get-ProcessSnapshot
$targetIds = Get-TargetProcessIds -Snapshot $snapshot -Ports $ports

if ($targetIds.Count -eq 0) {
    Write-Host "No News, SEC, Reference, IBKR Supervisor, Client Portal, or configured-port processes are running."
    return
}

Write-Host "Matched service processes:"
foreach ($targetProcessId in ($targetIds | Sort-Object)) {
    if ($snapshot.ContainsKey($targetProcessId)) {
        $process = $snapshot[$targetProcessId]
        Write-Host ("  PID {0,-7} {1}  {2}" -f $targetProcessId, $process.Name, $process.CommandLine)
    }
    else {
        Write-Host ("  PID {0}" -f $targetProcessId)
    }
}

if ($ListOnly) {
    Write-Host "List-only mode; no process was stopped."
    return
}

$python = Resolve-PythonExecutable -Requested $PythonExe
$rootIds = Get-TargetRootIds -TargetIds $targetIds -Snapshot $snapshot
$gracefulSignalSent = $false

foreach ($rootProcessId in ($rootIds | Sort-Object)) {
    if (-not (Test-ProcessAlive -ProcessId $rootProcessId)) {
        continue
    }
    if (Send-GracefulConsoleInterrupt -Python $python -ProcessId $rootProcessId) {
        Write-Host "Sent Ctrl+C to the service console containing root PID $rootProcessId."
        $gracefulSignalSent = $true
    }
    else {
        Write-Warning "The console containing root PID $rootProcessId did not accept Ctrl+C."
    }
}

if (-not $gracefulSignalSent) {
    Write-Warning "No matching console accepted Ctrl+C; waiting before the bounded forced fallback."
}

$remainingIds = @(Wait-ForTargetsToExit -TargetIds $targetIds -TimeoutSeconds $GracefulTimeoutSeconds)
if ($remainingIds.Count -gt 0) {
    Write-Warning ("Force-stopping {0} process(es) that did not exit after the grace window: {1}" -f $remainingIds.Count, ($remainingIds -join ", "))
    foreach ($targetProcessId in ($remainingIds | Sort-Object -Descending)) {
        Stop-Process -Id $targetProcessId -Force -ErrorAction SilentlyContinue
    }
}

Start-Sleep -Milliseconds 500
$finalSnapshot = Get-ProcessSnapshot
$finalTargetIds = Get-TargetProcessIds -Snapshot $finalSnapshot -Ports $ports
if ($finalTargetIds.Count -gt 0) {
    Write-Warning ("Cleaning up {0} replacement or remaining process(es): {1}" -f $finalTargetIds.Count, (($finalTargetIds | Sort-Object) -join ", "))
    foreach ($targetProcessId in ($finalTargetIds | Sort-Object -Descending)) {
        Stop-Process -Id $targetProcessId -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Milliseconds 500
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

Write-Host "Stopped all matching live gateway service trees; configured ports are free."
