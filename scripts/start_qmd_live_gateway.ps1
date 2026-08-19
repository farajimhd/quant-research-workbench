[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$HostName = "127.0.0.1",
    [ValidateRange(1, 65535)]
    [int]$QmdLivePort = 8795,
    [string]$PythonExe = "",
    [string]$WindowsTerminalExe = "",
    [ValidateSet("Auto", "Caller", "Named")]
    [string]$TerminalTarget = "Auto",
    [string]$TerminalWindowName = "quant-research-workbench-qmd-live",
    [string]$QmdLiveServiceRuntimeRoot = "",
    [ValidateRange(0.01, 100.0)]
    [double]$MaxGitDirectoryGB = 2.0
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$env:PYTHONDONTWRITEBYTECODE = "1"

$repoRoot = Split-Path -Parent $PSScriptRoot
$qmdLiveLauncher = Join-Path $PSScriptRoot "run_qmd_gateway.ps1"
$serviceTabHost = Join-Path $PSScriptRoot "run_windows_terminal_service_tab.ps1"
$terminalWindowTargetHelper = Join-Path $PSScriptRoot "windows_terminal_window_target.ps1"

if (-not (Test-Path -LiteralPath $terminalWindowTargetHelper -PathType Leaf)) {
    throw "Required Windows Terminal target helper is missing: $terminalWindowTargetHelper"
}
. $terminalWindowTargetHelper

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

    $userCandidates = @(
        (Join-Path $env:USERPROFILE "miniconda3\python.exe"),
        (Join-Path $env:USERPROFILE "anaconda3\python.exe")
    )
    foreach ($candidate in $userCandidates) {
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

    throw "Python was not found. Activate the intended environment or pass -PythonExe <path-to-python.exe>."
}

function Assert-Launcher {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Required service launcher is missing: $Path"
    }
}

function Resolve-WindowsTerminalExecutable {
    param([string]$Requested)

    if ($Requested.Trim()) {
        $requestedCommand = Get-Command $Requested.Trim() -ErrorAction SilentlyContinue
        if ($requestedCommand) {
            return $requestedCommand.Source
        }
        if (Test-Path -LiteralPath $Requested.Trim() -PathType Leaf) {
            return (Resolve-Path -LiteralPath $Requested.Trim()).Path
        }
        throw "The requested Windows Terminal executable does not exist: $Requested"
    }

    $terminalCommand = Get-Command wt.exe -ErrorAction SilentlyContinue
    if ($terminalCommand) {
        return $terminalCommand.Source
    }

    $appExecutionAlias = Join-Path $env:LOCALAPPDATA "Microsoft\WindowsApps\wt.exe"
    if (Test-Path -LiteralPath $appExecutionAlias -PathType Leaf) {
        return $appExecutionAlias
    }

    throw "Windows Terminal (wt.exe) was not found. Install Windows Terminal or pass -WindowsTerminalExe <path-to-wt.exe>."
}

function ConvertTo-PowerShellLiteral {
    param([string]$Value)

    return "'" + $Value.Replace("'", "''") + "'"
}

