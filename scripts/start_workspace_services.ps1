[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$HostName = "127.0.0.1",
    [ValidateRange(1, 65535)]
    [int]$QmdHistoryPort = 8801,
    [ValidateRange(1, 65535)]
    [int]$BackendPort = 8000,
    [ValidateRange(1, 65535)]
    [int]$FrontendPort = 5173,
    [ValidateRange(1, 65535)]
    [int]$BarGptPort = 8805,
    [string]$BarGptV2Checkpoint = "",
    [string]$BarGptV3Checkpoint = "",
    [string]$PythonExe = "",
    [string]$WindowsTerminalExe = "",
    [ValidateSet("Auto", "Caller", "Named")]
    [string]$TerminalTarget = "Auto",
    [string]$TerminalWindowName = "quant-research-workbench-workspace",
    [string]$WorkspaceRuntimeRoot = "",
    [ValidateRange(0.01, 100.0)]
    [double]$MaxGitDirectoryGB = 2.0,
    [switch]$NoBackendReload,
    [switch]$WithBarGpt
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$env:PYTHONDONTWRITEBYTECODE = "1"

$repoRoot = Split-Path -Parent $PSScriptRoot
$qmdHistoryLauncher = Join-Path $PSScriptRoot "run_qmd_history_gateway.ps1"
$backendLauncher = Join-Path $PSScriptRoot "run_backend.ps1"
$frontendLauncher = Join-Path $PSScriptRoot "run_frontend.py"
$barGptLauncher = Join-Path $PSScriptRoot "run_bar_gpt.ps1"
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

function Resolve-WorkspaceRuntimeRoot {
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
    $resolved = [IO.Path]::GetFullPath($candidate)
    $resolvedRepo = [IO.Path]::GetFullPath($repoRoot).TrimEnd('\')
    if ($resolved.TrimEnd('\').Equals($resolvedRepo, [StringComparison]::OrdinalIgnoreCase) -or
        $resolved.StartsWith($resolvedRepo + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "Workspace service runtime state must be outside the repository: $resolved"
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
            "Workspace startup refuses to adopt existing port owners because they were not created by this launcher. " +
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
            "Workspace startup stopped because .git is {0:N2} GiB, above the {1:N2} GiB safety limit. " -f
            $sizeGB, $MaximumGB
        ) + (
            "An oversized Git database makes Codex status and diff snapshots slow even though services are separate. " +
            "Audit with scripts\maintain_repository_git.ps1 and compact with " +
            "scripts\maintain_repository_git.ps1 -Compact before starting services."
        )
    }
}

Assert-Launcher -Path $qmdHistoryLauncher
Assert-Launcher -Path $backendLauncher
Assert-Launcher -Path $frontendLauncher
Assert-Launcher -Path $serviceTabHost
if ($WithBarGpt) { Assert-Launcher -Path $barGptLauncher }
Assert-RepositoryGitSize -MaximumGB $MaxGitDirectoryGB

$resolvedPython = Resolve-PythonExecutable -Requested $PythonExe
$callerTerminalWindow = Get-WindowsTerminalCallerWindow `
    -PythonExecutable $resolvedPython
$powerShellExe = (Get-Command powershell.exe -ErrorAction Stop).Source
$resolvedWindowsTerminal = Resolve-WindowsTerminalExecutable -Requested $WindowsTerminalExe
$resolvedWorkspaceRuntimeRoot = Resolve-WorkspaceRuntimeRoot -Requested $WorkspaceRuntimeRoot
$workspaceInstanceId = [Guid]::NewGuid().ToString("N")
$workspaceInstanceRoot = Join-Path (Join-Path $resolvedWorkspaceRuntimeRoot "instances") $workspaceInstanceId
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
$qmdHistoryBind = "$HostName`:$QmdHistoryPort"
$qmdHistoryCommand = $pathAssignment +
    '$env:QMD_HISTORY_BIND = ' + (ConvertTo-PowerShellLiteral -Value $qmdHistoryBind) + [Environment]::NewLine +
    "& " + (ConvertTo-PowerShellLiteral -Value $qmdHistoryLauncher)
$backendCommand = $pathAssignment +
    "& " + (ConvertTo-PowerShellLiteral -Value $backendLauncher) +
    " -HostName " + (ConvertTo-PowerShellLiteral -Value $HostName) +
    " -Port $BackendPort" +
    " -PythonExe " + (ConvertTo-PowerShellLiteral -Value $resolvedPython)
if ($NoBackendReload) {
    $backendCommand += " -NoReload"
}
$frontendCommand = $pathAssignment +
    "& " + (ConvertTo-PowerShellLiteral -Value $resolvedPython) +
    " " + (ConvertTo-PowerShellLiteral -Value $frontendLauncher) +
    " dev -- --host " + (ConvertTo-PowerShellLiteral -Value $HostName) +
    " --port $FrontendPort"
$barGptCommand = $pathAssignment +
    '$env:BAR_GPT_BIND = ' + (ConvertTo-PowerShellLiteral -Value "$HostName`:$BarGptPort") + [Environment]::NewLine
if ($BarGptV2Checkpoint.Trim()) {
    $barGptCommand += '$env:BAR_GPT_V2_CHECKPOINT = ' + (ConvertTo-PowerShellLiteral -Value $BarGptV2Checkpoint.Trim()) + [Environment]::NewLine
}
if ($BarGptV3Checkpoint.Trim()) {
    $barGptCommand += '$env:BAR_GPT_V3_CHECKPOINT = ' + (ConvertTo-PowerShellLiteral -Value $BarGptV3Checkpoint.Trim()) + [Environment]::NewLine
}
$barGptCommand += "& " + (ConvertTo-PowerShellLiteral -Value $barGptLauncher) +
    " -Bind " + (ConvertTo-PowerShellLiteral -Value "$HostName`:$BarGptPort") +
    " -PythonExe " + (ConvertTo-PowerShellLiteral -Value $resolvedPython)

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
            "-RegistryPath", (Join-Path $workspaceInstanceRoot "$($Tabs[$index].Role).json"),
            "-ServiceRole", $Tabs[$index].Role,
            "-ServicePort", $Tabs[$index].Port,
            "-InstanceId", $workspaceInstanceId,
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
        Title = "QMD History"
        Role = "qmd_history"
        Port = $QmdHistoryPort
        Command = $qmdHistoryCommand
    },
    [pscustomobject]@{
        Title = "Backend"
        Role = "backend"
        Port = $BackendPort
        Command = $backendCommand
    },
    [pscustomobject]@{
        Title = "Frontend"
        Role = "frontend"
        Port = $FrontendPort
        Command = $frontendCommand
    }
)
if ($WithBarGpt) {
    if (-not $BarGptV2Checkpoint.Trim() -and -not $BarGptV3Checkpoint.Trim() -and -not $env:BAR_GPT_RELEASES_JSON) {
        throw "-WithBarGpt requires a v2/v3 checkpoint or BAR_GPT_RELEASES_JSON."
    }
    $serviceTabs += [pscustomobject]@{
        Title = "BarGPT"
        Role = "bar_gpt"
        Port = $BarGptPort
        Command = $barGptCommand
    }
}

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
Write-Host "Opened $($serviceTabs.Count) independent workspace service tabs in $($usedTerminalWindowTarget.Description)."
Write-Host "This starter now exits instead of supervising the launcher processes."
Write-Host "A successful graceful stop exits each tab host cleanly so Windows Terminal closes the service tabs."
Write-Host "QMD History: http://$HostName`:$QmdHistoryPort"
Write-Host "Backend:    http://$HostName`:$BackendPort"
Write-Host "Frontend:   http://$HostName`:$FrontendPort"
if ($WithBarGpt) { Write-Host "BarGPT:     http://$HostName`:$BarGptPort" }
Write-Host "Ownership:  $workspaceInstanceRoot"
Write-Host "Stop only launcher-owned instances with scripts\stop_workspace_services.ps1."
Write-Host "QMD Live has an independent lifecycle: scripts\start_qmd_live_gateway.ps1 and scripts\stop_qmd_live_gateway.ps1."
