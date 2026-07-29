[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$HostName = "127.0.0.1",
    [ValidateRange(1, 65535)]
    [int]$BackendPort = 8000,
    [ValidateRange(1, 65535)]
    [int]$FrontendPort = 5173,
    [string]$PythonExe = "",
    [string]$WindowsTerminalExe = "",
    [ValidateSet("Auto", "Caller", "Named")]
    [string]$TerminalTarget = "Auto",
    [string]$TerminalWindowName = "quant-research-workbench-workspace",
    [switch]$NoBackendReload
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent $PSScriptRoot
$qmdLauncher = Join-Path $PSScriptRoot "run_qmd_history_gateway.ps1"
$backendLauncher = Join-Path $PSScriptRoot "run_backend.ps1"
$frontendLauncher = Join-Path $PSScriptRoot "run_frontend.py"
$serviceTabHost = Join-Path $PSScriptRoot "run_windows_terminal_service_tab.ps1"

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

    $userCandidates = @(
        (Join-Path $env:USERPROFILE "miniconda3\python.exe"),
        (Join-Path $env:USERPROFILE "anaconda3\python.exe")
    )
    foreach ($candidate in $userCandidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return $candidate
        }
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

function Resolve-TerminalWindowTarget {
    param(
        [string]$Mode,
        [string]$FallbackWindowName
    )

    $insideWindowsTerminal = -not [string]::IsNullOrWhiteSpace($env:WT_SESSION)
    if ($Mode -eq "Caller") {
        if (-not $insideWindowsTerminal) {
            throw "-TerminalTarget Caller requires this script to run inside Windows Terminal (WT_SESSION is not set)."
        }
        return [pscustomobject]@{
            Window = "0"
            Description = "the invoking Windows Terminal window"
        }
    }
    if ($Mode -eq "Auto" -and $insideWindowsTerminal) {
        return [pscustomobject]@{
            Window = "0"
            Description = "the invoking Windows Terminal window"
        }
    }
    if (-not $FallbackWindowName.Trim()) {
        throw "-TerminalWindowName cannot be empty when a named Windows Terminal window is used."
    }
    return [pscustomobject]@{
        Window = $FallbackWindowName.Trim()
        Description = "named Windows Terminal window '$($FallbackWindowName.Trim())'"
    }
}

Assert-Launcher -Path $qmdLauncher
Assert-Launcher -Path $backendLauncher
Assert-Launcher -Path $frontendLauncher
Assert-Launcher -Path $serviceTabHost

$resolvedPython = Resolve-PythonExecutable -Requested $PythonExe
$powerShellExe = (Get-Command powershell.exe -ErrorAction Stop).Source
$resolvedWindowsTerminal = Resolve-WindowsTerminalExecutable -Requested $WindowsTerminalExe
$terminalWindowTarget = Resolve-TerminalWindowTarget `
    -Mode $TerminalTarget `
    -FallbackWindowName $TerminalWindowName
$pythonDirectory = Split-Path -Parent $resolvedPython
$cargoCommand = Get-Command cargo -ErrorAction SilentlyContinue
$toolDirectories = @($pythonDirectory)
if ($cargoCommand) {
    $toolDirectories += Split-Path -Parent $cargoCommand.Source
}
$toolDirectories = @($toolDirectories | Select-Object -Unique)
$pathTerms = @($toolDirectories | ForEach-Object { ConvertTo-PowerShellLiteral -Value $_ })
$pathTerms += '$env:PATH'
$pathAssignment = '$env:PATH = ' + ($pathTerms -join ' + [IO.Path]::PathSeparator + ') + [Environment]::NewLine
$qmdCommand = $pathAssignment + "& " + (ConvertTo-PowerShellLiteral -Value $qmdLauncher)
$backendCommand = $pathAssignment +
    "& " + (ConvertTo-PowerShellLiteral -Value $backendLauncher) +
    " -HostName " + (ConvertTo-PowerShellLiteral -Value $HostName) +
    " -Port $BackendPort"
if ($NoBackendReload) {
    $backendCommand += " -NoReload"
}
$frontendCommand = $pathAssignment +
    "& " + (ConvertTo-PowerShellLiteral -Value $resolvedPython) +
    " " + (ConvertTo-PowerShellLiteral -Value $frontendLauncher) +
    " dev -- --host " + (ConvertTo-PowerShellLiteral -Value $HostName) +
    " --port $FrontendPort"

function Open-ServiceTabs {
    param([object[]]$Tabs)

    if (-not $PSCmdlet.ShouldProcess(
        $terminalWindowTarget.Description,
        "Open $($Tabs.Count) independent PowerShell service tabs"
    )) {
        return
    }

    $terminalArguments = @("-w", $terminalWindowTarget.Window)
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
            "-PowerShellExe", $powerShellExe
        )
    }

    & $resolvedWindowsTerminal @terminalArguments
    if ($LASTEXITCODE -ne 0) {
        throw "Windows Terminal failed to create the service tabs (exit code $LASTEXITCODE)."
    }
}

$serviceTabs = @(
    [pscustomobject]@{
        Title = "QMD History"
        Command = $qmdCommand
    },
    [pscustomobject]@{
        Title = "Backend"
        Command = $backendCommand
    },
    [pscustomobject]@{
        Title = "Frontend"
        Command = $frontendCommand
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

Open-ServiceTabs -Tabs $serviceTabs

if ($WhatIfPreference) {
    Write-Host ""
    Write-Host "Validation complete; no Windows Terminal tabs were opened."
    return
}

Write-Host ""
Write-Host "Opened independent QMD History, Backend, and Frontend PowerShell tabs in $($terminalWindowTarget.Description)."
Write-Host "This starter now exits instead of supervising the three launcher processes."
Write-Host "A successful graceful stop exits each tab host cleanly so Windows Terminal closes the service tabs."
Write-Host "QMD History: its launcher resolves QMD_HISTORY_BIND (default http://127.0.0.1:8801)."
Write-Host "Backend:    http://$HostName`:$BackendPort"
Write-Host "Frontend:   http://$HostName`:$FrontendPort"
Write-Host "Stop all matching instances with scripts\stop_workspace_services.ps1."
