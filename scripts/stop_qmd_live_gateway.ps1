[CmdletBinding()]
param(
    [ValidateRange(1, 65535)]
    [int]$QmdLivePort = 8795,
    [ValidateRange(1, 60)]
    [int]$GracefulTimeoutSeconds = 8,
    [string]$PythonExe = "",
    [string]$QmdLiveServiceRuntimeRoot = "",
    [string]$LegacyWorkspaceRuntimeRoot = "",
    [switch]$ListOnly
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$env:PYTHONDONTWRITEBYTECODE = "1"

Import-Module CimCmdlets -ErrorAction Stop

$repoRoot = Split-Path -Parent $PSScriptRoot
$serviceTabHost = [IO.Path]::GetFullPath(
    (Join-Path $PSScriptRoot "run_windows_terminal_service_tab.ps1")
)
$serviceRoles = @("qmd_live")

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
        (Join-Path $env:USERPROFILE "miniconda3\python.exe"),
        (Join-Path $env:USERPROFILE "anaconda3\python.exe")
    )) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return $candidate
        }
    }

    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCommand -and
        $pythonCommand.Source -and
        $pythonCommand.Source.IndexOf("\Microsoft\WindowsApps\", [StringComparison]::OrdinalIgnoreCase) -lt 0) {
        return $pythonCommand.Source
    }

    return ""
}

