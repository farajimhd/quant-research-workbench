[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$HostName = "127.0.0.1",
    [ValidateRange(1, 65535)]
    [int]$BackendPort = 8000,
    [ValidateRange(1, 65535)]
    [int]$FrontendPort = 5173,
    [string]$PythonExe = "",
    [string]$WindowsTerminalExe = "",
    [string]$TerminalWindowName = "quant-research-workbench-services",
    [switch]$NoBackendReload
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent $PSScriptRoot
$qmdLauncher = Join-Path $PSScriptRoot "run_qmd_history_gateway.ps1"
$backendLauncher = Join-Path $PSScriptRoot "run_backend.ps1"
$frontendLauncher = Join-Path $PSScriptRoot "run_frontend.py"

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

Assert-Launcher -Path $qmdLauncher
Assert-Launcher -Path $backendLauncher
Assert-Launcher -Path $frontendLauncher

$resolvedPython = Resolve-PythonExecutable -Requested $PythonExe
$powerShellExe = (Get-Command powershell.exe -ErrorAction Stop).Source
$resolvedWindowsTerminal = Resolve-WindowsTerminalExecutable -Requested $WindowsTerminalExe
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

function Open-ServiceTab {
    param(
        [string]$Title,
        [string]$Command
    )

    if (-not $PSCmdlet.ShouldProcess(
        "$TerminalWindowName / $Title",
        "Open an independent PowerShell tab in Windows Terminal"
    )) {
        return
    }

    & $resolvedWindowsTerminal `
        -w $TerminalWindowName `
        new-tab `
        --title $Title `
        -d $repoRoot `
        $powerShellExe `
        -NoLogo `
        -NoProfile `
        -ExecutionPolicy Bypass `
        -Command $Command
    if ($LASTEXITCODE -ne 0) {
        throw "Windows Terminal failed to create the '$Title' tab (exit code $LASTEXITCODE)."
    }
}

Open-ServiceTab -Title "QMD History" -Command $qmdCommand
Open-ServiceTab -Title "Backend" -Command $backendCommand
Open-ServiceTab -Title "Frontend" -Command $frontendCommand

if ($WhatIfPreference) {
    Write-Host ""
    Write-Host "Validation complete; no Windows Terminal tabs were opened."
    return
}

Write-Host ""
Write-Host "Opened independent QMD History, Backend, and Frontend PowerShell tabs in Windows Terminal window '$TerminalWindowName'."
Write-Host "This starter now exits instead of supervising the three launcher processes."
Write-Host "QMD History: its launcher resolves QMD_HISTORY_BIND (default http://127.0.0.1:8801)."
Write-Host "Backend:    http://$HostName`:$BackendPort"
Write-Host "Frontend:   http://$HostName`:$FrontendPort"
Write-Host "Stop all matching instances with scripts\stop_workspace_services.ps1."