function ConvertTo-PowerShellEncodedCommand {
    param([string]$Command)

    return [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($Command))
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
    $resolved = [IO.Path]::GetFullPath($candidate)
    $resolvedRepo = [IO.Path]::GetFullPath($repoRoot).TrimEnd('\')
    if ($resolved.TrimEnd('\').Equals($resolvedRepo, [StringComparison]::OrdinalIgnoreCase) -or
        $resolved.StartsWith($resolvedRepo + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "QMD Live service runtime state must be outside the repository: $resolved"
    }
    return $resolved
}

function Assert-ServicePortsAvailable {
    param([object[]]$Tabs)

    $conflicts = @()
    foreach ($tab in $Tabs) {
        foreach ($connection in @(Get-NetTCPConnection -State Listen -LocalPort $tab.Port -ErrorAction SilentlyContinue)) {
            $conflicts += "role=$($tab.Role) port=$($tab.Port) pid=$($connection.OwningProcess)"
        }
    }
    if ($conflicts.Count -gt 0) {
        throw (
            "QMD Live startup refuses to adopt an existing port owner because it was not created by this launcher. " +
            "Stop or relocate them explicitly. Conflicts: " + ($conflicts -join "; ")
        )
    }
}

function Assert-RepositoryGitSize {
    param([double]$MaximumGB)

    $gitRoot = Join-Path $repoRoot ".git"
    if (-not (Test-Path -LiteralPath $gitRoot -PathType Container)) {
        return
    }
    $measure = Get-ChildItem -LiteralPath $gitRoot -Recurse -File -Force -ErrorAction Stop |
        Measure-Object Length -Sum
    $sizeGB = [double]$measure.Sum / 1GB
    if ($sizeGB -gt $MaximumGB) {
        throw (
            "QMD Live startup stopped because .git is {0:N2} GiB, above the {1:N2} GiB safety limit. " -f
            $sizeGB, $MaximumGB
        ) + (
            "An oversized Git database makes Codex status and diff snapshots slow even though services are separate. " +
            "Audit with scripts\maintain_repository_git.ps1 and compact with " +
            "scripts\maintain_repository_git.ps1 -Compact before starting services."
        )
    }
}

Assert-Launcher -Path $qmdLiveLauncher
Assert-Launcher -Path $serviceTabHost
Assert-RepositoryGitSize -MaximumGB $MaxGitDirectoryGB

$resolvedPython = Resolve-PythonExecutable -Requested $PythonExe
$callerTerminalWindow = Get-WindowsTerminalCallerWindow `
    -PythonExecutable $resolvedPython
$powerShellExe = (Get-Command powershell.exe -ErrorAction Stop).Source
$resolvedWindowsTerminal = Resolve-WindowsTerminalExecutable -Requested $WindowsTerminalExe
$resolvedQmdLiveServiceRuntimeRoot = Resolve-QmdLiveServiceRuntimeRoot -Requested $QmdLiveServiceRuntimeRoot
$qmdLiveInstanceId = [Guid]::NewGuid().ToString("N")
$qmdLiveInstanceRoot = Join-Path (Join-Path $resolvedQmdLiveServiceRuntimeRoot "instances") $qmdLiveInstanceId
$terminalWindowTarget = Resolve-WindowsTerminalTarget `
    -Mode $TerminalTarget `
    -FallbackWindowName $TerminalWindowName `
    -CallerWindowHandle $callerTerminalWindow
if ($terminalWindowTarget.Reason) {
    Write-Host $terminalWindowTarget.Reason
}
$pythonDirectory = Split-Path -Parent $resolvedPython
$cargoCommand = Get-Command cargo -ErrorAction SilentlyContinue
$toolDirectories = @($pythonDirectory)
if ($cargoCommand) {
    $toolDirectories += Split-Path -Parent $cargoCommand.Source
}
$toolDirectories = @($toolDirectories | Select-Object -Unique)
$pathTerms = @($toolDirectories | ForEach-Object { ConvertTo-PowerShellLiteral -Value $_ })
$pathTerms += '$env:PATH'
$pathAssignment = '$env:PYTHONDONTWRITEBYTECODE = ''1''' + [Environment]::NewLine +
    '$env:PATH = ' + ($pathTerms -join ' + [IO.Path]::PathSeparator + ') + [Environment]::NewLine
$qmdLiveBind = "$HostName`:$QmdLivePort"
$qmdLiveCommand = $pathAssignment +
    "& " + (ConvertTo-PowerShellLiteral -Value $qmdLiveLauncher) +
    " -Bind " + (ConvertTo-PowerShellLiteral -Value $qmdLiveBind) +
    " -PythonExe " + (ConvertTo-PowerShellLiteral -Value $resolvedPython) +
    " -TerminalNoScreen"

function Open-ServiceTabs {
    param([object[]]$Tabs)

    if (-not $PSCmdlet.ShouldProcess(
        $terminalWindowTarget.Description,
        "Open $($Tabs.Count) independent PowerShell service tabs"
    )) {
        return $terminalWindowTarget
    }

    Assert-ServicePortsAvailable -Tabs $Tabs

    $dispatchTarget = Confirm-WindowsTerminalTarget `
        -Target $terminalWindowTarget `
        -RequestedMode $TerminalTarget `
        -FallbackWindowName $TerminalWindowName `
        -PythonExecutable $resolvedPython
    $terminalArguments = @("-w", $dispatchTarget.Window)
    for ($index = 0; $index -lt $Tabs.Count; $index++) {
        if ($index -gt 0) {
            $terminalArguments += ";"
        }
        $terminalArguments += @(
            "new-tab",
            "--title", $Tabs[$index].Title,
            "--suppressApplicationTitle",
            "-d", $repoRoot,
            $powerShellExe,
            "-NoLogo",
            "-NoProfile",
            "-ExecutionPolicy", "Bypass",
            "-File", $serviceTabHost,
            "-EncodedCommand", (ConvertTo-PowerShellEncodedCommand -Command $Tabs[$index].Command),
            "-PowerShellExe", $powerShellExe,
            "-RegistryPath", (Join-Path $qmdLiveInstanceRoot "$($Tabs[$index].Role).json"),
            "-ServiceRole", $Tabs[$index].Role,
            "-ServicePort", $Tabs[$index].Port,
            "-InstanceId", $qmdLiveInstanceId,
            "-RepositoryRoot", $repoRoot
        )
    }

    & $resolvedWindowsTerminal @terminalArguments
    if ($LASTEXITCODE -ne 0) {
        throw "Windows Terminal failed to create the service tabs (exit code $LASTEXITCODE)."
    }
    return $dispatchTarget
}

$serviceTabs = @(
    [pscustomobject]@{
        Title = "QMD Live"
        Role = "qmd_live"
        Port = $QmdLivePort
        Command = $qmdLiveCommand
    }
)

foreach ($serviceTab in $serviceTabs) {
    $commandTokens = $null
    $commandErrors = $null
    [void][Management.Automation.Language.Parser]::ParseInput(
        $serviceTab.Command,
        [ref]$commandTokens,
        [ref]$commandErrors
    )
    if ($commandErrors.Count -gt 0) {
        $messages = @($commandErrors | ForEach-Object { $_.Message }) -join "; "
        throw "The generated '$($serviceTab.Title)' PowerShell command is invalid: $messages"
    }
}

$usedTerminalWindowTarget = Open-ServiceTabs -Tabs $serviceTabs

if ($WhatIfPreference) {
    Write-Host ""
    Write-Host "Validation complete; no Windows Terminal tabs were opened."
    return
}

Write-Host ""
Write-Host "Opened an independent QMD Live PowerShell tab in $($usedTerminalWindowTarget.Description)."
Write-Host "This starter exits after handing the service to its registered tab host."
Write-Host "A successful graceful stop exits the tab host cleanly so Windows Terminal closes the service tab."
Write-Host "QMD Live:   http://$HostName`:$QmdLivePort"
Write-Host "Ownership:  $qmdLiveInstanceRoot"
Write-Host "Stop only launcher-owned QMD Live instances with scripts\stop_qmd_live_gateway.ps1."