function Resolve-QmdLiveServiceRuntimeRoot {
    param([string]$Requested)

    $candidate = if ($Requested.Trim()) {
        $Requested.Trim()
    }
    elseif ($env:QMD_LIVE_SERVICE_RUNTIME_ROOT) {
        $env:QMD_LIVE_SERVICE_RUNTIME_ROOT.Trim()
    }
    else {
        "D:\TradingML\runtimes\qmd_live_service"
    }
    $resolved = [IO.Path]::GetFullPath($candidate).TrimEnd('\')
    $resolvedRepo = [IO.Path]::GetFullPath($repoRoot).TrimEnd('\')
    if ($resolved.Equals($resolvedRepo, [StringComparison]::OrdinalIgnoreCase) -or
        $resolved.StartsWith($resolvedRepo + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "QMD Live service runtime state must be outside the repository: $resolved"
    }
    return $resolved
}

function Resolve-LegacyWorkspaceRuntimeRoot {
    param([string]$Requested)

    $candidate = if ($Requested.Trim()) {
        $Requested.Trim()
    }
    elseif ($env:QW_WORKSPACE_SERVICES_RUNTIME_ROOT) {
        $env:QW_WORKSPACE_SERVICES_RUNTIME_ROOT.Trim()
    }
    else {
        "D:\TradingML\runtimes\workspace_services"
    }
    $resolved = [IO.Path]::GetFullPath($candidate).TrimEnd('\')
    $resolvedRepo = [IO.Path]::GetFullPath($repoRoot).TrimEnd('\')
    if ($resolved.Equals($resolvedRepo, [StringComparison]::OrdinalIgnoreCase) -or
        $resolved.StartsWith($resolvedRepo + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "Legacy workspace runtime state must be outside the repository: $resolved"
    }
    return $resolved
}

function Get-ProcessSnapshot {
    $snapshot = @{}
    foreach ($process in Get-CimInstance Win32_Process) {
        $snapshot[[int]$process.ProcessId] = $process
    }
    return $snapshot
}

function Test-ProcessStartIdentity {
    param(
        $Process,
        $ExpectedUtc
    )

    try {
        if ($ExpectedUtc -is [DateTimeOffset]) {
            $expected = [DateTimeOffset]$ExpectedUtc
        }
        elseif ($ExpectedUtc -is [DateTime]) {
            $expected = [DateTimeOffset]([DateTime]$ExpectedUtc).ToUniversalTime()
        }
        else {
            $expected = [DateTimeOffset]::ParseExact(
                [string]$ExpectedUtc,
                "o",
                [Globalization.CultureInfo]::InvariantCulture,
                [Globalization.DateTimeStyles]::RoundtripKind
            )
        }
    }
    catch {
        return $false
    }
    $actual = ([DateTime]$Process.CreationDate).ToUniversalTime()
    return [Math]::Abs(($actual - $expected.UtcDateTime).TotalSeconds) -le 2
}

function Read-ValidRegistration {
    param(
        [string]$Path,
        [hashtable]$Snapshot
    )

    try {
        $record = Get-Content -Raw -LiteralPath $Path | ConvertFrom-Json
    }
    catch {
        Write-Warning "Ignoring unreadable QMD Live ownership record '$Path': $($_.Exception.Message)"
        return $null
    }

    try {
        if ([int]$record.schema_version -ne 1 -or
            [string]$record.service_role -notin $serviceRoles -or
            [string]::IsNullOrWhiteSpace([string]$record.instance_id)) {
            throw "required ownership fields are invalid"
        }
        $recordRepo = [IO.Path]::GetFullPath([string]$record.repository_root).TrimEnd('\')
        $recordPath = [IO.Path]::GetFullPath([string]$record.registry_path)
        $expectedPath = [IO.Path]::GetFullPath($Path)
        $hostPid = [int]$record.host_pid
    }
    catch {
        Write-Warning "Ignoring invalid QMD Live ownership record '$Path': $($_.Exception.Message)"
        return $null
    }
    $expectedRepo = [IO.Path]::GetFullPath($repoRoot).TrimEnd('\')
    if (-not $recordRepo.Equals($expectedRepo, [StringComparison]::OrdinalIgnoreCase)) {
        Write-Warning "Ignoring ownership record for another repository: $Path"
        return $null
    }
    if (-not $recordPath.Equals($expectedPath, [StringComparison]::OrdinalIgnoreCase)) {
        Write-Warning "Ignoring ownership record whose recorded path does not match its file: $Path"
        return $null
    }

    if ($hostPid -le 0 -or -not $Snapshot.ContainsKey($hostPid)) {
        return $null
    }
    $hostProcess = $Snapshot[$hostPid]
    $hostName = ([string]$hostProcess.Name).ToLowerInvariant()
    $hostCommand = [string]$hostProcess.CommandLine
    $identityFailures = @()
    if ($hostName -notin @("powershell.exe", "pwsh.exe")) { $identityFailures += "host executable" }
    if ($hostCommand.IndexOf($serviceTabHost, [StringComparison]::OrdinalIgnoreCase) -lt 0) { $identityFailures += "tab host path" }
    if ($hostCommand.IndexOf($recordPath, [StringComparison]::OrdinalIgnoreCase) -lt 0) { $identityFailures += "registry path" }
    if ($hostCommand.IndexOf([string]$record.instance_id, [StringComparison]::OrdinalIgnoreCase) -lt 0) { $identityFailures += "instance id" }
    if (-not (Test-ProcessStartIdentity -Process $hostProcess -ExpectedUtc $record.host_started_at_utc)) { $identityFailures += "process start time" }
    if ($identityFailures.Count -gt 0) {
        Write-Warning "Ignoring stale or mismatched QMD Live ownership record ($($identityFailures -join ', ')): $Path"
        return $null
    }

    return [pscustomobject]@{
        Path = $Path
        Record = $record
        HostPid = $hostPid
    }
}

function Get-OwnedProcessIds {
    param(
        [object[]]$Registrations,
        [hashtable]$Snapshot
    )

    $owned = [Collections.Generic.HashSet[int]]::new()
    foreach ($registration in $Registrations) {
        [void]$owned.Add([int]$registration.HostPid)
    }
    $changed = $true
    while ($changed) {
        $changed = $false
        foreach ($entry in $Snapshot.GetEnumerator()) {
            $candidatePid = [int]$entry.Key
            $parentPid = [int]$entry.Value.ParentProcessId
            if (-not $owned.Contains($candidatePid) -and $owned.Contains($parentPid)) {
                [void]$owned.Add($candidatePid)
                $changed = $true
            }
        }
    }
    [void]$owned.Remove([int]$PID)
    return ,$owned
}

function Get-PortOwners {
    param([int[]]$Ports)

    $owners = @()
    foreach ($port in $Ports) {
        foreach ($connection in @(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue)) {
            $owners += [pscustomobject]@{
                Port = $port
                ProcessId = [int]$connection.OwningProcess
            }
        }
    }
    return $owners
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

function Wait-ForHostsToExit {
    param(
        [int[]]$HostIds,
        [int]$TimeoutSeconds
    )

    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        $remaining = @($HostIds | Where-Object { Test-ProcessAlive -ProcessId $_ })
        if ($remaining.Count -eq 0) {
            return @()
        }
        Start-Sleep -Milliseconds 250
    } while ([DateTime]::UtcNow -lt $deadline)
    return @($HostIds | Where-Object { Test-ProcessAlive -ProcessId $_ })
}

$runtimeRoot = Resolve-QmdLiveServiceRuntimeRoot -Requested $QmdLiveServiceRuntimeRoot
$legacyRuntimeRoot = Resolve-LegacyWorkspaceRuntimeRoot -Requested $LegacyWorkspaceRuntimeRoot
$qmdLiveInstanceRoot = Join-Path $runtimeRoot "instances"
$legacyInstanceRoot = Join-Path $legacyRuntimeRoot "instances"
$instanceRoots = @(
    $qmdLiveInstanceRoot,
    $legacyInstanceRoot
) | Select-Object -Unique
$registrationPaths = @()
foreach ($instanceRoot in $instanceRoots) {
    if (Test-Path -LiteralPath $instanceRoot -PathType Container) {
        $registrationPaths += @(Get-ChildItem -LiteralPath $instanceRoot -Recurse -File -Filter "*.json" -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty FullName)
    }
}

$snapshot = Get-ProcessSnapshot
$registrations = @()
$stalePaths = @()
foreach ($registrationPath in $registrationPaths) {
    try {
        $recordedRole = [string]((Get-Content -Raw -LiteralPath $registrationPath | ConvertFrom-Json).service_role)
    }
    catch {
        $recordedRole = ""
    }
    $resolvedRegistrationPath = [IO.Path]::GetFullPath($registrationPath)
    $isLegacyWorkspaceRecord = $resolvedRegistrationPath.StartsWith(
        [IO.Path]::GetFullPath($legacyInstanceRoot).TrimEnd('\') + '\',
        [StringComparison]::OrdinalIgnoreCase
    )
    if ($isLegacyWorkspaceRecord -and $recordedRole -ne "qmd_live") {
        continue
    }
    if ($recordedRole -and $recordedRole -notin $serviceRoles) {
        continue
    }
    $registration = Read-ValidRegistration -Path $registrationPath -Snapshot $snapshot
    if ($null -ne $registration) {
        $registrations += $registration
    }
    else {
        $stalePaths += $registrationPath
    }
}
$ownedIds = Get-OwnedProcessIds -Registrations $registrations -Snapshot $snapshot
$registeredPorts = @($registrations | ForEach-Object { [int]$_.Record.service_port } | Where-Object { $_ -gt 0 })
$ports = @(@($QmdLivePort) + $registeredPorts) |
    Select-Object -Unique
$portOwners = @(Get-PortOwners -Ports $ports)

if ($registrations.Count -eq 0) {
    Write-Host "No launcher-owned QMD Live service instances are running."
}
else {
    Write-Host "Registered QMD Live service instances:"
    foreach ($registration in ($registrations | Sort-Object { $_.Record.service_role })) {
        Write-Host (
            "  {0,-12} host PID {1,-7} child PID {2,-7} instance {3}" -f
            $registration.Record.service_role,
            $registration.HostPid,
            $registration.Record.child_pid,
            $registration.Record.instance_id
        )
    }
}

$foreignPortOwners = @($portOwners | Where-Object { -not $ownedIds.Contains([int]$_.ProcessId) })
if ($foreignPortOwners.Count -gt 0) {
    Write-Warning (
        "Configured ports have non-owned listeners that will not be stopped: " +
        (($foreignPortOwners | ForEach-Object { "port=$($_.Port) pid=$($_.ProcessId)" }) -join "; ")
    )
}

if ($ListOnly) {
    Write-Host "List-only mode; no process or ownership record was changed."
    return
}

foreach ($stalePath in $stalePaths) {
    Remove-Item -LiteralPath $stalePath -Force -ErrorAction SilentlyContinue
}
if ($registrations.Count -eq 0) {
    return
}

$python = Resolve-PythonExecutable -Requested $PythonExe
$hostIds = @($registrations | Select-Object -ExpandProperty HostPid -Unique)
foreach ($hostPid in $hostIds) {
    if (Test-ProcessAlive -ProcessId $hostPid) {
        if (Send-GracefulConsoleInterrupt -Python $python -ProcessId $hostPid) {
            Write-Host "Sent one Ctrl+C event to registered service console host PID $hostPid."
        }
        else {
            Write-Warning "Registered service console host PID $hostPid did not accept Ctrl+C."
        }
    }
}

$remainingHosts = @(Wait-ForHostsToExit -HostIds $hostIds -TimeoutSeconds $GracefulTimeoutSeconds)
if ($remainingHosts.Count -gt 0) {
    Write-Warning (
        "Force-stopping registered service host(s) after the graceful timeout: " +
        ($remainingHosts -join ", ")
    )
    foreach ($hostPid in $remainingHosts) {
        Stop-Process -Id $hostPid -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Milliseconds 500
}

# The tab host owns a kill-on-close Windows Job Object. Closing or terminating
# that validated host is the bounded fallback for its complete child tree.
$remainingOwned = @($ownedIds | Where-Object { Test-ProcessAlive -ProcessId $_ })
if ($remainingOwned.Count -gt 0) {
    throw "Registered QMD Live process cleanup failed; remaining PIDs: $($remainingOwned -join ', ')."
}
foreach ($registration in $registrations) {
    Remove-Item -LiteralPath $registration.Path -Force -ErrorAction SilentlyContinue
}

$remainingPortOwners = @(Get-PortOwners -Ports $ports)
$remainingOwnedPortOwners = @($remainingPortOwners | Where-Object { $ownedIds.Contains([int]$_.ProcessId) })
if ($remainingOwnedPortOwners.Count -gt 0) {
    throw (
        "Owned QMD Live listeners remain after shutdown: " +
        (($remainingOwnedPortOwners | ForEach-Object { "port=$($_.Port) pid=$($_.ProcessId)" }) -join "; ")
    )
}

Write-Host "Stopped all registered QMD Live service instances. Foreign processes and ports were left untouched."
